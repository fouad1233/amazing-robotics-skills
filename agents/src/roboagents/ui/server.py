# SPDX-License-Identifier: Apache-2.0
"""The browser game world: event stream out, prompts back in.

The server is deliberately thin. It owns no model of the world — it forwards
:class:`roboagents.events.Event` objects verbatim and lets the page decide how
to draw them. That is the whole point: if a sprite is working at a desk, an
agent really emitted ``agent_state``.

Two directions:

* **out** — one pump reads the source (the live :class:`EventBus`, or a
  recorded JSONL transcript via ``tail_jsonl``) and fans each event out to
  every connected browser as one WebSocket message, plus a replay buffer for
  ``GET /api/history`` so a tab opened halfway through a run catches up.
* **in** — ``POST /api/prompt`` (and the same payload as a WebSocket message)
  hands what the human typed to :mod:`roboagents.control`. With
  ``session=True`` a :class:`roboagents.control.Session` runs alongside the
  server and actually executes it; without one the endpoint says so with a 503
  instead of silently swallowing the prompt.

Each browser gets its own bounded outbound queue. A tab that has been paused in
a background window drops its oldest events rather than applying backpressure
through the pump and into the agents doing real work — the same trade the bus
itself makes.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections import deque
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from aiohttp import WSMsgType, web

from ..control import ControlBus, QueueFull, Session, control
from ..events import Event, EventBus, tail_jsonl
from ..events import bus as default_bus
from ..roster import specs

#: Events replayed to a late-joining browser. Matches the bus's own depth.
_HISTORY_MAX = 2000
#: Per-browser outbound backlog before the oldest event is dropped.
_PEER_QUEUE_MAX = 1024

#: Told to the user rather than to the log, because it is a setup mistake and
#: the only cure is a flag they have to type.
_NO_BENCH = "no bench attached — start with `roboagents web --session`"

INDEX = Path(__file__).resolve().parent / "web" / "index.html"


# --------------------------------------------------------------------------
# fan-out
# --------------------------------------------------------------------------


class _Peer:
    """One connected browser: a socket and a bounded outbound queue."""

    __slots__ = ("queue", "socket")

    def __init__(self, socket: web.WebSocketResponse) -> None:
        self.socket = socket
        self.queue: asyncio.Queue[str] = asyncio.Queue(maxsize=_PEER_QUEUE_MAX)

    def offer(self, payload: str) -> None:
        """Queue one message, evicting the oldest if this tab is not draining."""
        try:
            self.queue.put_nowait(payload)
        except asyncio.QueueFull:
            with contextlib.suppress(asyncio.QueueEmpty):
                self.queue.get_nowait()
            with contextlib.suppress(asyncio.QueueFull):
                self.queue.put_nowait(payload)


class _World:
    """The replay buffer and the set of browsers watching it."""

    def __init__(self, limit: int = _HISTORY_MAX) -> None:
        self._history: deque[str] = deque(maxlen=limit)
        self._peers: set[_Peer] = set()

    # -- history ---------------------------------------------------------

    def history_json(self) -> str:
        """The replay buffer as a JSON array, without re-encoding each event."""
        return "[" + ",".join(self._history) + "]"

    def __len__(self) -> int:
        return len(self._history)

    # -- membership ------------------------------------------------------

    def join(self, socket: web.WebSocketResponse) -> _Peer:
        """Register a browser, seeded with the backlog.

        Synchronous on purpose. There is no await between snapshotting the
        history and registering the peer, so on a single-threaded loop this
        browser can neither miss an event nor receive one twice.
        """
        peer = _Peer(socket)
        for payload in self._history:
            peer.offer(payload)
        self._peers.add(peer)
        return peer

    def leave(self, peer: _Peer) -> None:
        self._peers.discard(peer)

    # -- publishing ------------------------------------------------------

    def publish(self, payload: str) -> None:
        self._history.append(payload)
        for peer in list(self._peers):
            peer.offer(payload)

    async def shutdown(self) -> None:
        """Close every socket. Errors are expected — a peer may already be gone."""
        for peer in list(self._peers):
            self._peers.discard(peer)
            with contextlib.suppress(Exception):
                await peer.socket.close()


async def _pump(world: _World, events: AsyncIterator[Event]) -> None:
    """Forward one source into the world until cancelled."""
    async with contextlib.aclosing(events):
        async for event in events:
            world.publish(event.to_json())


# --------------------------------------------------------------------------
# prompts in
# --------------------------------------------------------------------------


def _submit(
    payload: Any,
    *,
    commands: ControlBus,
    enabled: bool,
) -> tuple[int, dict[str, Any]]:
    """Validate and queue one typed prompt. Returns (HTTP status, body).

    Shared by the POST handler and the WebSocket handler so both channels
    answer identically.
    """
    if not enabled:
        return 503, {"ok": False, "error": _NO_BENCH}
    if not isinstance(payload, dict):
        return 400, {"ok": False, "error": "body must be a JSON object"}

    prompt = payload.get("prompt", "")
    if not isinstance(prompt, str):
        return 400, {"ok": False, "error": "prompt must be a string"}

    raw_experts = payload.get("experts") or []
    if isinstance(raw_experts, str):
        raw_experts = [raw_experts]
    if not isinstance(raw_experts, list):
        return 400, {"ok": False, "error": "experts must be a list of expert names"}
    experts = tuple(str(name) for name in raw_experts if str(name).strip())

    try:
        command = commands.submit(prompt, source="web", experts=experts)
    except QueueFull as exc:
        return 429, {"ok": False, "error": str(exc)}
    except ValueError as exc:
        return 400, {"ok": False, "error": str(exc)}

    return 202, {
        "ok": True,
        "command_id": command.id,
        "prompt": command.prompt,
        "experts": list(command.experts),
        "pending": commands.pending,
    }


# --------------------------------------------------------------------------
# the server
# --------------------------------------------------------------------------


def build_app(
    world: _World,
    *,
    commands: ControlBus,
    session_enabled: bool,
) -> web.Application:
    """Wire the routes onto a world. Split out so tests can drive it directly."""

    async def index(_request: web.Request) -> web.StreamResponse:
        if not INDEX.is_file():
            # Missing asset is a packaging bug; say which file rather than 404.
            return web.Response(status=500, text=f"missing game world page: {INDEX}")
        return web.FileResponse(INDEX, headers={"Cache-Control": "no-cache"})

    async def roster(_request: web.Request) -> web.StreamResponse:
        return web.json_response(specs())

    async def history(_request: web.Request) -> web.StreamResponse:
        return web.Response(text=world.history_json(), content_type="application/json")

    async def prompt(request: web.Request) -> web.StreamResponse:
        try:
            payload = await request.json()
        except (ValueError, UnicodeDecodeError):
            return web.json_response({"ok": False, "error": "body must be JSON"}, status=400)
        status, body = _submit(payload, commands=commands, enabled=session_enabled)
        return web.json_response(body, status=status)

    async def status(_request: web.Request) -> web.StreamResponse:
        return web.json_response(
            {
                "session": session_enabled,
                "busy": commands.busy,
                "pending": commands.pending,
                "history": len(world),
            }
        )

    async def websocket(request: web.Request) -> web.StreamResponse:
        socket = web.WebSocketResponse(heartbeat=30.0)
        await socket.prepare(request)
        peer = world.join(socket)
        writer = asyncio.create_task(_write_forever(peer))
        try:
            async for message in socket:
                if message.type is not WSMsgType.TEXT:
                    continue
                _on_client_message(peer, message.data, commands, session_enabled)
        finally:
            world.leave(peer)
            writer.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await writer
            with contextlib.suppress(Exception):
                await socket.close()
        return socket

    app = web.Application()
    app.add_routes(
        [
            web.get("/", index),
            web.get("/index.html", index),
            web.get("/api/roster", roster),
            web.get("/api/history", history),
            web.get("/api/status", status),
            web.post("/api/prompt", prompt),
            web.get("/ws", websocket),
        ]
    )
    return app


async def _write_forever(peer: _Peer) -> None:
    """Drain one browser's queue. Returns on the first failed send.

    Sending is what discovers a disconnect mid-broadcast; the handler's finally
    block is what actually removes the peer, so the pump never sees the error.
    """
    while True:
        payload = await peer.queue.get()
        try:
            await peer.socket.send_str(payload)
        except (ConnectionResetError, RuntimeError, OSError):
            return


def _on_client_message(
    peer: _Peer,
    raw: str,
    commands: ControlBus,
    session_enabled: bool,
) -> None:
    """Handle one inbound WebSocket frame. Unknown message types are ignored."""
    try:
        message = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return
    if not isinstance(message, dict) or message.get("type") != "prompt":
        return

    status, body = _submit(message, commands=commands, enabled=session_enabled)
    # Acknowledgements are addressed to one browser and are not part of the
    # run's history, so they go straight into that peer's queue and carry a
    # kind the page knows not to draw in the world.
    peer.offer(json.dumps({"kind": "ack", "status": status, **body}))


async def serve(
    host: str = "127.0.0.1",
    port: int = 8770,
    source: Path | str | None = None,
    bus: EventBus | None = None,
    *,
    session: bool = False,
    orchestrator: Any = None,
    commands: ControlBus | None = None,
    path: Path | str | None = None,
) -> None:
    """Serve the game world until cancelled.

    ``source`` is a recorded JSONL transcript to replay and follow; leave it
    unset to watch the live bus. ``path`` is the same thing under the name the
    CLI passes. ``session=True`` attaches a bench so prompts typed in the
    browser actually run — pass ``orchestrator`` to supply your own (the tests
    use it to avoid spending an LLM call on transport plumbing).
    """
    source = source if source is not None else path
    stream = bus if bus is not None else default_bus()
    channel = commands or control()

    # Both buses hand work between threads by hopping onto a loop; this is the
    # one they should use.
    with contextlib.suppress(RuntimeError):
        stream.bind_loop()
    with contextlib.suppress(RuntimeError):
        channel.bind_loop()

    if source is not None:
        transcript = Path(source).expanduser()
        if not transcript.is_file():
            raise FileNotFoundError(f"no run transcript at {transcript}")
        events: AsyncIterator[Event] = tail_jsonl(transcript)
    else:
        events = stream.stream(replay=True)

    world = _World()
    app = build_app(world, commands=channel, session_enabled=session)

    tasks: list[asyncio.Task[None]] = [
        asyncio.create_task(_pump(world, events), name="roboagents-web-pump")
    ]
    if session:
        bench = Session(orchestrator, stream=stream, commands=channel)
        tasks.append(asyncio.create_task(bench.serve(), name="roboagents-web-bench"))

    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()

    url = f"http://{host}:{port}"
    print(f"roboagents web world on {url}")
    if source is not None:
        print(f"  replaying {source}")
    if session:
        print("  bench attached — prompts typed in the browser will run")
    else:
        print("  read-only — start with --session to accept prompts")

    try:
        await asyncio.Event().wait()
    finally:
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        await world.shutdown()
        await runner.cleanup()
