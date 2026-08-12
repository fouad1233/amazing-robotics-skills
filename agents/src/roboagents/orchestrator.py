# SPDX-License-Identifier: Apache-2.0
"""The router — one request in, a wave of experts out.

Only one thing here is decided by a model: *which* experts get *which* slice of
the request. Everything after that is deterministic Python, because NOOA's rule
is that orchestrators are pure Python and because the interesting failure modes
of a multi-agent run — an expert that never got built, a dependency edge nobody
honoured, a claim that was never reviewed — are exactly the ones a model will
paper over if you let it.

The shape of a run:

    route()  ->  drop unknown experts  ->  dependency waves  ->  per assignment
    { build a FRESH expert, execute, hand the result to a FRESH reviewer }

Two invariants are load-bearing:

* **One instance per concurrent task.** A NOOA agentic method holds a lock on
  its agent instance, so a shared expert would silently serialise a run that
  looks parallel in the code and in the trace.
* **Verified and unverified never merge.** An expert's ``WorkResult.succeeded``
  is the expert's own opinion of itself. Until a reviewer has looked at the
  evidence, the result is unverified, and ``report_text`` prints it as such.

Everything the run does is emitted onto ``events.bus()`` as it happens, so the
terminal, browser and desktop views show real work rather than a progress bar.
"""

from __future__ import annotations

import asyncio
import contextlib
import json  # noqa: F401 - part of the CodeAct namespace for route()
import time
import traceback
from pathlib import Path
from typing import Any, ClassVar

from nooa import Agent, hidden
from nooa.runtime.channels import JobError, QueueManager
from nooa.runtime.producers import cron, monitor, tail
from pydantic import BaseModel, Field

from . import hooks, roster
from .config import Policy, runs_dir
from .events import Kind, bus
from .llm import Tier, get_llm
from .skills import WorkspaceSkill
from .types import Assignment, RoutePlan, Verdict, WorkResult

#: Channel that carries the heartbeat in ``watch``. Named, not positional, so
#: the dispatcher can tell a tick from a line of source output.
_TICK = "tick"

#: How many lines of one source ``watch`` keeps between heartbeats. A chatty
#: log must not grow the pending batch without bound while a run is in flight.
_WATCH_BUFFER = 50


class ExpertResult(BaseModel):
    """One assignment's outcome, and whether it survived review.

    ``result.succeeded`` is the expert's claim about itself; ``verdict`` is the
    reviewer's opinion of that claim. A missing verdict means nobody checked —
    which is not the same as a rejection, and is never the same as a pass.
    """

    expert: str = Field(description="Class name of the expert that ran")
    actor: str = Field(description="Label this instance used on the event stream")
    task: str
    result: WorkResult
    verdict: Verdict | None = None
    attempts: int = 1

    @property
    def verified(self) -> bool:
        """True only when the expert claimed success and a reviewer agreed."""
        return bool(self.result.succeeded and self.verdict is not None and self.verdict.holds_up)

    @property
    def status(self) -> str:
        """Why this result is where it is, in one word or two."""
        if not self.result.succeeded:
            return "failed"
        if self.verdict is None:
            return "unreviewed"
        if self.verdict.holds_up:
            return "verified"
        return f"rejected ({self.verdict.severity})"


class RunReport(BaseModel):
    """Everything one orchestrated run produced, written next to its transcript."""

    request: str
    rationale: str = ""
    results: list[ExpertResult] = Field(default_factory=list)
    verified: int = 0
    unverified: int = 0
    duration_seconds: float = 0.0
    needs_human: list[str] = Field(default_factory=list)
    dropped: list[str] = Field(default_factory=list)
    run_id: str = ""
    transcript: str = Field(default="", description="Path to the JSONL event transcript")
    report_path: str = Field(default="", description="Path this report was written to")


class _Budget:
    """Shared ceiling on how many expert calls one run may make.

    Not locked: every ``take`` runs to completion inside one asyncio tick with
    no await in it, so concurrent assignments cannot interleave mid-decrement.
    """

    def __init__(self, limit: int) -> None:
        self.limit = max(1, int(limit))
        self.spent = 0

    def take(self) -> bool:
        if self.spent >= self.limit:
            return False
        self.spent += 1
        return True


class Orchestrator(Agent):
    """Router for a bench of robotics experts. You do not do the work yourself;
    you decide who does, and you never widen a request beyond what was asked.
    """

    #: Routing is the judgement call in this package — it gets the big model.
    tier: ClassVar[Tier] = "planner"

    def __init__(
        self,
        llm: Any = None,
        *,
        workdir: Path | str | None = None,
        repo: Path | str | None = None,
        policy: Policy | None = None,
        experts: list[str] | tuple[str, ...] | None = None,
        **kwargs: Any,
    ) -> None:
        # An orchestrator has no parent agent to inherit an LLM from, so NOOA's
        # INHERIT sentinel would fail at construction. Resolve the planner tier
        # here instead; callers wanting a fake or a hosted model pass `llm=`.
        #
        # `workdir`, `repo` and `policy` are ours, not NOOA's — they are the
        # settings we hand down to each expert we build. They must be consumed
        # here rather than forwarded, or Agent.__init__ raises on them.
        super().__init__(llm if llm is not None else get_llm(self.tier), **kwargs)

        self._policy = policy or Policy.default()
        self.workdir = Path(workdir).expanduser() if workdir else Path.home() / "robotics"
        self.repo = Path(repo).expanduser() if repo else self.workdir
        # A pinned roster subset (`--experts A,B`). When set, routing is skipped
        # entirely and each named expert gets the request verbatim — which is
        # the point of pinning: the human has already done the routing.
        self._pinned: tuple[str, ...] = tuple(experts or ())
        # One client per tier, shared across experts. The lock that forces one
        # instance per task lives on the *agent*, not on the client.
        self._clients: dict[str, Any] = {}
        # Event-stream labels are per run: a second IsaacSimAgent in the same
        # run is a genuinely different worker and must not overwrite the first.
        self._actor_counts: dict[str, int] = {}

    @hidden
    def pinned_plan(self, request: str) -> RoutePlan:
        """The plan implied by ``--experts A,B``: no model, no routing.

        Each pinned expert receives the request as-is and they run together,
        because a human naming two experts is asking for two opinions rather
        than declaring an ordering between them. An unknown name is left in so
        that ``_filter`` reports it — dropping it silently here would make a
        typo look like a working run that simply found nothing.
        """
        return RoutePlan(
            rationale=f"experts pinned on the command line: {', '.join(self._pinned)}",
            assignments=[Assignment(expert=name, task=request) for name in self._pinned],
            parallel=True,
        )

    # -- the one agentic method ------------------------------------------

    async def route(self, request: str) -> RoutePlan:
        """Split this request across the smallest set of experts that covers it.

        Pick from the roster printed above and nowhere else — an expert you
        invent does not exist and its slice of the work will be dropped. Call
        `self.roster_table()` again if you need to re-read it.

        Choose the fewest experts that *genuinely* cover the request. Two
        experts on the same question produce two opinions and no more evidence;
        one expert on a question outside its charter produces a guess. If a
        single expert covers the whole request, return a single assignment.

        Each assignment's `task` must stand on its own. The expert receiving it
        sees neither the original request nor the other assignments, so name the
        machine, the paths, the versions and the acceptance check inside the
        task itself. Put catalogue skill ids or patterns in `skills` when you
        know which documented procedure applies.

        Set `depends_on` only for a real ordering constraint — when one
        assignment cannot start until another's output exists. A dependency you
        add "to be safe" costs the whole run the time of a serialised wave.
        Prefer parallel: independent assignments run at the same time.
        """
        # Prefill: run before the model gets the turn, and its output is shown
        # as the starting point. The roster is on screen before a single expert
        # name is chosen, so there is nothing to hallucinate.
        print(self.roster_table())
        ...  # noqa: PIE790 - the ellipsis IS the body; NOOA reads it as agentic

    # -- deterministic helpers the router calls ---------------------------

    def roster_table(self) -> str:
        """Every expert that actually loaded, with the charter for each.

        Read this before naming anyone. It is generated from the live registry,
        so a name absent here cannot be routed to.
        """
        return roster.table()

    # -- the run itself: pure Python --------------------------------------

    @hidden
    async def run(self, request: str) -> RunReport:
        """Route the request, run the experts, review what they claim.

        Hidden from the LLM: `route` must not be able to re-enter the whole
        orchestration through `doc(self)`.
        """
        started = time.monotonic()
        stream = bus()
        # Experts run shell work in threads; bind the loop so events emitted
        # from those threads still reach the subscribed views.
        with contextlib.suppress(RuntimeError):
            stream.bind_loop()

        self._actor_counts = {}
        actor = type(self).__name__
        stream.emit(Kind.RUN_STARTED, actor, text=request, request=request)

        report = RunReport(request=request, run_id=stream.run_id)
        transcript = stream.sink_path
        report.transcript = str(transcript) if transcript else ""

        with hooks.watched(self, name=actor, domain="orchestration", role="orchestrator"):
            try:
                plan = self.pinned_plan(request) if self._pinned else await self.route(request)
            except Exception as exc:  # noqa: BLE001 - a bad route ends the run, not the process
                detail = traceback.format_exc()
                stream.emit(
                    Kind.ERROR, actor, text=f"routing failed: {type(exc).__name__}: {exc}",
                    detail=detail,
                )
                report.rationale = f"routing failed: {type(exc).__name__}: {exc}"
                report.needs_human = ["Routing failed; no expert ran. See the transcript."]
                report.duration_seconds = time.monotonic() - started
                self._persist(report)
                stream.emit(Kind.RUN_FINISHED, actor, text="routing failed", ok=False)
                return report

            report.rationale = plan.rationale
            stream.emit(
                Kind.ROUTE_DECIDED,
                actor,
                text=plan.rationale,
                rationale=plan.rationale,
                assignments=[a.model_dump() for a in plan.assignments],
            )

            kept, report.dropped = self._filter(plan.assignments)
            for note in report.dropped:
                stream.emit(Kind.ERROR, actor, text=note, dropped=True)

            budget = _Budget(self._policy.max_iterations)
            semaphore = asyncio.Semaphore(max(1, self._policy.max_parallel_agents))

            for wave in self._waves(kept):
                # Inside a wave nothing depends on anything, so run it all at
                # once. gather returns in order; exceptions are already caught
                # per assignment, so one failure cannot cancel its neighbours.
                results = await asyncio.gather(
                    *(self._handle(a, semaphore, budget, stream) for _, a in wave)
                )
                report.results.extend(results)

        report.verified = sum(1 for r in report.results if r.verified)
        report.unverified = len(report.results) - report.verified
        report.needs_human = self._needs_human(report.results)
        report.duration_seconds = time.monotonic() - started
        self._persist(report)
        stream.emit(
            Kind.RUN_FINISHED,
            actor,
            text=f"{report.verified} verified, {report.unverified} unverified",
            verified=report.verified,
            unverified=report.unverified,
            duration_seconds=report.duration_seconds,
        )
        return report

    # -- one assignment, end to end ---------------------------------------

    async def _handle(
        self,
        assignment: Assignment,
        semaphore: asyncio.Semaphore,
        budget: _Budget,
        stream: Any,
    ) -> ExpertResult:
        """Execute one assignment and review it, holding one concurrency slot.

        The slot covers execute *and* verify so that `max_parallel_agents`
        bounds the real load. It deliberately does not bound the two phases
        against each other: while this assignment is being reviewed, another is
        still executing. There is no barrier between the phases of a run.
        """
        async with semaphore:
            try:
                cls = roster.get(assignment.expert)
            except roster.UnknownExpert as exc:  # pragma: no cover - _filter ran first
                return self._failed(assignment, "orchestrator", str(exc))

            actor = self._label(cls.__name__)
            try:
                # THE most important line in this file. A NOOA agentic method
                # holds a lock on its agent instance, so reusing one expert
                # across assignments turns asyncio.gather into a queue and the
                # run silently serialises. One assignment, one instance.
                expert = roster.build(
                    assignment.expert,
                    llm=self._client(cls.tier),
                    workdir=self.workdir,
                    repo=self.repo,
                    policy=self._policy,
                )
            except Exception as exc:  # noqa: BLE001 - a broken expert is data, not a crash
                detail = traceback.format_exc()
                stream.emit(Kind.ERROR, actor, text=f"could not build: {exc}", detail=detail)
                return self._failed(assignment, actor, detail)

            with hooks.watched(expert, name=actor, domain=cls.domain, role="expert"):
                if assignment.skills:
                    expert.activate_skills(tuple(assignment.skills))
                # Re-announce under this instance's label: the announcement made
                # inside __init__ fires before watched() has spawned the actor,
                # and carries the class name rather than this instance's label.
                with contextlib.suppress(Exception):
                    hooks.announce_skills(expert, name=actor)

                return await self._work(expert, assignment, actor, budget, stream)

    async def _work(
        self,
        expert: Any,
        assignment: Assignment,
        actor: str,
        budget: _Budget,
        stream: Any,
    ) -> ExpertResult:
        """Execute, review, and retry once if the reviewer found a major hole."""
        task = assignment.task
        attempts = 0
        result: WorkResult
        verdict: Verdict | None = None

        while True:
            if not budget.take():
                note = (
                    f"Stopped before running {assignment.expert}: this run has used its "
                    f"budget of {budget.limit} expert calls (Policy.max_iterations)."
                )
                stream.emit(Kind.ERROR, actor, text=note, budget_exhausted=True)
                result = WorkResult(summary=note, succeeded=False, evidence="")
                break

            attempts += 1
            stream.emit(
                Kind.TASK_STARTED, actor, text=task, task=task, attempt=attempts,
                expert=assignment.expert,
            )
            try:
                result = await expert.execute(task)
            except Exception as exc:  # noqa: BLE001 - one expert must not end the run
                detail = traceback.format_exc()
                stream.emit(
                    Kind.ERROR, actor, text=f"{type(exc).__name__}: {exc}", detail=detail,
                )
                stream.emit(Kind.TASK_FINISHED, actor, text="raised", succeeded=False)
                result = WorkResult(
                    summary=f"{assignment.expert} raised {type(exc).__name__}: {exc}",
                    succeeded=False,
                    evidence=detail,
                )
                break

            stream.emit(
                Kind.TASK_FINISHED, actor, text=result.summary, succeeded=result.succeeded,
                attempt=attempts,
            )
            verdict = await self._review(actor, task, result, stream)

            retryable = (
                verdict is not None
                and not verdict.holds_up
                and verdict.severity == "major"
                and attempts == 1
            )
            if not retryable:
                break

            # Retry ONCE, carrying the reviewer's objection into the task so the
            # second attempt answers it instead of repeating the first.
            assert verdict is not None
            missing = "; ".join(verdict.missing_evidence)
            task = (
                f"{assignment.task}\n\n"
                f"A reviewer rejected your previous attempt: {verdict.reason}\n"
                + (f"Missing evidence: {missing}\n" if missing else "")
                + "Redo the work and produce that evidence."
            )

        return ExpertResult(
            expert=assignment.expert,
            actor=actor,
            task=task,
            result=result,
            verdict=verdict,
            attempts=attempts,
        )

    async def _review(
        self, subject: str, task: str, result: WorkResult, stream: Any
    ) -> Verdict | None:
        """Have a fresh reviewer judge one result. None means nobody checked.

        A reviewer that cannot be built or that raises leaves the result
        *unverified* — it never leaves it looking approved.
        """
        if "ReviewerAgent" not in roster.REGISTRY:
            stream.emit(
                Kind.ERROR,
                "orchestrator",
                target=subject,
                text="ReviewerAgent did not load; this result is unverified.",
            )
            return None

        actor = self._label("ReviewerAgent")
        try:
            # Fresh instance again: reviews of different assignments overlap,
            # and one shared reviewer would serialise every one of them.
            reviewer = roster.build(
                "ReviewerAgent",
                llm=self._client("planner"),
                workdir=self.workdir,
                repo=self.repo,
                policy=self._policy,
            )
        except Exception as exc:  # noqa: BLE001
            stream.emit(Kind.ERROR, actor, target=subject, text=f"could not build reviewer: {exc}")
            return None

        claim = f"Task: {task}\n\nClaimed result: {result.summary}"
        with hooks.watched(reviewer, name=actor, domain=type(reviewer).domain, role="reviewer"):
            try:
                verdict = await reviewer.verify(claim, result.evidence)
            except Exception as exc:  # noqa: BLE001 - unreviewed, not approved
                stream.emit(
                    Kind.ERROR, actor, target=subject,
                    text=f"review failed: {type(exc).__name__}: {exc}",
                    detail=traceback.format_exc(),
                )
                return None

        stream.emit(
            Kind.VERDICT,
            actor,
            target=subject,
            text=verdict.reason,
            holds_up=verdict.holds_up,
            severity=verdict.severity,
            missing_evidence=verdict.missing_evidence,
        )
        return verdict

    # -- routing hygiene ---------------------------------------------------

    @staticmethod
    def _filter(assignments: list[Assignment]) -> tuple[list[tuple[int, Assignment]], list[str]]:
        """Keep only assignments naming a real expert; say what was dropped.

        Indices are the *original* positions, because ``depends_on`` refers to
        them. Renumbering after a drop would silently rewire the graph.
        """
        kept: list[tuple[int, Assignment]] = []
        dropped: list[str] = []
        for position, assignment in enumerate(assignments):
            try:
                roster.get(assignment.expert)
            except roster.UnknownExpert as exc:
                dropped.append(f"dropped assignment {position} for {assignment.expert!r}: {exc}")
                continue
            kept.append((position, assignment))
        return kept, dropped

    @staticmethod
    def _waves(kept: list[tuple[int, Assignment]]) -> list[list[tuple[int, Assignment]]]:
        """Layer the assignments so every dependency lands in an earlier wave.

        Dependencies on an index that was dropped, on itself, or outside the
        list are ignored — an edge to something that will never run would
        otherwise deadlock the whole request. A genuine cycle is broken by
        running the remainder together rather than by hanging.
        """
        by_index = {index: assignment for index, assignment in kept}
        pending = [index for index, _ in kept]
        satisfied: set[int] = set()
        waves: list[list[tuple[int, Assignment]]] = []

        while pending:
            ready = [
                index
                for index in pending
                if all(
                    dependency in satisfied
                    for dependency in by_index[index].depends_on
                    if dependency in by_index and dependency != index
                )
            ]
            if not ready:
                ready = list(pending)
            waves.append([(index, by_index[index]) for index in ready])
            satisfied.update(ready)
            chosen = set(ready)
            pending = [index for index in pending if index not in chosen]

        return waves

    # -- reporting ---------------------------------------------------------

    def report_text(self, report: RunReport) -> str:
        """The run, written out for a human.

        Verified and unverified results are printed in separate sections and an
        unverified result always carries the reason it is unverified. Nothing
        here can read as "done" unless a reviewer said so.
        """
        lines = [
            f"Request: {report.request}",
            f"Run:     {report.run_id}  ({report.duration_seconds:.1f}s)",
        ]
        if report.rationale:
            lines.append(f"Routing: {report.rationale}")
        lines.append("")
        lines.append(
            f"{len(report.results)} assignment(s): "
            f"{report.verified} verified, {report.unverified} unverified."
        )

        verified = [r for r in report.results if r.verified]
        unverified = [r for r in report.results if not r.verified]

        lines.append("")
        lines.append("VERIFIED — a reviewer checked the evidence")
        if not verified:
            lines.append("  (nothing)")
        for entry in verified:
            lines.append(f"  {entry.actor}: {entry.result.summary}")
            if entry.verdict is not None:
                lines.append(f"      reviewer: {entry.verdict.reason}")
            for path in entry.result.files_changed:
                lines.append(f"      changed: {path}")

        lines.append("")
        lines.append("UNVERIFIED — not proven; do not act on these as if they were done")
        if not unverified:
            lines.append("  (nothing)")
        for entry in unverified:
            lines.append(f"  {entry.actor} [{entry.status}]: {entry.result.summary}")
            if entry.verdict is not None:
                lines.append(f"      reviewer: {entry.verdict.reason}")
                for gap in entry.verdict.missing_evidence:
                    lines.append(f"      missing: {gap}")
            else:
                lines.append("      reviewer: nobody reviewed this result")
            if entry.attempts > 1:
                lines.append(f"      attempts: {entry.attempts}")

        if report.dropped:
            lines.append("")
            lines.append("DROPPED — routed to an expert that does not exist")
            lines += [f"  {note}" for note in report.dropped]

        if report.needs_human:
            lines.append("")
            lines.append("NEEDS A HUMAN")
            lines += [f"  {note}" for note in report.needs_human]

        follow_ups = [
            f"  {entry.actor}: {item}"
            for entry in report.results
            for item in entry.result.follow_up
        ]
        if follow_ups:
            lines.append("")
            lines.append("FOLLOW-UP suggested by the experts")
            lines += follow_ups

        if report.transcript:
            lines.append("")
            lines.append(f"transcript: {report.transcript}")
        if report.report_path:
            lines.append(f"report:     {report.report_path}")
        return "\n".join(lines)

    @staticmethod
    def _needs_human(results: list[ExpertResult]) -> list[str]:
        """Everything a person has to pick up, derived from what actually happened."""
        notes: list[str] = []
        for entry in results:
            if not entry.result.succeeded:
                notes.append(f"{entry.actor} did not complete its task: {entry.result.summary}")
            elif entry.verdict is None:
                notes.append(f"{entry.actor}'s claim was never reviewed: {entry.result.summary}")
            elif not entry.verdict.holds_up:
                notes.append(
                    f"{entry.actor}'s claim was rejected ({entry.verdict.severity}): "
                    f"{entry.verdict.reason}"
                )
        return notes

    def _persist(self, report: RunReport) -> None:
        """Write the report beside the JSONL transcript and log one line.

        Best effort on purpose: a read-only cache directory or progress log must
        not throw away a run that already happened.
        """
        try:
            path = runs_dir() / f"{report.run_id}.report.json"
            path.write_text(report.model_dump_json(indent=2))
            report.report_path = str(path)
        except OSError as exc:
            report.report_path = f"(not written: {exc})"

        summary = (
            f"roboagents run {report.run_id}: {len(report.results)} assignment(s), "
            f"{report.verified} verified, {report.unverified} unverified, "
            f"{report.duration_seconds:.1f}s — {report.request[:120]}"
        )
        with contextlib.suppress(Exception):
            WorkspaceSkill(root=self.workdir).note(summary)

    # -- reactive mode -----------------------------------------------------

    @hidden
    async def watch(self, sources: list[str], interval: float = 300.0) -> None:
        """React to changing sources: run the orchestrator when they say something.

        Each source is a file to tail if it exists on disk, otherwise a shell
        command to monitor. Output is *batched*, not acted on line by line — a
        heartbeat every ``interval`` seconds drains whatever arrived and turns it
        into one request. A full multi-expert run per log line would be absurd,
        and the batch is what makes ``interval`` mean something.

        Runs until cancelled. While a run is in flight the dispatcher is not
        racing, but nothing is lost: channels buffer, and the next tick picks the
        backlog up.
        """
        queues = QueueManager(event_manager=self.event_manager)
        stream = bus()
        actor = type(self).__name__

        # Registration order is priority order in race(), and spawn() refuses a
        # channel that does not exist yet — so register everything first.
        by_channel: dict[str, str] = {}
        for position, source in enumerate(sources):
            channel = f"source{position}"
            queues.queue(channel)
            by_channel[channel] = source
        queues.queue(_TICK)

        for channel, source in by_channel.items():
            path = Path(source).expanduser()
            if path.is_file():
                queues.spawn(tail(str(path)), channel=channel, label=f"tail {source}")
            else:
                queues.spawn(monitor(source), channel=channel, label=f"monitor {source}")
        queues.spawn(cron(interval), channel=_TICK, label=f"tick/{interval:g}s")

        pending: dict[str, list[str]] = {}
        try:
            while True:
                try:
                    items = await queues.race()
                except ValueError:
                    # No channels registered at all — nothing to watch. Exit
                    # cleanly, as the channels skill prescribes.
                    return

                if not items:
                    # An event-mode put woke us. This dispatcher registers no
                    # event-mode channels, so there is nothing to consume.
                    continue

                channel, item = items[0]
                if isinstance(item, JobError):
                    stream.emit(
                        Kind.ERROR,
                        actor,
                        text=f"watch source {by_channel.get(channel, channel)!r} failed: "
                        f"{item.error_type}: {item.error_message}",
                        channel=channel,
                    )
                    continue

                if channel == _TICK:
                    if not pending:
                        continue
                    batch, pending = pending, {}
                    await self.run(self._watch_request(batch))
                    continue

                source = by_channel.get(channel, channel)
                lines = pending.setdefault(source, [])
                lines.append(str(item))
                if len(lines) > _WATCH_BUFFER:
                    del lines[0]
        finally:
            # Kills the monitored process groups and the tails. Suppressed
            # because this also runs on cancellation, where the awaits below
            # may themselves be cancelled.
            with contextlib.suppress(Exception):
                await queues.shutdown()

    @staticmethod
    def _watch_request(batch: dict[str, list[str]]) -> str:
        """Turn a batch of source output into one self-contained request."""
        parts = [
            (
                "These sources produced new output since the last check. Work out "
                "whether anything here needs action, and act only on what does."
            ),
        ]
        for source, lines in batch.items():
            body = "\n".join(lines)
            parts.append(f"\n--- {source} ({len(lines)} line(s)) ---\n{body}")
        return "\n".join(parts)

    # -- plumbing ----------------------------------------------------------

    def _client(self, tier: str) -> Any:
        """One LLM client per tier, built on first use."""
        if tier not in self._clients:
            self._clients[tier] = get_llm(tier)  # type: ignore[arg-type]
        return self._clients[tier]

    def _label(self, class_name: str) -> str:
        """A unique event-stream label for one instance of an expert class."""
        count = self._actor_counts.get(class_name, 0) + 1
        self._actor_counts[class_name] = count
        return class_name if count == 1 else f"{class_name}#{count}"

    @staticmethod
    def _failed(assignment: Assignment, actor: str, detail: str) -> ExpertResult:
        """Record an assignment that never got as far as running."""
        return ExpertResult(
            expert=assignment.expert,
            actor=actor,
            task=assignment.task,
            result=WorkResult(
                summary=f"{assignment.expert} could not be started.",
                succeeded=False,
                evidence=detail,
            ),
            attempts=0,
        )
