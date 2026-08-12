# SPDX-License-Identifier: Apache-2.0
"""The input channel — the reverse direction of the event stream.

``events.py`` carries agents → views. This carries views → agents: you type a
prompt into the browser world, the terminal dashboard or a desktop pet, and the
bench picks it up and works on it.

One run at a time, on purpose. A 30B model served locally has one set of
weights; two orchestrated runs at once would interleave their turns, halve the
throughput of both and make the game world unreadable. Submissions are queued,
the queue depth is published as an event, and every view can show its position
and disable its input box while the bench is busy.

    session = Session()                      # owns an Orchestrator
    asyncio.create_task(session.serve())     # drains the queue forever

    control().submit("why does the benchmark segfault?", source="web")

Submitting is synchronous and thread-safe, so an aiohttp handler, a GTK idle
callback or a terminal key handler can all call it directly.
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
import time
import traceback
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import asdict, dataclass, field
from typing import Any

from .events import EventBus, Kind, bus

#: Beyond this the bench is clearly not keeping up and further prompts would
#: only pile on. Rejecting loudly beats accepting silently and never running.
_MAX_PENDING = 32


@dataclass(frozen=True)
class Command:
    """One prompt, from one view."""

    prompt: str
    source: str = "ui"
    #: Pin the roster subset, exactly like ``--experts`` on the command line.
    experts: tuple[str, ...] = ()
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    ts: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["experts"] = list(self.experts)
        return data


class QueueFull(RuntimeError):
    """Raised by ``submit`` when the backlog is already unreasonable."""


class ControlBus:
    """Prompts in, one at a time out."""

    def __init__(self, stream: EventBus | None = None) -> None:
        self._stream = stream
        self._pending: list[Command] = []
        self._lock = threading.Lock()
        self._waiters: list[asyncio.Queue[Command]] = []
        self._loop: asyncio.AbstractEventLoop | None = None
        self._busy = False

    @property
    def stream(self) -> EventBus:
        return self._stream or bus()

    def bind_loop(self, loop: asyncio.AbstractEventLoop | None = None) -> None:
        """Remember the loop, so a GTK or aiohttp thread can submit into it."""
        self._loop = loop or asyncio.get_running_loop()

    # -- producing (called from any view, any thread) --------------------

    def submit(
        self,
        prompt: str,
        *,
        source: str = "ui",
        experts: tuple[str, ...] | list[str] = (),
    ) -> Command:
        """Queue a prompt. Returns the accepted Command; never blocks."""
        text = (prompt or "").strip()
        if not text:
            raise ValueError("empty prompt")

        command = Command(prompt=text, source=source, experts=tuple(experts))

        with self._lock:
            if len(self._pending) >= _MAX_PENDING:
                raise QueueFull(
                    f"{len(self._pending)} prompts already queued; "
                    "wait for the bench to catch up"
                )
            self._pending.append(command)
            depth = len(self._pending)
            waiters = list(self._waiters)

        self.stream.emit(
            Kind.PROMPT,
            actor=f"human:{source}",
            text=text,
            command_id=command.id,
            source=source,
            experts=list(command.experts),
        )
        if depth > 1 or self._busy:
            self.stream.emit(
                Kind.RUN_QUEUED,
                actor="bench",
                text=f"queued at position {depth}",
                command_id=command.id,
                position=depth,
            )

        self._wake(waiters, command)
        return command

    def _wake(self, waiters: list[asyncio.Queue[Command]], command: Command) -> None:
        if not waiters:
            return

        def deliver() -> None:
            for queue in waiters:
                with contextlib.suppress(asyncio.QueueFull):
                    queue.put_nowait(command)

        running = _current_loop()
        if running is not None and (self._loop is None or running is self._loop):
            deliver()
        elif self._loop is not None and self._loop.is_running():
            with contextlib.suppress(RuntimeError):
                self._loop.call_soon_threadsafe(deliver)

    # -- consuming (the session) -----------------------------------------

    async def commands(self) -> AsyncIterator[Command]:
        """Yield queued prompts forever, oldest first."""
        queue: asyncio.Queue[Command] = asyncio.Queue(maxsize=_MAX_PENDING * 2)
        with self._lock:
            self._waiters.append(queue)
        try:
            while True:
                with self._lock:
                    command = self._pending.pop(0) if self._pending else None
                if command is not None:
                    yield command
                    continue
                # Nothing buffered: block until a submitter wakes us, then loop
                # back and take from _pending so ordering stays FIFO even when
                # several producers raced.
                await queue.get()
        finally:
            with self._lock:
                if queue in self._waiters:
                    self._waiters.remove(queue)

    # -- state -----------------------------------------------------------

    @property
    def pending(self) -> int:
        with self._lock:
            return len(self._pending)

    @property
    def busy(self) -> bool:
        return self._busy

    def set_busy(self, busy: bool, *, detail: str = "") -> None:
        """Publish whether the bench can take work. Views gate their input on it."""
        self._busy = busy
        self.stream.emit(
            Kind.BENCH_STATE,
            actor="bench",
            text=detail or ("working" if busy else "ready"),
            busy=busy,
            pending=self.pending,
        )


_CONTROL: ControlBus | None = None
_CONTROL_LOCK = threading.Lock()


def control() -> ControlBus:
    """The process-wide control bus."""
    global _CONTROL
    with _CONTROL_LOCK:
        if _CONTROL is None:
            _CONTROL = ControlBus()
        return _CONTROL


def reset_control(new: ControlBus | None = None) -> ControlBus:
    """Swap the control bus. Used by the tests and by each fresh CLI run."""
    global _CONTROL
    with _CONTROL_LOCK:
        _CONTROL = new if new is not None else ControlBus()
        return _CONTROL


class Session:
    """A live bench: drains the control bus and runs each prompt to completion.

    This is what turns the views from a read-only trace into something you can
    talk to. It owns exactly one ``Orchestrator``; a fresh one per prompt would
    throw away nothing useful but would re-resolve the model every time.
    """

    def __init__(
        self,
        orchestrator: Any = None,
        *,
        stream: EventBus | None = None,
        commands: ControlBus | None = None,
        on_report: Callable[[Any], None] | None = None,
        **orchestrator_kwargs: Any,
    ) -> None:
        self._orchestrator = orchestrator
        self._orchestrator_kwargs = orchestrator_kwargs
        self._stream = stream
        self._control = commands or control()
        self._on_report = on_report
        self.reports: list[Any] = []

    @property
    def stream(self) -> EventBus:
        return self._stream or bus()

    def _build(self, experts: tuple[str, ...]) -> Any:
        """An Orchestrator for this prompt.

        Pinned experts change construction, so a pinned prompt gets its own
        instance rather than mutating the shared one under a running view.
        """
        from .orchestrator import Orchestrator

        if experts:
            return Orchestrator(experts=list(experts), **self._orchestrator_kwargs)
        if self._orchestrator is None:
            self._orchestrator = Orchestrator(**self._orchestrator_kwargs)
        return self._orchestrator

    async def serve(self) -> None:
        """Run until cancelled, executing one prompt at a time."""
        self._control.bind_loop()
        with contextlib.suppress(RuntimeError):
            self.stream.bind_loop()
        self._control.set_busy(False, detail="ready for a prompt")

        async for command in self._control.commands():
            await self.run_once(command)

    async def run_once(self, command: Command) -> Any:
        """Execute one command. Never raises — a bad prompt must not end the loop."""
        self._control.set_busy(True, detail=f"working on: {command.prompt[:60]}")
        try:
            orchestrator = self._build(command.experts)
            report = await orchestrator.run(command.prompt)
            self.reports.append(report)
            if self._on_report is not None:
                with contextlib.suppress(Exception):
                    self._on_report(report)
            return report
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - one bad prompt is not fatal
            self.stream.emit(
                Kind.ERROR,
                actor="bench",
                text=f"prompt failed: {type(exc).__name__}: {exc}",
                detail=traceback.format_exc(),
                command_id=command.id,
            )
            return None
        finally:
            self._control.set_busy(False, detail="ready for a prompt")


def _current_loop() -> asyncio.AbstractEventLoop | None:
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return None
