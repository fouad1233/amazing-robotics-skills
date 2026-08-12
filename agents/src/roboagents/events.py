# SPDX-License-Identifier: Apache-2.0
"""The event stream — one source of truth for every view.

There are three ways to watch these agents work: a terminal dashboard, a
browser game world, and sprites walking across the desktop. All three are
*views over this stream*. None of them simulates anything; if a bubble appears
over an agent's head, an LLM really did say that.

Transport is deliberately boring:

* in-process fan-out to ``asyncio.Queue`` subscribers (the TUI, the web server);
* an append-only JSONL file, so a viewer that starts late can replay, and so a
  run can be inspected after the fact;
* the web server rebroadcasts over WebSocket, which is how the desktop pets
  (a separate process, on the system Python because it needs GTK) tune in.

Emitting is synchronous and never blocks the agent doing real work. A slow or
dead viewer drops events rather than applying backpressure to the robot.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, AsyncIterator, Iterable

from .config import runs_dir

#: Newer events evict older ones for a subscriber that cannot keep up.
_QUEUE_MAX = 512
#: Replay buffer handed to a viewer that connects mid-run.
_HISTORY_MAX = 2000


class Kind(StrEnum):
    """Every kind of thing a viewer can draw."""

    RUN_STARTED = "run_started"
    RUN_FINISHED = "run_finished"
    ROUTE_DECIDED = "route_decided"

    #: A human typed something into one of the views.
    PROMPT = "prompt"
    #: Accepted but not started — a local model serves one run at a time.
    RUN_QUEUED = "run_queued"
    #: Whether the bench is free to accept work; drives the input box state.
    BENCH_STATE = "bench_state"

    AGENT_SPAWNED = "agent_spawned"
    AGENT_STATE = "agent_state"
    AGENT_RETIRED = "agent_retired"

    SKILL_ACTIVATED = "skill_activated"
    THOUGHT = "thought"
    MESSAGE = "message"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"

    TASK_STARTED = "task_started"
    TASK_FINISHED = "task_finished"
    VERDICT = "verdict"
    ERROR = "error"


class State(StrEnum):
    """What an agent is doing — drives the sprite animation and the TUI colour."""

    IDLE = "idle"
    THINKING = "thinking"
    WORKING = "working"
    WAITING = "waiting"
    REVIEWING = "reviewing"
    DONE = "done"
    FAILED = "failed"


@dataclass(frozen=True)
class Event:
    """One thing that happened. Serialises straight to JSON for every transport."""

    kind: str
    actor: str = "system"
    #: Recipient for MESSAGE, subject under review for VERDICT.
    target: str = ""
    #: Human-readable one-liner. This is what a speech bubble shows.
    text: str = ""
    #: Structured payload; shape depends on `kind`.
    data: dict[str, Any] = field(default_factory=dict)
    seq: int = 0
    ts: float = field(default_factory=time.time)
    run_id: str = ""

    def to_json(self) -> str:
        return json.dumps(asdict(self), default=str)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Event":
        allowed = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in raw.items() if k in allowed})


class EventBus:
    """Fan-out hub. Thread-safe to emit into, async to read from."""

    def __init__(self, run_id: str = "", sink: Path | None = None) -> None:
        self.run_id = run_id or time.strftime("%Y%m%d-%H%M%S")
        self._subscribers: list[asyncio.Queue[Event]] = []
        self._history: deque[Event] = deque(maxlen=_HISTORY_MAX)
        self._seq = 0
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._sink_path = sink if sink is not None else runs_dir() / f"{self.run_id}.jsonl"
        self._sink = None
        if self._sink_path:
            self._sink_path.parent.mkdir(parents=True, exist_ok=True)
            self._sink = self._sink_path.open("a", buffering=1)

    # -- lifecycle -------------------------------------------------------

    def bind_loop(self, loop: asyncio.AbstractEventLoop | None = None) -> None:
        """Remember the loop so background threads can deliver events into it."""
        self._loop = loop or asyncio.get_running_loop()

    def close(self) -> None:
        if self._sink:
            with contextlib.suppress(Exception):
                self._sink.close()
            self._sink = None

    @property
    def sink_path(self) -> Path | None:
        return self._sink_path

    # -- producing -------------------------------------------------------

    def emit(
        self,
        kind: Kind | str,
        actor: str = "system",
        *,
        target: str = "",
        text: str = "",
        **data: Any,
    ) -> Event:
        """Record something. Safe to call from any thread; never blocks."""
        with self._lock:
            self._seq += 1
            event = Event(
                kind=str(kind),
                actor=actor,
                target=target,
                text=text,
                data=data,
                seq=self._seq,
                run_id=self.run_id,
            )
            self._history.append(event)
            if self._sink:
                with contextlib.suppress(Exception):
                    self._sink.write(event.to_json() + "\n")
            queues = list(self._subscribers)

        if not queues:
            return event

        running = _current_loop()
        if running is not None and (self._loop is None or running is self._loop):
            for queue in queues:
                _offer(queue, event)
        elif self._loop is not None and self._loop.is_running():
            # Emitted from a worker thread: hop to the loop that owns the queues.
            with contextlib.suppress(RuntimeError):
                self._loop.call_soon_threadsafe(lambda: [_offer(q, event) for q in queues])
        else:
            for queue in queues:
                _offer(queue, event)
        return event

    # -- consuming -------------------------------------------------------

    def history(self, limit: int = _HISTORY_MAX) -> list[Event]:
        with self._lock:
            return list(self._history)[-limit:]

    @contextlib.contextmanager
    def subscription(self) -> Any:
        """Context-managed queue; guarantees the subscriber is removed."""
        queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=_QUEUE_MAX)
        with self._lock:
            self._subscribers.append(queue)
        try:
            yield queue
        finally:
            with self._lock:
                if queue in self._subscribers:
                    self._subscribers.remove(queue)

    async def stream(self, replay: bool = True) -> AsyncIterator[Event]:
        """Every event from now on, optionally preceded by the backlog."""
        with self.subscription() as queue:
            if replay:
                for event in self.history():
                    yield event
            while True:
                yield await queue.get()


def _offer(queue: asyncio.Queue, event: Event) -> None:
    """Never block a producer. A viewer too slow to keep up loses the oldest."""
    try:
        queue.put_nowait(event)
    except asyncio.QueueFull:
        with contextlib.suppress(asyncio.QueueEmpty):
            queue.get_nowait()
        with contextlib.suppress(asyncio.QueueFull):
            queue.put_nowait(event)


def _current_loop() -> asyncio.AbstractEventLoop | None:
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return None


# --------------------------------------------------------------------------
# Process-wide default bus
# --------------------------------------------------------------------------

_BUS: EventBus | None = None
_BUS_LOCK = threading.Lock()


def bus() -> EventBus:
    """The bus this process publishes to."""
    global _BUS
    with _BUS_LOCK:
        if _BUS is None:
            _BUS = EventBus(run_id=os.environ.get("ROBOAGENTS_RUN_ID", ""))
        return _BUS


def reset_bus(new: EventBus | None = None) -> EventBus:
    """Swap the default bus. Used by the tests and by each fresh CLI run."""
    global _BUS
    with _BUS_LOCK:
        if _BUS is not None and new is not _BUS:
            _BUS.close()
        _BUS = new if new is not None else EventBus()
        return _BUS


def emit(kind: Kind | str, actor: str = "system", **kwargs: Any) -> Event:
    """Shorthand for ``bus().emit(...)``."""
    return bus().emit(kind, actor, **kwargs)


# --------------------------------------------------------------------------
# Reading a finished (or in-flight) run back off disk
# --------------------------------------------------------------------------


def read_jsonl(path: Path | str) -> list[Event]:
    """Load a recorded run. Malformed trailing lines are skipped, not fatal —
    the file is written live, so the last line may be half-flushed."""
    events: list[Event] = []
    for line in Path(path).read_text(errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(Event.from_dict(json.loads(line)))
        except (json.JSONDecodeError, TypeError):
            continue
    return events


def latest_run() -> Path | None:
    """The most recently written run transcript, if there is one."""
    runs = sorted(runs_dir().glob("*.jsonl"), key=lambda p: p.stat().st_mtime)
    return runs[-1] if runs else None


async def tail_jsonl(path: Path | str, poll: float = 0.25) -> AsyncIterator[Event]:
    """Follow a run file as it is written. Lets a viewer attach to a run that
    is happening in another process without needing the WebSocket server."""
    handle = Path(path).open()
    try:
        while True:
            line = handle.readline()
            if not line:
                await asyncio.sleep(poll)
                continue
            line = line.strip()
            if not line:
                continue
            try:
                yield Event.from_dict(json.loads(line))
            except (json.JSONDecodeError, TypeError):
                continue
    finally:
        handle.close()


def agents_seen(events: Iterable[Event]) -> dict[str, dict[str, Any]]:
    """Fold an event list into current per-agent state, for a late-joining view."""
    agents: dict[str, dict[str, Any]] = {}
    for event in events:
        if event.kind == Kind.AGENT_SPAWNED:
            agents[event.actor] = {
                "name": event.actor,
                "domain": event.data.get("domain", ""),
                "state": State.IDLE,
                "text": "",
                "skills": [],
            }
        elif event.actor in agents:
            record = agents[event.actor]
            if event.kind == Kind.AGENT_STATE:
                record["state"] = event.data.get("state", record["state"])
                record["text"] = event.text or record["text"]
            elif event.kind == Kind.SKILL_ACTIVATED:
                record["skills"] = event.data.get("skills", record["skills"])
            elif event.kind in (Kind.MESSAGE, Kind.THOUGHT):
                record["text"] = event.text
    return agents
