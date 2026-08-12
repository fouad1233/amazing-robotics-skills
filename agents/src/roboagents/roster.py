# SPDX-License-Identifier: Apache-2.0
"""The roster — every expert, discoverable by name.

Imports are recorded rather than raised so that one broken expert module cannot
take down the whole CLI. ``IMPORT_ERRORS`` is surfaced by ``roboagents doctor``
and asserted empty by the test suite, so a failure is loud without being fatal.
"""

from __future__ import annotations

import importlib
from collections.abc import Iterator
from typing import Any

from .base import RoboAgent

#: module name -> class name, in the order the roster is displayed.
_EXPERTS: tuple[tuple[str, str], ...] = (
    ("isaac_sim", "IsaacSimAgent"),
    ("isaac_lab", "IsaacLabAgent"),
    ("newton_physics", "NewtonPhysicsAgent"),
    ("warp", "WarpAgent"),
    ("usd", "USDAgent"),
    ("ros2", "ROS2Agent"),
    ("perception", "PerceptionAgent"),
    ("lerobot", "LeRobotAgent"),
    ("vla", "VLAAgent"),
    ("data", "DataAgent"),
    ("jetson", "JetsonAgent"),
    ("inference", "InferenceAgent"),
    ("simops", "SimOpsAgent"),
    ("repo", "RepoAgent"),
    ("reviewer", "ReviewerAgent"),
    ("skill_scout", "SkillScoutAgent"),
)

REGISTRY: dict[str, type[RoboAgent]] = {}
IMPORT_ERRORS: dict[str, str] = {}

for _module, _class in _EXPERTS:
    try:
        REGISTRY[_class] = getattr(importlib.import_module(f".experts.{_module}", __package__), _class)
    except Exception as _exc:  # noqa: BLE001 - recorded, reported by `doctor`
        IMPORT_ERRORS[_class] = f"{type(_exc).__name__}: {_exc}"


class UnknownExpert(KeyError):
    """Raised when a name does not match anything in the roster."""


def names() -> list[str]:
    """Class names of every expert that loaded."""
    return list(REGISTRY)


def get(name: str) -> type[RoboAgent]:
    """Look an expert up by class name, case-insensitively."""
    if name in REGISTRY:
        return REGISTRY[name]
    lowered = {key.lower(): key for key in REGISTRY}
    if name.lower() in lowered:
        return REGISTRY[lowered[name.lower()]]
    if name in IMPORT_ERRORS:
        raise UnknownExpert(f"{name} failed to import: {IMPORT_ERRORS[name]}")
    raise UnknownExpert(f"No expert named {name!r}. Available: {', '.join(REGISTRY)}")


def build(name: str, **kwargs: Any) -> RoboAgent:
    """Construct one expert instance.

    Always build a *fresh* instance per concurrent task: NOOA holds a lock
    across an agentic method, so a shared instance silently serialises work
    that was meant to run in parallel.
    """
    return get(name)(**kwargs)


def specs() -> list[dict[str, Any]]:
    """Machine-readable roster, for the router prompt and ``roboagents agents``."""
    return [cls.spec() for cls in REGISTRY.values()]


def by_domain(domain: str) -> list[type[RoboAgent]]:
    """Every expert claiming a domain."""
    return [cls for cls in REGISTRY.values() if cls.domain == domain]


def table() -> str:
    """The roster as text, for a routing prompt or the terminal."""
    if not REGISTRY:
        return "No experts loaded."
    width = max(len(name) for name in REGISTRY)
    rows = [f"{name:<{width}}  {cls.charter}" for name, cls in REGISTRY.items()]
    if IMPORT_ERRORS:
        rows.append("")
        rows += [f"FAILED TO LOAD: {name} — {err}" for name, err in IMPORT_ERRORS.items()]
    return "\n".join(rows)


def __iter__() -> Iterator[type[RoboAgent]]:  # pragma: no cover - convenience
    return iter(REGISTRY.values())
