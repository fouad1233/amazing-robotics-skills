# SPDX-License-Identifier: Apache-2.0
"""Bridge NOOA's per-agent event log onto our stream.

This is what makes the visualisations honest. Every agent carries an
``EventManager``; subscribing to it with ``on("*", …)`` means the terminal
dashboard, the browser world and the desktop pets are all driven by things that
actually happened inside the agent — the LLM turn that started, the Python cell
that ran, the error that was raised — rather than by a status field somebody
remembered to update.

Attach once per agent instance::

    detach = attach(agent)
    ...
    detach()

or, for the whole lifetime of a task::

    with watched(agent, domain="isaac-sim"):
        await agent.execute(task)
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable, Iterator
from typing import Any

from .events import EventBus, Kind, State, bus

#: How much of a payload to carry into a speech bubble. Whole stdout dumps
#: would be unreadable on screen and pointless in a JSONL transcript.
_SNIP = 400


def _text(value: Any, limit: int = _SNIP) -> str:
    if value is None:
        return ""
    text = value if isinstance(value, str) else str(value)
    text = " ".join(text.split())
    return text[:limit] + ("…" if len(text) > limit else "")


def _first_line(value: Any, limit: int = 120) -> str:
    text = _text(value, limit * 4)
    return text[:limit] + ("…" if len(text) > limit else "")


def attach(
    agent: Any,
    stream: EventBus | None = None,
    *,
    name: str | None = None,
    domain: str = "",
    role: str = "expert",
) -> Callable[[], None]:
    """Mirror one agent's NOOA events onto the stream. Returns an unsubscribe."""
    sink = stream or bus()
    actor = name or type(agent).__name__
    domain = domain or getattr(type(agent), "domain", "")

    sink.emit(
        Kind.AGENT_SPAWNED,
        actor,
        text=getattr(type(agent), "charter", ""),
        domain=domain,
        role=role,
        klass=type(agent).__name__,
    )

    manager = getattr(agent, "event_manager", None)
    if manager is None or not hasattr(manager, "on"):
        # Not every object handed to a viewer is a full NOOA agent; degrade to
        # spawn-only rather than refusing to show it at all.
        return lambda: None

    def handler(event: Any) -> None:
        try:
            _translate(sink, actor, event)
        except Exception:  # noqa: BLE001, S110 - a viewer must never break the agent
            pass

    unsubscribe = manager.on("*", handler)
    return unsubscribe if callable(unsubscribe) else (lambda: None)


def _translate(sink: EventBus, actor: str, event: Any) -> None:
    """Map one NOOA event onto zero or one of ours."""
    kind = type(event).__name__

    if kind == "BeforeTurn":
        sink.emit(
            Kind.AGENT_STATE,
            actor,
            text=f"thinking · {getattr(event, 'method_name', '')}",
            state=str(State.THINKING),
            method=getattr(event, "method_name", ""),
            turn=getattr(event, "turn_number", 0),
            strategy=_first_line(getattr(event, "strategy", "")),
        )

    elif kind == "AfterTurn":
        final = bool(getattr(event, "is_final", False))
        ok = getattr(event, "success", None)
        if final:
            sink.emit(
                Kind.AGENT_STATE,
                actor,
                text="finished" if ok is not False else "failed",
                state=str(State.DONE if ok is not False else State.FAILED),
                method=getattr(event, "method_name", ""),
                success=ok,
            )
        else:
            sink.emit(
                Kind.AGENT_STATE,
                actor,
                text="working",
                state=str(State.WORKING),
                turn=getattr(event, "turn_number", 0),
            )

    elif kind == "LLMComplete":
        reasoning = _text(getattr(event, "reasoning_content", ""))
        sink.emit(
            Kind.THOUGHT,
            actor,
            text=reasoning or f"{getattr(event, 'model_name', 'llm')} replied",
            model=getattr(event, "model_name", ""),
            prompt_tokens=getattr(event, "prompt_tokens", 0),
            completion_tokens=getattr(event, "completion_tokens", 0),
            cost_usd=getattr(event, "cost_usd", 0.0),
            tool_calls=len(getattr(event, "tool_calls", None) or []),
        )

    elif kind in ("LLMOutput", "Message", "TextOnlyReply"):
        content = _text(getattr(event, "content", ""))
        if content:
            sink.emit(Kind.MESSAGE, actor, text=content)

    elif kind == "Reasoning":
        content = _text(getattr(event, "content", ""))
        if content:
            sink.emit(Kind.THOUGHT, actor, text=content)

    elif kind == "PythonOutput":
        status = getattr(event, "execution_status", "")
        stderr = _text(getattr(event, "stderr", ""))
        stdout = _text(getattr(event, "stdout", ""))
        failed = bool(getattr(event, "error", None)) or status in ("error", "failed")
        sink.emit(
            Kind.TOOL_RESULT,
            actor,
            text=_first_line(stderr if failed and stderr else stdout) or f"cell {status}",
            status=str(status),
            ok=not failed,
            stdout=stdout,
            stderr=stderr,
            cell=getattr(event, "execution_count", 0),
        )

    elif kind == "Task":
        prompt = _text(getattr(event, "prompt", ""))
        sink.emit(Kind.TASK_STARTED, actor, text=_first_line(prompt), prompt=prompt)

    elif kind == "Error":
        sink.emit(
            Kind.ERROR,
            actor,
            text=_first_line(getattr(event, "content", "")),
            detail=_text(getattr(event, "content", ""), 2000),
        )

    elif kind == "Notification":
        sink.emit(
            Kind.MESSAGE,
            actor,
            text=_first_line(getattr(event, "description", "")),
            source=getattr(event, "source", ""),
        )


@contextlib.contextmanager
def watched(
    agent: Any,
    stream: EventBus | None = None,
    *,
    name: str | None = None,
    domain: str = "",
    role: str = "expert",
) -> Iterator[Any]:
    """Attach for the duration of a block, then retire the agent from the view."""
    sink = stream or bus()
    actor = name or type(agent).__name__
    detach = attach(agent, sink, name=actor, domain=domain, role=role)
    try:
        yield agent
    finally:
        with contextlib.suppress(Exception):
            detach()
        sink.emit(Kind.AGENT_RETIRED, actor)


def announce_skills(agent: Any, stream: EventBus | None = None, name: str | None = None) -> None:
    """Publish which catalogue skills an agent currently has attached.

    Called after ``activate_skills`` so a viewer can show what an expert is
    actually reading — the whole point of the catalogue.
    """
    sink = stream or bus()
    actor = name or type(agent).__name__
    attached = getattr(agent, "_attached", []) or []
    skills = [
        {"id": entry.id, "category": entry.category, "summary": entry.summary}
        for entry in attached
    ]
    sink.emit(
        Kind.SKILL_ACTIVATED,
        actor,
        text=", ".join(s["id"] for s in skills) or "no skills",
        skills=skills,
    )
