# SPDX-License-Identifier: Apache-2.0
"""Isaac Sim expert."""

from __future__ import annotations

import re
import shlex
from pathlib import Path
from typing import ClassVar

from ..base import RoboAgent
from ..llm import Tier

#: An Isaac Sim line has a driver CEILING as well as a floor. This is empirical,
#: measured on this hardware with identical headless smoke tests — it is not
#: derived from any documented compatibility matrix.
#:
#: `known_bad_above` is the highest driver CUDA version confirmed WORKING; above
#: it, the highest version confirmed BROKEN is recorded in the note. Anything in
#: between is untested, and is reported as such rather than guessed at.
#:
#: On the mechanism: the crash logs a Warp entry-point error for
#: `cuDeviceGetUuid` just before dying, and it is tempting to blame CUDA 13 for
#: removing it. That was tested directly and disproved — the symbol resolves on
#: both drivers, and Warp initialises after printing the error. The segfault
#: lands in the RTX renderer (`librtx.scenedb.plugin.so`) and its actual cause
#: is still unidentified. Do not repeat the Warp explanation as if it were known.
#: 2026-08-13: file-marker smoke test (not print(), which Kit's own logging
#: redirect swallows) — app created, 30/30 app.update() steps completed,
#: exit code 0. Overturns the earlier "untested at 13.0" note.
_CONFIRMED_5X_AT_13_0 = (
    "SIGSEGV confirmed on driver CUDA 13.2; CONFIRMED WORKING on 13.0 (580.173.02) "
    "-- 30-step headless smoke test, clean exit 0, 2026-08-13"
)

CUDA_WINDOW: dict[str, tuple[float, float, str]] = {
    "5.0": (12.8, 13.0, _CONFIRMED_5X_AT_13_0),
    "5.1": (12.8, 13.0, "same line as 5.0; not independently re-tested at 13.0"),
    "6.0": (12.8, 13.9, "clean start/step/close confirmed on driver CUDA 13.2"),
    "6.1": (12.8, 13.9, "same line as 6.0"),
}

#: Isaac Sim major version pins the Python it must be driven with.
PYTHON_FOR_MAJOR = {"5": "3.11", "6": "3.12"}


class IsaacSimAgent(RoboAgent):
    """Isaac Sim engineer. You drive Kit, not a GUI.

    Prefer headless runs and the documented launcher flags. Pin a GPU with
    `--/renderer/multiGpu/enabled=false` and `--/physics/cudaDevice=N`, never
    with CUDA_VISIBLE_DEVICES — Kit warns that it causes crashes. A long pause
    on first run is usually asset streaming from NVIDIA's CDN, not a hang.
    """

    domain: ClassVar[str] = "isaac-sim"
    charter: ClassVar[str] = (
        "Isaac Sim scenes, headless runs, the Kit launcher and its settings, "
        "sensors and cameras, rendering, and Isaac Sim crashes or startup failures."
    )
    skill_patterns: ClassVar[tuple[str, ...]] = (
        "isaac-sim",
        "isaac-camera",
        "headless",
        "isaac-sim-remote",
        "orchestrator",
    )
    skill_categories: ClassVar[tuple[str, ...]] = ("isaac-sim", "omniverse")
    tier: ClassVar[Tier] = "worker"

    # -- deterministic domain knowledge ----------------------------------

    def installs(self) -> str:
        """Isaac Sim builds on this machine, with the Python each one needs."""
        rows = []
        for path in sorted(self.workdir.glob("isaacsim-*")):
            version = path.name.removeprefix("isaacsim-")
            major = version.split(".", 1)[0]
            runnable = "yes" if (path / "python.sh").is_file() else "NO (incomplete)"
            rows.append(
                f"{path.name}  python.sh={runnable}  "
                f"requires Python {PYTHON_FOR_MAJOR.get(major, '?')}"
            )
        return "\n".join(rows) or f"No Isaac Sim installs under {self.workdir}"

    def driver_compatibility(self, driver_cuda: float | None = None) -> str:
        """Check each installed Isaac Sim against the running driver's CUDA version.

        Pass `driver_cuda` (e.g. 13.0) or leave it out to read it from the
        driver. This catches the failure mode where Isaac Sim crashes with a
        segfault inside a renderer plugin and the real cause is that the driver
        is too *new* for the Warp build shipped in that Isaac Sim.
        """
        if driver_cuda is None:
            probe = self.env._cuda_health_sync()
            match = re.search(r"CUDA (\d+)\.(\d+)", probe)
            if not match:
                return f"Could not read the driver's CUDA version: {probe}"
            driver_cuda = float(f"{match.group(1)}.{match.group(2)}")

        rows = [f"driver reports CUDA {driver_cuda}"]
        for path in sorted(self.workdir.glob("isaacsim-*")):
            version = path.name.removeprefix("isaacsim-")
            line = ".".join(version.split(".")[:2])
            window = CUDA_WINDOW.get(line)
            if not window:
                rows.append(f"  {path.name}: unknown version, no compatibility data")
                continue
            low, high, note = window
            if driver_cuda < low:
                rows.append(f"  {path.name}: TOO OLD — needs CUDA >= {low}. {note}")
            elif driver_cuda > high:
                rows.append(
                    f"  {path.name}: RISK — highest driver CUDA confirmed working is {high}. "
                    f"{note}. Run the headless smoke test before trusting it; a failure here "
                    "looks like a random SIGSEGV in a renderer plugin, not a version error."
                )
            else:
                rows.append(f"  {path.name}: ok. {note}")
        return "\n".join(rows)

    def launch_command(
        self,
        script: str,
        version: str = "",
        gpu: int = 0,
        headless: bool = True,
        extra_args: str = "",
    ) -> str:
        """Build the exact command to run a script inside an Isaac Sim build.

        Returns the command; it does not run it. Inspect it, then hand it to
        `self.shell.run(...)`.
        """
        candidates = sorted(self.workdir.glob(f"isaacsim-{version}*" if version else "isaacsim-*"))
        if not candidates:
            return f"# no Isaac Sim {version or ''} install found under {self.workdir}"
        root: Path = candidates[-1]

        parts = [str(root / "python.sh"), shlex.quote(script)]
        if headless:
            parts.append("--/app/window/hideUi=true")
            parts.append("--no-window")
        parts.append("--/renderer/multiGpu/enabled=false")
        parts.append(f"--/physics/cudaDevice={int(gpu)}")
        if extra_args:
            parts.append(extra_args)
        return " ".join(parts)

    async def asset_cache(self) -> str:
        """Size of the Omniverse asset cache.

        A first run that appears frozen is usually streaming assets into this
        directory at a few MB/s. Check that it is growing before calling it a hang.
        """
        return await self.env._sh("du -sh ~/.cache/ov 2>/dev/null || echo 'no cache yet'")
