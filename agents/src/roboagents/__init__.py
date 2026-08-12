# SPDX-License-Identifier: Apache-2.0
"""Robotics domain-expert agents, built on NVIDIA OO Agents.

    from roboagents import build, Orchestrator

    reviewer = build("ReviewerAgent", llm=get_llm("planner"))

The catalogue in this repository supplies the domain knowledge; this package
supplies the agents that act on it. See ``agents/README.md``.
"""

from __future__ import annotations

from .base import RoboAgent
from .config import Policy
from .llm import get_llm, resolve, status
from .roster import REGISTRY, build, get, names, specs, table
from .skillbridge import SkillEntry, SkillIndex, index
from .types import (
    Assignment,
    Finding,
    Plan,
    RoutePlan,
    Step,
    Verdict,
    WorkResult,
)

__version__ = "0.1.0"

__all__ = [
    "Assignment",
    "Finding",
    "Plan",
    "Policy",
    "REGISTRY",
    "RoboAgent",
    "RoutePlan",
    "SkillEntry",
    "SkillIndex",
    "Step",
    "Verdict",
    "WorkResult",
    "__version__",
    "build",
    "get",
    "get_llm",
    "index",
    "names",
    "resolve",
    "specs",
    "status",
    "table",
]
