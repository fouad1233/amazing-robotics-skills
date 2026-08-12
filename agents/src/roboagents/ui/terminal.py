# SPDX-License-Identifier: Apache-2.0
"""The terminal view — watch the bench work, and type at it.

Left: who is on the bench, what state each expert is in, which catalogue skills
it has open. Right: the conversation — thoughts dimmed, messages between
experts as ``a -> b``, tool results with their real output, errors in red.
Bottom: a prompt line you can type into, gated on whether the bench is free.

Everything on screen came off ``events.bus()``. Nothing here polls an agent or
maintains a status of its own, so the display cannot drift from the truth.

Run it against a live run, or replay a recorded one::

    roboagents tui                       # live, in this process
    roboagents tui --run <transcript>    # replay a finished run
    roboagents tui --follow <transcript> # tail a run happening elsewhere
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import sys
import time
from collections import deque
from collections.abc import AsyncIterator
from pathlib import Path

from rich.console import Group, RenderableType
from rich.layout import Layout
from rich.live import Live
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from ..events import Event, EventBus, Kind, State, bus, read_jsonl, tail_jsonl

#: Redraw rate. Fast enough to feel live, slow enough not to fight the model.
_FPS = 8

#: Colours per agent, hashed from the name so a given expert keeps its colour
#: across runs and matches the web and desktop views.
_PALETTE = (
    "cyan",
    "magenta",
    "green",
    "yellow",
    "blue",
    "bright_cyan",
    "bright_magenta",
    "bright_green",
    "bright_yellow",
    "bright_blue",
)

_STATE_STYLE: dict[str, tuple[str, str]] = {
    State.IDLE: ("·", "dim"),
    State.THINKING: ("?", "cyan"),
    State.WORKING: ("*", "yellow"),
    State.WAITING: ("~", "blue"),
    State.REVIEWING: ("!", "magenta"),
    State.DONE: ("+", "green"),
    State.FAILED: ("x", "red"),
}

_FEED_MAX = 400


def agent_colour(name: str) -> str:
    """Stable colour for an agent name."""
    return _PALETTE[sum(name.encode()) % len(_PALETTE)]


class _Agent:
    """What the view knows about one expert. Purely derived from events."""

    def __init__(self, name: str, domain: str = "", role: str = "expert") -> None:
        self.name = name
        self.domain = domain
        self.role = role
        self.state: str = State.IDLE
        self.detail = ""
        self.skills: list[str] = []
        self.tokens = 0
        self.retired = False
        self.last_seen = time.time()


class Dashboard:
    """Folds the event stream into something drawable."""

    def __init__(self) -> None:
        self.agents: dict[str, _Agent] = {}
        self.feed: deque[Event] = deque(maxlen=_FEED_MAX)
        self.run_id = ""
        self.request = ""
        self.started = time.time()
        self.counts = {"messages": 0, "tools": 0, "errors": 0, "prompts": 0}
        self.last_tool: Event | None = None
        self.bench_busy = False
        self.bench_pending = 0
        self.bench_detail = ""
        self.finished = False

    # -- ingest ----------------------------------------------------------

    def apply(self, event: Event) -> None:
        kind = event.kind
        self.run_id = event.run_id or self.run_id

        if kind == Kind.RUN_STARTED:
            self.request = event.text
            self.started = time.time()
            self.finished = False
        elif kind == Kind.RUN_FINISHED:
            self.finished = True

        elif kind == Kind.AGENT_SPAWNED:
            record = _Agent(
                event.actor,
                domain=str(event.data.get("domain", "")),
                role=str(event.data.get("role", "expert")),
            )
            record.detail = event.text
            self.agents[event.actor] = record
        elif kind == Kind.AGENT_RETIRED:
            if event.actor in self.agents:
                self.agents[event.actor].retired = True

        elif kind == Kind.AGENT_STATE:
            record = self._ensure(event.actor)
            record.state = str(event.data.get("state", record.state))
            record.detail = event.text or record.detail
        elif kind == Kind.SKILL_ACTIVATED:
            record = self._ensure(event.actor)
            record.skills = [str(s.get("id", "")) for s in event.data.get("skills", [])]
        elif kind == Kind.THOUGHT:
            record = self._ensure(event.actor)
            record.tokens += int(event.data.get("completion_tokens", 0) or 0)

        elif kind == Kind.BENCH_STATE:
            self.bench_busy = bool(event.data.get("busy"))
            self.bench_pending = int(event.data.get("pending", 0) or 0)
            self.bench_detail = event.text

        if kind == Kind.MESSAGE:
            self.counts["messages"] += 1
        elif kind in (Kind.TOOL_CALL, Kind.TOOL_RESULT):
            self.counts["tools"] += 1
            self.last_tool = event
        elif kind == Kind.ERROR:
            self.counts["errors"] += 1
        elif kind == Kind.PROMPT:
            self.counts["prompts"] += 1

        if kind not in (Kind.AGENT_STATE, Kind.BENCH_STATE):
            self.feed.append(event)

        if event.actor in self.agents:
            self.agents[event.actor].last_seen = time.time()

    def _ensure(self, name: str) -> _Agent:
        if name not in self.agents:
            self.agents[name] = _Agent(name)
        return self.agents[name]

    # -- render ----------------------------------------------------------

    def header(self) -> RenderableType:
        elapsed = time.time() - self.started
        bits = [
            f"run {self.run_id or '-'}",
            f"{elapsed:5.0f}s",
            f"{len(self.agents)} agents",
            f"{self.counts['messages']} msg",
            f"{self.counts['tools']} tools",
        ]
        if self.counts["errors"]:
            bits.append(f"[red]{self.counts['errors']} errors[/red]")
        if self.finished:
            bits.append("[green]finished[/green]")
        line = Text.from_markup("   ".join(bits))
        title = escape(self.request[:110]) if self.request else "roboagents"
        return Panel(line, title=title, border_style="blue", padding=(0, 1))

    def roster(self) -> RenderableType:
        table = Table.grid(padding=(0, 1))
        table.add_column(width=2)
        table.add_column(ratio=2, no_wrap=True)
        table.add_column(ratio=3)

        for record in self.agents.values():
            glyph, style = _STATE_STYLE.get(record.state, ("·", "dim"))
            if record.retired:
                style = "dim"
            colour = agent_colour(record.name)
            name = Text(record.name, style=f"{colour}{' dim' if record.retired else ''}")
            if record.domain:
                name.append(f"  {record.domain}", style="dim")

            detail = Text(record.detail[:80], style=style)
            if record.skills:
                detail.append("\n" + ", ".join(record.skills[:3])[:78], style="dim")
            table.add_row(Text(glyph, style=style), name, detail)

        if not self.agents:
            table.add_row("", Text("no agents yet", style="dim"), "")
        return Panel(table, title="bench", border_style="grey37", padding=(0, 1))

    def conversation(self, height: int) -> RenderableType:
        lines: list[Text] = []
        for event in list(self.feed)[-max(1, height) :]:
            lines.append(self._line(event))
        body: RenderableType = Group(*lines) if lines else Text("waiting…", style="dim")
        return Panel(body, title="conversation", border_style="grey37", padding=(0, 1))

    def _line(self, event: Event) -> Text:
        stamp = time.strftime("%H:%M:%S", time.localtime(event.ts))
        text = Text(f"{stamp} ", style="dim")
        colour = agent_colour(event.actor)

        if event.kind == Kind.PROMPT:
            text.append("you", style="bold white")
            text.append(f"  {event.text}", style="bold")
            return text
        if event.kind == Kind.ERROR:
            text.append(event.actor, style=colour)
            text.append(f"  {event.text}", style="red")
            return text
        if event.kind == Kind.THOUGHT:
            text.append(event.actor, style=f"{colour} dim")
            text.append(f"  {event.text}", style="italic dim")
            return text
        if event.kind == Kind.MESSAGE:
            text.append(event.actor, style=colour)
            if event.target:
                text.append(" -> ", style="dim")
                text.append(event.target, style=agent_colour(event.target))
            text.append(f"  {event.text}")
            return text
        if event.kind == Kind.TOOL_RESULT:
            ok = event.data.get("ok", True)
            text.append(event.actor, style=colour)
            text.append("  $ ", style="dim")
            text.append(event.text, style="green" if ok else "red")
            return text
        if event.kind == Kind.SKILL_ACTIVATED:
            text.append(event.actor, style=colour)
            text.append("  opened ", style="dim")
            text.append(event.text, style="blue")
            return text
        if event.kind == Kind.VERDICT:
            holds = event.data.get("holds_up")
            text.append(event.actor, style=colour)
            text.append("  verdict ", style="dim")
            text.append(event.text, style="green" if holds else "yellow")
            return text

        text.append(event.actor, style=colour)
        text.append(f"  {event.kind} ", style="dim")
        text.append(event.text[:120])
        return text

    def footer(self, draft: str, interactive: bool) -> RenderableType:
        if self.last_tool is not None:
            ok = self.last_tool.data.get("ok", True)
            tool = Text("last tool  ", style="dim")
            tool.append(self.last_tool.text[:100], style="green" if ok else "red")
        else:
            tool = Text("last tool  none yet", style="dim")

        if not interactive:
            return Panel(tool, border_style="grey37", padding=(0, 1))

        if self.bench_busy:
            prompt = Text("bench busy", style="yellow")
            if self.bench_pending:
                prompt.append(f" — {self.bench_pending} queued", style="dim")
            prompt.append(f"   {self.bench_detail[:60]}", style="dim")
        else:
            prompt = Text("> ", style="bold green")
            prompt.append(draft or "type a prompt, Enter to send, Ctrl-C to quit",
                          style="" if draft else "dim")

        return Panel(Group(tool, prompt), border_style="grey37", padding=(0, 1))

    def render(self, draft: str = "", interactive: bool = False) -> RenderableType:
        layout = Layout()
        layout.split_column(
            Layout(self.header(), size=3),
            Layout(name="body"),
            Layout(self.footer(draft, interactive), size=4),
        )
        # Narrow terminals get the conversation only; a 30-column roster next to
        # a 20-column feed is worse than no roster.
        width = _terminal_width()
        if width < 100:
            layout["body"].update(self.conversation(_terminal_height() - 9))
        else:
            layout["body"].split_row(
                Layout(self.roster(), ratio=2),
                Layout(self.conversation(_terminal_height() - 9), ratio=3),
            )
        return layout


def _terminal_width() -> int:
    try:
        return max(40, __import__("shutil").get_terminal_size().columns)
    except OSError:  # pragma: no cover - no tty
        return 100


def _terminal_height() -> int:
    try:
        return max(16, __import__("shutil").get_terminal_size().lines)
    except OSError:  # pragma: no cover - no tty
        return 30


# --------------------------------------------------------------------------
# Sources
# --------------------------------------------------------------------------


async def _from_bus(stream: EventBus) -> AsyncIterator[Event]:
    async for event in stream.stream(replay=True):
        yield event


async def _from_file(path: Path, follow: bool, speed: float) -> AsyncIterator[Event]:
    """Replay a transcript, optionally pacing it like the original run."""
    events = read_jsonl(path)
    previous: float | None = None
    for event in events:
        if speed > 0 and previous is not None:
            gap = (event.ts - previous) / speed
            if 0 < gap < 5:
                await asyncio.sleep(gap)
        previous = event.ts
        yield event
    if follow:
        async for event in tail_jsonl(path):
            yield event


# --------------------------------------------------------------------------
# Input
# --------------------------------------------------------------------------


async def _read_prompts(dashboard: Dashboard) -> None:
    """Feed typed lines into the control bus.

    Deliberately line-based rather than raw-mode: a full-screen editor inside a
    Live region fights rich for the cursor, and the point here is to send a
    sentence, not to edit one. stdin is read off-thread so the render loop keeps
    running while you type.
    """
    from ..control import QueueFull, control

    loop = asyncio.get_running_loop()
    control().bind_loop(loop)

    while True:
        line = await asyncio.to_thread(sys.stdin.readline)
        if not line:  # EOF: piped input, nothing more to read
            return
        text = line.strip()
        if not text:
            continue
        if text in ("/q", "/quit", "/exit"):
            raise KeyboardInterrupt
        try:
            control().submit(text, source="tui")
        except (QueueFull, ValueError) as exc:
            dashboard.bench_detail = str(exc)


async def run_terminal(
    source: Path | str | None = None,
    *,
    follow: bool = True,
    speed: float = 0.0,
    interactive: bool = False,
    stream: EventBus | None = None,
) -> None:
    """Draw the dashboard until the stream ends or the user interrupts."""
    dashboard = Dashboard()
    events = (
        _from_file(Path(source), follow, speed)
        if source is not None
        else _from_bus(stream or bus())
    )

    input_task: asyncio.Task[None] | None = None
    if interactive and sys.stdin.isatty():
        input_task = asyncio.create_task(_read_prompts(dashboard))

    live = Live(
        dashboard.render(interactive=interactive),
        refresh_per_second=_FPS,
        screen=False,
        transient=False,
    )
    try:
        with live:
            async for event in events:
                dashboard.apply(event)
                live.update(dashboard.render(interactive=interactive))
            # A replayed file ends; hold the final frame briefly so it is readable.
            live.update(dashboard.render(interactive=interactive))
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        if input_task is not None:
            input_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await input_task


async def run_tui(
    path: Path | str | None = None,
    *,
    follow: bool = True,
    interactive: bool = False,
    speed: float = 0.0,
    stream: EventBus | None = None,
) -> None:
    """The name and signature the CLI binds to.

    ``run_terminal`` is the descriptive entry point; this is the stable one the
    ``tui`` subcommand calls, with ``path`` named the way every other
    subcommand names it.
    """
    await run_terminal(
        path,
        follow=follow,
        speed=speed,
        interactive=interactive,
        stream=stream,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="roboagents tui", description=__doc__)
    parser.add_argument("--run", metavar="PATH", help="replay a recorded JSONL transcript")
    parser.add_argument("--follow", action="store_true", help="keep tailing the file")
    parser.add_argument(
        "--speed",
        type=float,
        default=0.0,
        help="replay pacing: 1.0 is real time, 0 (default) is instant",
    )
    parser.add_argument("--interactive", action="store_true", help="accept typed prompts")
    args = parser.parse_args(argv)

    try:
        asyncio.run(
            run_terminal(
                args.run,
                follow=args.follow,
                speed=args.speed,
                interactive=args.interactive,
            )
        )
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
