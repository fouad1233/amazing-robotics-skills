#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Desktop pets — the agents walk around on your desktop while they work.

One transparent, click-through, always-on-top window per agent. They wander
along the bottom of the screen, show what they are doing, and pop a speech
bubble when their agent says something. With ``--interactive`` a small console
appears that you can type a prompt into, so the pets are a way *in* to the
bench as well as a view of it.

**This module runs under the SYSTEM interpreter, not the package venv**, because
GTK lives in ``/usr/lib/python3/dist-packages``. It therefore imports nothing
from ``roboagents`` — it speaks to the bench over the same WebSocket the browser
world uses, with a WebSocket client written against the stdlib, and falls back
to tailing a JSONL transcript when no server is listening.

    /usr/bin/python3 pets.py --url ws://127.0.0.1:8770/ws --interactive
    /usr/bin/python3 pets.py --jsonl ~/.cache/roboagents/runs/<id>.jsonl

Requires X11 with a compositing manager for real transparency. On a display
without an RGBA visual it still runs, but the window background will be opaque.
"""

from __future__ import annotations

import argparse
import base64
import json
import math
import os
import random
import socket
import struct
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterator
from typing import Any

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")

import cairo
from gi.repository import Gdk, GLib, Gtk

#: Animation tick. 20 fps is smooth enough for a walking sprite and cheap.
_TICK_MS = 50

#: How long a speech bubble stays up.
_BUBBLE_SECONDS = 6.0

_PET_W, _PET_H = 150, 118

_PALETTE = (
    (0.35, 0.78, 0.98),
    (0.95, 0.55, 0.85),
    (0.55, 0.90, 0.55),
    (0.98, 0.82, 0.35),
    (0.60, 0.65, 0.98),
    (0.45, 0.92, 0.85),
    (0.98, 0.65, 0.45),
    (0.80, 0.75, 0.98),
)


def colour_for(name: str) -> tuple[float, float, float]:
    """Stable colour per agent, matching the other views' hashing idea."""
    return _PALETTE[sum(name.encode()) % len(_PALETTE)]


# --------------------------------------------------------------------------
# A very small WebSocket client (RFC 6455), stdlib only
# --------------------------------------------------------------------------


class WebSocketError(RuntimeError):
    pass


class WebSocket:
    """Text-frame client. Enough of RFC 6455 to consume an event feed."""

    def __init__(self, url: str, timeout: float = 10.0) -> None:
        parts = urllib.parse.urlparse(url)
        if parts.scheme not in ("ws", "wss"):
            raise WebSocketError(f"unsupported scheme {parts.scheme!r}")
        if parts.scheme == "wss":
            raise WebSocketError("wss is not supported; the bench is local, use ws://")

        host = parts.hostname or "127.0.0.1"
        port = parts.port or 80
        path = parts.path or "/"
        if parts.query:
            path += "?" + parts.query

        self._sock = socket.create_connection((host, port), timeout=timeout)
        self._buffer = b""
        key = base64.b64encode(os.urandom(16)).decode()
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "\r\n"
        )
        self._sock.sendall(request.encode())

        header = b""
        while b"\r\n\r\n" not in header:
            chunk = self._sock.recv(4096)
            if not chunk:
                raise WebSocketError("server closed during handshake")
            header += chunk
        status = header.split(b"\r\n", 1)[0]
        if b"101" not in status:
            raise WebSocketError(f"handshake refused: {status.decode(errors='replace')}")
        self._buffer = header.split(b"\r\n\r\n", 1)[1]
        self._sock.settimeout(None)

    def _recv_exact(self, count: int) -> bytes:
        while len(self._buffer) < count:
            chunk = self._sock.recv(65536)
            if not chunk:
                raise WebSocketError("connection closed")
            self._buffer += chunk
        out, self._buffer = self._buffer[:count], self._buffer[count:]
        return out

    def messages(self) -> Iterator[str]:
        """Yield each text frame. Control frames are handled transparently."""
        while True:
            first, second = self._recv_exact(2)
            opcode = first & 0x0F
            masked = bool(second & 0x80)
            length = second & 0x7F
            if length == 126:
                length = struct.unpack(">H", self._recv_exact(2))[0]
            elif length == 127:
                length = struct.unpack(">Q", self._recv_exact(8))[0]
            mask = self._recv_exact(4) if masked else b""
            payload = self._recv_exact(length) if length else b""
            if masked:
                payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))

            if opcode == 0x8:  # close
                return
            if opcode == 0x9:  # ping -> pong
                self._send(0xA, payload)
                continue
            if opcode in (0x1, 0x0) and payload:
                yield payload.decode("utf-8", errors="replace")

    def _send(self, opcode: int, payload: bytes) -> None:
        # Client frames must be masked.
        mask = os.urandom(4)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        header = bytes([0x80 | opcode])
        length = len(payload)
        if length < 126:
            header += bytes([0x80 | length])
        elif length < 1 << 16:
            header += bytes([0x80 | 126]) + struct.pack(">H", length)
        else:
            header += bytes([0x80 | 127]) + struct.pack(">Q", length)
        self._sock.sendall(header + mask + masked)

    def send_text(self, text: str) -> None:
        self._send(0x1, text.encode())

    def close(self) -> None:
        try:
            self._send(0x8, b"")
        except OSError:
            pass
        try:
            self._sock.close()
        except OSError:
            pass


# --------------------------------------------------------------------------
# Event sources
# --------------------------------------------------------------------------


def websocket_source(url: str, on_event: Callable[[dict], None], stop: threading.Event) -> None:
    """Read the feed forever, reconnecting when the server goes away."""
    delay = 1.0
    while not stop.is_set():
        try:
            sock = WebSocket(url)
            delay = 1.0
            for raw in sock.messages():
                if stop.is_set():
                    break
                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                # The server may send a history array on connect.
                if isinstance(payload, list):
                    for item in payload:
                        if isinstance(item, dict):
                            on_event(item)
                elif isinstance(payload, dict):
                    on_event(payload.get("event", payload))
            sock.close()
        except (WebSocketError, OSError):
            pass
        if stop.wait(delay):
            return
        delay = min(delay * 2, 15.0)


def jsonl_source(path: str, on_event: Callable[[dict], None], stop: threading.Event) -> None:
    """Tail a transcript. Used when there is no server to talk to."""
    while not stop.is_set() and not os.path.exists(path):
        if stop.wait(0.5):
            return
    with open(path, errors="replace") as handle:
        while not stop.is_set():
            line = handle.readline()
            if not line:
                if stop.wait(0.25):
                    return
                continue
            line = line.strip()
            if not line:
                continue
            try:
                on_event(json.loads(line))
            except json.JSONDecodeError:
                continue


# --------------------------------------------------------------------------
# The pet window
# --------------------------------------------------------------------------


class Pet(Gtk.Window):
    """One agent, walking on the desktop."""

    def __init__(self, name: str, domain: str = "", scale: float = 1.0) -> None:
        super().__init__(type=Gtk.WindowType.POPUP)
        self.name = name
        self.domain = domain
        self.scale = scale
        self.colour = colour_for(name)

        self.state = "idle"
        self.detail = ""
        self.bubble = ""
        self.bubble_until = 0.0
        self.phase = random.random() * math.tau
        self.facing = 1.0
        self.target_x: float | None = None

        self.set_decorated(False)
        self.set_app_paintable(True)
        self.set_keep_above(True)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_accept_focus(False)
        self.set_focus_on_map(False)
        self.stick()  # visible on every workspace
        self.set_default_size(int(_PET_W * scale), int(_PET_H * scale))

        screen = self.get_screen()
        visual = screen.get_rgba_visual()
        self.transparent = visual is not None
        if self.transparent:
            self.set_visual(visual)

        self.connect("draw", self._on_draw)
        self.connect("realize", self._on_realize)

        geometry = _screen_geometry()
        self.x = random.uniform(60, max(120.0, geometry[0] - 200))
        self.y = geometry[1] - int(_PET_H * scale) - 70
        self.move(int(self.x), int(self.y))

    def _on_realize(self, _widget: Gtk.Widget) -> None:
        """Make the window click-through: an empty input region."""
        window = self.get_window()
        if window is not None:
            window.input_shape_combine_region(cairo.Region(), 0, 0)

    # -- state from events ------------------------------------------------

    def set_state(self, state: str, detail: str = "") -> None:
        self.state = state or self.state
        if detail:
            self.detail = detail[:60]

    def say(self, text: str) -> None:
        self.bubble = text[:160]
        self.bubble_until = time.monotonic() + _BUBBLE_SECONDS

    # -- animation --------------------------------------------------------

    def tick(self, dt: float) -> None:
        self.phase += dt * (6.0 if self.state == "working" else 2.5)

        width, height = _screen_geometry()
        speed = {"working": 55.0, "thinking": 12.0, "reviewing": 40.0}.get(self.state, 22.0)

        if self.state in ("done", "failed"):
            speed = 0.0
        if self.target_x is None or abs(self.target_x - self.x) < 8:
            self.target_x = random.uniform(40, max(80.0, width - _PET_W * self.scale - 40))

        if speed:
            direction = 1.0 if self.target_x > self.x else -1.0
            self.facing = direction
            self.x += direction * speed * dt
            self.x = max(0.0, min(self.x, width - _PET_W * self.scale))

        self.y = height - int(_PET_H * self.scale) - 70
        self.move(int(self.x), int(self.y))
        self.queue_draw()

    # -- drawing ----------------------------------------------------------

    def _on_draw(self, _widget: Gtk.Widget, cr: cairo.Context) -> None:
        scale = self.scale
        cr.save()
        # This is what makes the background actually invisible instead of black.
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.set_source_rgba(0, 0, 0, 0)
        cr.paint()
        cr.restore()
        cr.scale(scale, scale)

        bob = math.sin(self.phase) * (3.0 if self.state != "idle" else 1.2)
        base_y = 74 + bob
        red, green, blue = self.colour
        if self.state == "failed":
            red, green, blue = 0.95, 0.35, 0.35

        # body
        cr.set_source_rgba(red, green, blue, 0.95)
        _rounded(cr, 52, base_y - 22, 26, 26, 6)
        cr.fill()
        # head
        _rounded(cr, 55, base_y - 42, 20, 18, 5)
        cr.fill()
        # eyes, looking the way it walks
        cr.set_source_rgba(0.06, 0.07, 0.10, 0.95)
        eye = 60 + (3 if self.facing > 0 else -3)
        cr.rectangle(eye, base_y - 36, 3, 4)
        cr.rectangle(eye + 7, base_y - 36, 3, 4)
        cr.fill()
        # legs, alternating
        cr.set_source_rgba(red * 0.7, green * 0.7, blue * 0.7, 0.95)
        swing = math.sin(self.phase) * 4
        cr.rectangle(56, base_y + 4, 5, 8 + swing)
        cr.rectangle(68, base_y + 4, 5, 8 - swing)
        cr.fill()

        self._draw_status(cr, base_y)
        self._draw_label(cr)
        self._draw_bubble(cr)

    def _draw_status(self, cr: cairo.Context, base_y: float) -> None:
        glyph = {
            "thinking": "...",
            "working": "><",
            "reviewing": "??",
            "done": "OK",
            "failed": "!!",
        }.get(self.state, "")
        if not glyph:
            return
        cr.select_font_face("monospace", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
        cr.set_font_size(11)
        colour = {
            "done": (0.45, 0.95, 0.55),
            "failed": (0.98, 0.42, 0.42),
        }.get(self.state, (1.0, 1.0, 1.0))
        cr.set_source_rgba(*colour, 0.92)
        cr.move_to(80, base_y - 30 + math.sin(self.phase * 1.6) * 2)
        cr.show_text(glyph)

    def _draw_label(self, cr: cairo.Context) -> None:
        cr.select_font_face("sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
        cr.set_font_size(10)
        label = self.name.replace("Agent", "")
        extents = cr.text_extents(label)
        x = 65 - extents.width / 2
        cr.set_source_rgba(0, 0, 0, 0.55)
        _rounded(cr, x - 5, 88, extents.width + 10, 15, 5)
        cr.fill()
        cr.set_source_rgba(1, 1, 1, 0.95)
        cr.move_to(x, 99)
        cr.show_text(label)

    def _draw_bubble(self, cr: cairo.Context) -> None:
        if not self.bubble or time.monotonic() > self.bubble_until:
            return
        cr.select_font_face("sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
        cr.set_font_size(9)
        lines = _wrap(cr, self.bubble, 132)[:3]
        height = 8 + len(lines) * 11
        width = max(cr.text_extents(line).width for line in lines) + 14

        top = 26 - height
        cr.set_source_rgba(0.10, 0.11, 0.14, 0.90)
        _rounded(cr, 6, top, width, height, 6)
        cr.fill()
        cr.set_source_rgba(1, 1, 1, 0.94)
        for index, line in enumerate(lines):
            cr.move_to(13, top + 14 + index * 11)
            cr.show_text(line)


def _rounded(cr: cairo.Context, x: float, y: float, w: float, h: float, r: float) -> None:
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 1.5 * math.pi)
    cr.close_path()


def _wrap(cr: cairo.Context, text: str, width: float) -> list[str]:
    words, lines, current = text.split(), [], ""
    for word in words:
        trial = f"{current} {word}".strip()
        if cr.text_extents(trial).width > width and current:
            lines.append(current)
            current = word
        else:
            current = trial
    if current:
        lines.append(current)
    return lines or [text]


def _screen_geometry() -> tuple[int, int]:
    display = Gdk.Display.get_default()
    if display is None:  # pragma: no cover - headless
        return (1920, 1080)
    monitor = display.get_primary_monitor() or display.get_monitor(0)
    if monitor is None:  # pragma: no cover
        return (1920, 1080)
    rect = monitor.get_geometry()
    return (rect.width, rect.height)


# --------------------------------------------------------------------------
# The console — how you talk to the bench from the desktop
# --------------------------------------------------------------------------


class Console(Gtk.Window):
    """A small always-on-top entry box that submits prompts to the bench.

    Deliberately NOT click-through: this is the one window you are meant to
    interact with. It posts to the web server's ``/api/prompt``, so it works
    whether the bench is in this machine's browser session or not.
    """

    def __init__(self, api_url: str) -> None:
        super().__init__(title="roboagents console")
        self.api_url = api_url
        self.set_keep_above(True)
        self.set_decorated(False)
        self.set_skip_taskbar_hint(True)
        self.set_default_size(520, 40)
        self.set_app_paintable(True)

        screen = self.get_screen()
        visual = screen.get_rgba_visual()
        if visual is not None:
            self.set_visual(visual)
        self.connect("draw", self._on_draw)

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        box.set_margin_start(10)
        box.set_margin_end(10)
        box.set_margin_top(6)
        box.set_margin_bottom(6)

        self.entry = Gtk.Entry()
        self.entry.set_placeholder_text("tell the bench what to do…")
        self.entry.connect("activate", self._on_submit)
        box.pack_start(self.entry, True, True, 0)

        self.status = Gtk.Label(label="")
        box.pack_start(self.status, False, False, 0)

        self.add(box)
        width, height = _screen_geometry()
        self.move(int(width / 2 - 260), height - 130)

    def _on_draw(self, _widget: Gtk.Widget, cr: cairo.Context) -> None:
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.set_source_rgba(0.08, 0.09, 0.12, 0.88)
        cr.paint()

    def _on_submit(self, _entry: Gtk.Entry) -> None:
        text = self.entry.get_text().strip()
        if not text:
            return
        self.entry.set_text("")
        self.status.set_text("sending…")
        # Off the GTK thread: an HTTP round trip must not freeze the pets.
        threading.Thread(target=self._post, args=(text,), daemon=True).start()

    def _post(self, prompt: str) -> None:
        body = json.dumps({"prompt": prompt, "source": "pets"}).encode()
        request = urllib.request.Request(
            self.api_url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                message = "queued" if response.status in (200, 202) else f"http {response.status}"
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:60]
            message = f"{exc.code}: {detail}"
        except (urllib.error.URLError, OSError) as exc:
            message = f"no bench: {exc}"
        GLib.idle_add(self.status.set_text, message)

    def note(self, text: str) -> None:
        self.status.set_text(text)


# --------------------------------------------------------------------------
# The application
# --------------------------------------------------------------------------


class Desktop:
    """Owns the pets and folds events into them."""

    def __init__(self, scale: float = 1.0, limit: int = 8, console: Console | None = None) -> None:
        self.pets: dict[str, Pet] = {}
        self.scale = scale
        self.limit = limit
        self.console = console
        self._last = time.monotonic()
        GLib.timeout_add(_TICK_MS, self._tick)

    def _tick(self) -> bool:
        now = time.monotonic()
        dt, self._last = now - self._last, now
        for pet in list(self.pets.values()):
            pet.tick(min(dt, 0.2))
        return True

    def handle(self, event: dict[str, Any]) -> bool:
        """Called on the GTK thread via idle_add. Must never raise."""
        try:
            self._handle(event)
        except Exception as exc:  # noqa: BLE001 - a bad event must not kill the UI
            print(f"pets: ignoring event: {exc}", file=sys.stderr)
        return False  # idle_add: run once

    def _handle(self, event: dict[str, Any]) -> None:
        kind = event.get("kind", "")
        actor = event.get("actor", "")
        text = event.get("text", "") or ""
        data = event.get("data") or {}

        if kind == "agent_spawned":
            self._spawn(actor, str(data.get("domain", "")))
        elif kind == "agent_retired":
            pet = self.pets.pop(actor, None)
            if pet is not None:
                pet.destroy()
        elif kind == "agent_state":
            pet = self.pets.get(actor) or self._spawn(actor)
            if pet:
                pet.set_state(str(data.get("state", "")), text)
        elif kind in ("message", "prompt"):
            if kind == "prompt":
                if self.console is not None:
                    self.console.note("sent")
                return
            pet = self.pets.get(actor) or self._spawn(actor)
            if pet:
                pet.say(text)
        elif kind == "thought":
            pet = self.pets.get(actor)
            if pet:
                pet.set_state("thinking", text)
        elif kind == "tool_result":
            pet = self.pets.get(actor)
            if pet:
                pet.set_state("working", text)
        elif kind == "error":
            pet = self.pets.get(actor)
            if pet:
                pet.set_state("failed", text)
                pet.say(text)
        elif kind == "bench_state" and self.console is not None:
            busy = bool(data.get("busy"))
            pending = int(data.get("pending", 0) or 0)
            self.console.note(f"busy ({pending} queued)" if busy else "ready")

    def _spawn(self, name: str, domain: str = "") -> Pet | None:
        if not name or name.startswith("human:") or name in ("system", "bench"):
            return None
        if name in self.pets:
            return self.pets[name]
        if len(self.pets) >= self.limit:
            return None
        pet = Pet(name, domain, self.scale)
        pet.show_all()
        self.pets[name] = pet
        return pet


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="roboagents pets", description=__doc__)
    parser.add_argument("--url", default="ws://127.0.0.1:8770/ws", help="event WebSocket")
    parser.add_argument("--api", default="", help="prompt endpoint (derived from --url)")
    parser.add_argument("--jsonl", default="", help="tail a transcript instead of a socket")
    parser.add_argument("--scale", type=float, default=1.0)
    parser.add_argument("--max", type=int, default=8, dest="limit")
    parser.add_argument("--interactive", action="store_true", help="show the prompt console")
    parser.add_argument("--seconds", type=float, default=0.0, help="quit after N seconds (testing)")
    args = parser.parse_args(argv)

    screen = Gdk.Screen.get_default()
    if screen is None:
        print("pets: no display; DISPLAY is not set or X is unreachable", file=sys.stderr)
        return 1
    if screen.get_rgba_visual() is None:
        print("pets: no RGBA visual — backgrounds will be opaque", file=sys.stderr)

    api = args.api
    if not api:
        parsed = urllib.parse.urlparse(args.url)
        api = f"http://{parsed.hostname or '127.0.0.1'}:{parsed.port or 80}/api/prompt"

    console = Console(api) if args.interactive else None
    if console is not None:
        console.show_all()

    desktop = Desktop(scale=args.scale, limit=args.limit, console=console)

    stop = threading.Event()
    on_event = lambda event: GLib.idle_add(desktop.handle, event)
    if args.jsonl:
        mode, target = "tailing", jsonl_source
        thread = threading.Thread(target=target, args=(args.jsonl, on_event, stop), daemon=True)
    else:
        mode, target = "websocket", websocket_source
        thread = threading.Thread(target=target, args=(args.url, on_event, stop), daemon=True)
    print(f"pets: {mode} {args.jsonl or args.url}", flush=True)
    thread.start()

    if args.seconds > 0:
        GLib.timeout_add(int(args.seconds * 1000), Gtk.main_quit)

    try:
        Gtk.main()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
