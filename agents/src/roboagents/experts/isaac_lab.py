# SPDX-License-Identifier: Apache-2.0
"""Isaac Lab expert."""

from __future__ import annotations

import shlex
from pathlib import Path
from typing import ClassVar

from ..base import RoboAgent
from ..llm import Tier

_REWRITE_NOTE = (
    "ground-up rewrite: multi-backend physics (PhysX or Newton), pluggable "
    "renderer, Warp-native pipelines. Do not carry 2.x API assumptions into it."
)

#: Isaac Lab line -> (Isaac Sim line it is built against, Python, note).
#: Isaac Lab ships no simulator of its own; it drives whichever Isaac Sim the
#: ``_isaac_sim`` symlink points at. The pairing is fixed at release time, so a
#: newer Isaac Sim is not an upgrade path — it is an import error waiting to
#: happen inside ``isaaclab.sim``.
LAB_TO_SIM: dict[str, tuple[str, str, str]] = {
    "2.2": ("5.0", "3.11", "manager-based env API, PhysX only"),
    "2.3": ("5.1", "3.11", "manager-based env API, PhysX only"),
    "3.0": ("6.0", "3.12", _REWRITE_NOTE),
}

#: Training entry points that exist under ``scripts/reinforcement_learning/``.
RL_FRAMEWORKS: tuple[str, ...] = ("rsl_rl", "skrl", "rl_games", "sb3")


class IsaacLabAgent(RoboAgent):
    """Isaac Lab engineer. Everything runs through `./isaaclab.sh -p`, never a bare python.

    A bare `python scripts/...` has none of the Kit extension paths and dies in
    the import of `isaaclab.app`; run `./isaaclab.sh -f` before any commit. The
    Isaac Lab version and the Isaac Sim it links to are a matched pair — check
    the pairing before you debug an import error. Isaac Lab 3.0 is a rewrite,
    not an upgrade, so grep the installed tree for an API before using it.
    """

    domain: ClassVar[str] = "isaac-lab"
    charter: ClassVar[str] = (
        "Isaac Lab RL environments and training runs: task registration, manager-based "
        "envs, num_envs scaling, the rsl_rl/skrl/rl_games launchers, and Isaac Lab to "
        "Isaac Sim version pairing."
    )
    skill_patterns: ClassVar[tuple[str, ...]] = (
        "isaac-lab",
        "clone-environments",
        "physics-simulation",
        "usd-articulation",
        "gpu-selection",
    )
    skill_categories: ClassVar[tuple[str, ...]] = ("robot-platforms", "physx")
    tier: ClassVar[Tier] = "worker"

    # -- deterministic domain knowledge ----------------------------------

    def installs(self) -> str:
        """Isaac Lab checkouts on this machine, and the Isaac Sim each is wired to.

        ``_isaac_sim`` is a symlink into an Isaac Sim install, and that link — not
        the documentation — is what a run actually loads. A line flagged MISMATCH
        against ``version_matrix()`` is the usual cause of an import error deep
        inside ``isaaclab.sim``.
        """
        rows: list[str] = []
        for path in sorted(self.workdir.glob("IsaacLab*")):
            if not path.is_dir():
                continue
            version = self._version_of(path)
            line = ".".join(version.split(".")[:2])
            runnable = "yes" if (path / "isaaclab.sh").is_file() else "NO (incomplete)"

            link = path / "_isaac_sim"
            if link.exists():
                target = link.resolve().name
            else:
                target = "MISSING — run ./isaaclab.sh --install"

            rows.append(
                f"{path.name}  VERSION={version}  isaaclab.sh={runnable}  _isaac_sim -> {target}"
            )
            pairing = LAB_TO_SIM.get(line)
            if not pairing:
                rows.append("    unknown Isaac Lab line, no pairing data")
                continue
            sim_line, python, _note = pairing
            detail = f"    expects Isaac Sim {sim_line}.x driven by Python {python}"
            if not target.startswith(f"isaacsim-{sim_line}"):
                detail += "  <-- MISMATCH"
            rows.append(detail)

        return "\n".join(rows) or f"No Isaac Lab checkouts under {self.workdir}"

    def version_matrix(self) -> str:
        """The Isaac Lab to Isaac Sim pairing, and the Python each line requires.

        Read this before installing, upgrading, or blaming a version. The two
        Python lines never merge: Isaac Sim 5.x is a 3.11 stack and 6.x is a 3.12
        stack, and a venv that mixes them is unrecoverable.
        """
        rows = ["Isaac Lab -> Isaac Sim -> Python"]
        for lab in sorted(LAB_TO_SIM):
            sim, python, note = LAB_TO_SIM[lab]
            rows.append(f"  Isaac Lab {lab}.x  ->  Isaac Sim {sim}.x  ->  Python {python}")
            rows.append(f"      {note}")
        return "\n".join(rows)

    def train_command(
        self,
        task: str,
        num_envs: int = 4096,
        headless: bool = True,
        gpu: int = 0,
        framework: str = "rsl_rl",
        version: str = "",
    ) -> str:
        """Build the exact training invocation for an Isaac Lab task.

        Returns the command; it does not run it. Inspect it, then hand it to
        `self.shell.run(...)`.

        Two traps are worth knowing. First, ``isaaclab.sh`` picks
        ``${CONDA_PREFIX}/bin/python`` whenever CONDA_PREFIX is set and only falls
        back to the Kit python otherwise — an unrelated conda env that happens to
        be active silently becomes the interpreter, so deactivate it or confirm it
        is the one paired with this Lab. Second, the run must go through
        ``./isaaclab.sh -p``; a bare ``python`` reaches none of the Kit extension
        paths. Pinning a GPU is ``--device cuda:N``, not CUDA_VISIBLE_DEVICES.
        """
        if framework not in RL_FRAMEWORKS:
            return f"# unknown framework {framework!r}; pick one of {', '.join(RL_FRAMEWORKS)}"

        pattern = f"IsaacLab-{version}*" if version else "IsaacLab*"
        found = sorted(self.workdir.glob(pattern))
        candidates = [p for p in found if (p / "isaaclab.sh").is_file()]
        if not candidates:
            return f"# no Isaac Lab {version or ''} checkout found under {self.workdir}"
        root: Path = candidates[-1]

        script = f"scripts/reinforcement_learning/{framework}/train.py"
        if not (root / script).is_file():
            return f"# {root / script} does not exist in this Isaac Lab"

        parts = [
            "./isaaclab.sh",
            "-p",
            script,
            "--task",
            shlex.quote(task),
            "--num_envs",
            str(int(num_envs)),
            "--device",
            f"cuda:{int(gpu)}",
        ]
        if headless:
            parts.append("--headless")
        return f"cd {shlex.quote(str(root))} && " + " ".join(parts)

    @staticmethod
    def _version_of(root: Path) -> str:
        version_file = root / "VERSION"
        if not version_file.is_file():
            return "unknown"
        return version_file.read_text(errors="replace").strip() or "unknown"
