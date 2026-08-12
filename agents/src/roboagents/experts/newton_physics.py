# SPDX-License-Identifier: Apache-2.0
"""Newton and PhysX expert."""

from __future__ import annotations

import shlex
from pathlib import Path
from typing import ClassVar

from ..base import RoboAgent
from ..llm import Tier

#: Run through a Python interpreter to report Newton and its Warp backend.
#: Written to survive a missing module: "not importable" is a real answer and
#: far more useful than a traceback that hides the second import.
_VERSION_PROBE = """
for name in ("newton", "warp"):
    try:
        module = __import__(name)
    except Exception as exc:
        print(f"{name}: not importable ({type(exc).__name__}: {exc})")
        continue
    version = getattr(module, "__version__", None)
    if version is None:
        version = getattr(getattr(module, "config", None), "version", "unknown")
    print(f"{name}: {version} from {getattr(module, '__file__', '?')}")
"""

#: Ordered by how often each is the real cause, cheapest check first. Change one
#: at a time: a fix that lands while three knobs moved together teaches nothing
#: and usually costs simulation speed for a problem that was elsewhere.
_STABILITY_CHECKS: tuple[tuple[str, str], ...] = (
    (
        "timestep (dt)",
        (
            "Rigid bodies tolerate 1/60 s; stiff contact, fast joints and small "
            "links want 1/120 or 1/240. If halving dt fixes it the solver was "
            "never diverging, it was under-resolved — which points at the next item."
        ),
    ),
    (
        "substeps",
        (
            "Physics substeps per env step. Usually the right answer instead of a "
            "smaller dt, because the env and the renderer keep stepping at the "
            "original rate while only physics gets finer. Cost is linear."
        ),
    ),
    (
        "solver iterations",
        (
            "Position iterations first, velocity iterations second — position ones "
            "resolve penetration and drive error, velocity ones only damp bounce. "
            "Defaults around 4/1 are fine for a free-floating body and far too few "
            "for a loaded arm; 8/1 to 16/1 is the working range. Past about 32 you "
            "are paying on every step to hide a modelling error."
        ),
    ),
    (
        "mass and inertia ratios",
        (
            "Ratio between connected links along an articulation. Above roughly "
            "10:1 no iteration count saves you — the constraint is ill-conditioned "
            "by construction. Fix it by raising the light link's mass or inflating "
            "its inertia tensor, not by turning up the solver. A link with a "
            "near-zero or default-authored inertia tensor is the classic instance."
        ),
    ),
    (
        "contact and rest offset",
        (
            "Contact offset must exceed rest offset. Too small and contacts are "
            "only found after interpenetration, so the solver pushes out "
            "explosively — this is what 'the robot flew away on the first frame' "
            "actually is. Both scale with scene units, so a centimetre-authored "
            "asset needs them rescaled."
        ),
    ),
    (
        "joint drive gains and armature",
        (
            "Add joint armature (rotor inertia) before raising damping. Armature "
            "conditions a high-stiffness drive without bleeding energy out of the "
            "system, whereas damping buys stability by making the robot sluggish "
            "and changes the policy you end up training."
        ),
    ),
    (
        "GPU buffer capacity",
        (
            "PhysX GPU pipeline buffers — found-lost pairs, rigid contact and patch "
            "counts. Overflow is a warning in the log, not a crash: contacts are "
            "silently dropped and objects sink through each other. Check the log "
            "for capacity warnings before believing the collision setup is wrong."
        ),
    ),
)


class NewtonPhysicsAgent(RoboAgent):
    """Physics engineer for Newton and PhysX solver behaviour.

    When a simulation explodes, jitters or drifts, work `solver_checklist()` in
    order and change one thing per run — raising solver iterations to cover a bad
    mass ratio buys a slower simulation that is still wrong. Newton is a Warp
    program, so check the Warp build and driver before the solver config; a
    toolkit/driver mismatch surfaces as physics that silently does nothing. Read
    the real values out of the USD or env cfg rather than assuming the defaults.
    """

    domain: ClassVar[str] = "newton"
    charter: ClassVar[str] = (
        "Physics behaviour and stability: PhysX and Newton solver settings, articulation "
        "and joint drive tuning, contact offsets, mass ratios, GPU physics buffers, and "
        "simulations that explode, jitter, sink or drift."
    )
    skill_patterns: ClassVar[tuple[str, ...]] = (
        "newton",
        "physx",
        "physics-simulation",
        "usd-articulation",
        "clone-environments",
        "tensor-bindings-gpu",
    )
    skill_categories: ClassVar[tuple[str, ...]] = ("newton", "physx")
    tier: ClassVar[Tier] = "worker"

    # -- deterministic domain knowledge ----------------------------------

    async def newton_version(self, python: str = "") -> str:
        """Newton and Warp versions in an interpreter. Newton is a Warp program.

        Pass an interpreter path, or leave it out to probe every virtualenv under
        ~/envs and the robotics root. If nothing reports a Newton version then the
        scene is running on PhysX and Newton solver settings do not apply to it —
        Newton is not part of the Isaac Sim 5.x line at all.
        """
        interpreters = [python] if python else self._interpreters()
        if not interpreters:
            return "No Python interpreters found to probe."

        rows: list[str] = []
        for interpreter in interpreters:
            command = (
                f"{shlex.quote(interpreter)} - <<'ROBOAGENTS_PROBE'\n"
                f"{_VERSION_PROBE}\nROBOAGENTS_PROBE"
            )
            output = await self.env._sh(command, timeout=60.0)
            lines = output.strip().splitlines() or ["(no output)"]
            rows.append(interpreter + "\n    " + "\n    ".join(lines))
        return "\n".join(rows)

    def solver_checklist(self) -> str:
        """The ordered things to check when a simulation is unstable.

        Deterministic and complete — do not improvise a different order. Each
        entry says what the knob does and what a fix there actually proves, so a
        change that works is also a diagnosis. Work top to bottom and stop at the
        first item that explains the behaviour.
        """
        rows = ["Unstable simulation — check in this order, one change per run:"]
        for number, (name, detail) in enumerate(_STABILITY_CHECKS, start=1):
            rows.append(f"{number}. {name}")
            rows.append(f"     {detail}")
        rows.append(
            "If all seven are clean, suspect the asset: a collision mesh with "
            "inverted normals or a self-intersecting convex hull produces contacts "
            "no solver setting can resolve."
        )
        return "\n".join(rows)

    def _interpreters(self) -> list[str]:
        """Virtualenv interpreters worth probing, existing and deduplicated.

        Deduplicated by resolved path: ~/robotics/envs is a symlink to ~/envs on
        this box, so a naive glob probes every interpreter twice.
        """
        found: dict[Path, str] = {}
        for base in (Path.home() / "envs", self.workdir / "envs"):
            if not base.is_dir():
                continue
            for python in sorted(base.glob("*/bin/python")):
                if python.is_file():
                    found.setdefault(python.resolve(), str(python))
        return list(found.values())
