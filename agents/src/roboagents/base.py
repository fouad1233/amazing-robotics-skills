# SPDX-License-Identifier: Apache-2.0
"""The base every robotics expert inherits from.

A ``RoboAgent`` is a NOOA ``Agent`` that arrives already holding the things a
robotics task needs: a persistent shell, a todo list, guarded git, workstation
probes, and a small, *relevant* slice of the skill catalogue.

The slice matters. The catalogue has hundreds of skills; a 30B model served
locally has neither the context nor the attention to be handed all of them. Each
expert declares the patterns it cares about, and only the top few are activated
per task — see ``Policy.max_active_skills``.

Subclasses follow NOOA's own conventions:

* **one method, one LLM task** — split classify/implement/verify apart;
* **orchestrators are pure Python** — a method that only sequences other calls
  gets a real body, not an ellipsis;
* **helpers beat prompts** — exact logic goes in a deterministic method;
* **evidence before assertions** — verify, then claim.

Visualisation is *not* bolted on here. Agents publish real NOOA events through
``hooks.attach``; the terminal, web and desktop views are readers of that
stream. Nothing in this file pretends to know what an agent is doing.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from nooa import Agent
from nooa.tools import ShellTools, TodoManager

from .config import Policy
from .llm import Tier
from .skillbridge import SkillEntry, index
from .skills import GitWorkflow, WorkspaceSkill
from .types import Finding, Plan, WorkResult


class RoboAgent(Agent):
    """A robotics engineer working on this workstation.

    Read the machine before assuming it. Prefer the documented procedure in
    your skills over improvising. Verify with a command whose output you show,
    then state what you found.
    """

    #: Short domain label, used by the router, the CLI and the game world.
    domain: ClassVar[str] = "general"
    #: One line describing when this expert should be chosen. Fed to the router.
    charter: ClassVar[str] = "General robotics work."
    #: Catalogue patterns this expert draws its skills from, best first.
    skill_patterns: ClassVar[tuple[str, ...]] = ()
    #: Catalogue categories to fall back on when patterns match too little.
    skill_categories: ClassVar[tuple[str, ...]] = ()
    #: Which model tier this expert runs on by default.
    tier: ClassVar[Tier] = "worker"

    def __init__(
        self,
        llm: Any = None,
        *,
        workdir: Path | str | None = None,
        repo: Path | str | None = None,
        policy: Policy | None = None,
        skill_limit: int | None = None,
        extra_skills: tuple[str, ...] = (),
        **kwargs: Any,
    ) -> None:
        # `llm=None` must not be forwarded — NOOA distinguishes "inherit from the
        # calling parent" (the INHERIT sentinel) from an explicit client.
        if llm is None:
            super().__init__(**kwargs)
        else:
            super().__init__(llm, **kwargs)

        self._policy = policy or Policy.default()
        self.workdir = Path(workdir).expanduser() if workdir else Path.home() / "robotics"

        self.shell = ShellTools(cwd=str(self.workdir))
        self.todo = TodoManager()
        self.env = WorkspaceSkill(root=self.workdir)
        self.git = GitWorkflow(repo or self.workdir, policy=self._policy)

        self._index = index()
        self._attached: list[SkillEntry] = []
        self.activate_skills(
            patterns=(*self.skill_patterns, *extra_skills),
            limit=skill_limit if skill_limit is not None else self._policy.max_active_skills,
        )

    # -- skills ----------------------------------------------------------

    def activate_skills(
        self,
        patterns: tuple[str, ...] = (),
        *,
        limit: int | None = None,
        replace: bool = True,
    ) -> str:
        """Attach the catalogue skills matching these patterns to this agent.

        Each becomes an attribute the LLM can inspect with ``doc(self.<name>)``.
        Deterministic on purpose: the router decides *which* skills an expert
        needs, and this method simply attaches them.
        """
        limit = limit if limit is not None else self._policy.max_active_skills

        if replace:
            for entry in self._attached:
                attribute = _attr(entry.id)
                if hasattr(self, attribute):
                    delattr(self, attribute)
            self._attached = []

        chosen = self._index.select(
            patterns or self.skill_patterns,
            categories=self.skill_categories,
            limit=limit,
        )
        if not chosen:
            return "No matching skills in the catalogue."

        for skill_id, skill in self._index.text_skills(chosen).items():
            setattr(self, _attr(skill_id), skill)
        self._attached.extend(chosen)

        # Tell any watcher what this expert is now reading. Imported lazily so
        # a bare agent works with no event machinery running.
        try:
            from .hooks import announce_skills

            announce_skills(self)
        except Exception:  # noqa: BLE001 - a viewer must never break the agent
            pass

        return self.playbook()

    def playbook(self) -> str:
        """The skills currently attached, and what each one covers."""
        if not self._attached:
            return "No skills attached."
        lines = [f"{len(self._attached)} skill(s) attached — call doc(self.<name>) for the guide:"]
        for entry in self._attached:
            lines.append(f"  self.{_attr(entry.id)} — {entry.summary or entry.category}")
        return "\n".join(lines)

    def search_catalogue(self, query: str, limit: int = 10) -> str:
        """Search all catalogue skills by keyword, without attaching anything.

        Use this to find out whether a documented procedure exists before you
        invent one; then ``activate_skills(['<id>'])`` to read it.
        """
        hits = self._index.select([query], limit=limit)
        if not hits:
            return f"No catalogue skill matches {query!r}."
        return "\n".join(f"{e.id} [{e.category}] — {e.summary}" for e in hits)

    @property
    def attached_skills(self) -> list[SkillEntry]:
        """Catalogue entries currently attached. Read by the views."""
        return list(self._attached)

    # -- the three standard capabilities ---------------------------------
    #
    # Every expert answers the same three questions — what is going on, what
    # should we do, do it — so the orchestrator can treat the roster uniformly.
    # Experts specialise by their class docstring, their skills, and the
    # deterministic helpers they add, not by inventing new method names.

    async def investigate(self, question: str) -> list[Finding]:
        """Find out what is actually true, and show the evidence.

        Read your skills first — `doc(self.<skill>)` — then gather evidence with
        `self.shell.run(...)` and the probes on `self.env`. Every finding must
        quote the command output it rests on. Report what you observed, not what
        you expect to be true; "I could not determine X" is a valid finding and
        far more useful than a confident guess.
        """
        ...

    async def plan_work(self, task: str) -> Plan:
        """Turn a task into concrete, verifiable steps.

        Consult your skills for the documented procedure before designing your
        own. Each step needs an exact command where one exists, and a check that
        proves it worked. Set `needs_human` when the task requires physical
        access, a reboot, credentials, or a choice with more than one defensible
        answer — do not guess past those.
        """
        ...

    async def execute(self, task: str) -> WorkResult:
        """Do the task, then prove it.

        Work through `self.todo` so progress is visible. Use `self.shell` for
        commands and `self.git` for version control — commit on a branch, never
        on main. Before you report success, run the check that would fail if you
        were wrong, and put its real output in `evidence`. If it failed, say so
        and report the actual error; a truthful failure is worth more than an
        optimistic summary.
        """
        ...

    # -- description for the router --------------------------------------

    @classmethod
    def spec(cls) -> dict[str, Any]:
        """Machine-readable description of this expert, for routing."""
        return {
            "name": cls.__name__,
            "domain": cls.domain,
            "charter": cls.charter,
            "skills": list(cls.skill_patterns),
            "tier": cls.tier,
        }


def _attr(skill_id: str) -> str:
    """Skill id -> attribute name (``isaac-camera`` -> ``isaac_camera``)."""
    safe = skill_id.replace("-", "_").replace(".", "_")
    return f"s_{safe}" if not safe.isidentifier() or safe[0].isdigit() else safe
