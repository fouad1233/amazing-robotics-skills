# SPDX-License-Identifier: Apache-2.0
"""Paths and settings.

Everything that needs to know *where things live* asks this module, so that the
package works both from a git checkout (dev) and from an installed wheel.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

#: Candidate locations for the skill catalogue, in priority order.
_CATALOGUE_CANDIDATES = (
    # dev checkout: agents/src/roboagents/config.py -> repo root is parents[3]
    Path(__file__).resolve().parents[3],
    Path.home() / "robotics" / "amazing-robotics-skills",
    Path.home() / ".local" / "share" / "roboagents" / "catalogue",
    Path("/opt/amazing-robotics-skills"),
)


def _is_catalogue(path: Path) -> bool:
    return (path / "sources.json").is_file() and (path / "skills").is_dir()


@lru_cache(maxsize=1)
def catalogue_root() -> Path:
    """Locate the amazing-robotics-skills checkout that holds the skills.

    Override with ``ROBOAGENTS_CATALOGUE``. Raises rather than guessing wrong —
    a silently-empty skill set is much harder to debug than a missing path.
    """
    override = os.environ.get("ROBOAGENTS_CATALOGUE")
    if override:
        path = Path(override).expanduser().resolve()
        if not _is_catalogue(path):
            raise FileNotFoundError(
                f"ROBOAGENTS_CATALOGUE={path} is not a skill catalogue "
                "(expected sources.json and skills/ inside)"
            )
        return path

    for candidate in _CATALOGUE_CANDIDATES:
        if _is_catalogue(candidate):
            return candidate

    raise FileNotFoundError(
        "Could not find the amazing-robotics-skills catalogue. "
        "Clone it and set ROBOAGENTS_CATALOGUE=/path/to/amazing-robotics-skills"
    )


def cache_dir() -> Path:
    """Where materialised SKILL.md directories and run artefacts are written."""
    base = os.environ.get("ROBOAGENTS_CACHE")
    path = Path(base).expanduser() if base else Path.home() / ".cache" / "roboagents"
    path.mkdir(parents=True, exist_ok=True)
    return path


def config_dir() -> Path:
    """User configuration (models.yaml, policy.yaml)."""
    base = os.environ.get("ROBOAGENTS_CONFIG")
    path = Path(base).expanduser() if base else Path.home() / ".config" / "roboagents"
    path.mkdir(parents=True, exist_ok=True)
    return path


def runs_dir() -> Path:
    """One directory per orchestrator run, for transcripts and artefacts."""
    path = cache_dir() / "runs"
    path.mkdir(parents=True, exist_ok=True)
    return path


@dataclass(frozen=True)
class Policy:
    """Guardrails applied to every agent. Deliberately restrictive by default.

    These are enforced in deterministic Python (see ``skills.git_workflow`` and
    ``base.RoboAgent``), never by asking the model nicely.
    """

    #: Directories agents may write to. Empty tuple means "no restriction".
    workspace_roots: tuple[Path, ...] = ()
    #: Never commit straight to these branches — branch first.
    protected_branches: tuple[str, ...] = ("main", "master", "develop")
    #: Push requires this to be True.
    allow_push: bool = False
    #: force-push, hard reset, branch deletion, history rewrite.
    allow_destructive_git: bool = False
    #: Wall-clock ceiling for a single shell command.
    shell_timeout: float = 900.0
    #: How many expert agents may run concurrently.
    max_parallel_agents: int = 4
    #: Maximum orchestrator iterations before giving up.
    max_iterations: int = 12
    #: Skills activated per agent per task. Local models have small contexts;
    #: activating all 1039 skills would bury the actual instruction.
    max_active_skills: int = 4

    @classmethod
    def default(cls) -> Policy:
        roots = (
            Path.home() / "robotics",
            cache_dir(),
        )
        return cls(workspace_roots=tuple(p for p in roots if p.exists()))


def progress_log() -> Path:
    """The shared human-readable progress log for this workstation."""
    return Path(os.environ.get("ROBOAGENTS_LOG", Path.home() / "setup-progress.log"))
