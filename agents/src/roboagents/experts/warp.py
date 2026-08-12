# SPDX-License-Identifier: Apache-2.0
"""Warp expert."""

from __future__ import annotations

import shlex
import sys
from typing import ClassVar

from ..base import RoboAgent
from ..llm import Tier

#: Run through a Python interpreter to report a Warp build and its CUDA window.
#: ``warp.context`` was made private in Warp 1.13 (it lives in
#: ``warp._src.context``), so both module paths are tried before giving up.
#: ``wp.init()`` is what actually calls into the driver, so its banner on stderr
#: is the evidence — the WorkspaceSkill shell probe merges it into stdout.
_WARP_PROBE = """
import importlib

import warp as wp

wp.init()

runtime = None
for name in ("warp._src.context", "warp.context"):
    try:
        runtime = getattr(importlib.import_module(name), "runtime", None)
    except ImportError:
        continue
    if runtime is not None:
        break

print("warp version:", wp.config.version)
print("built against CUDA:", getattr(runtime, "toolkit_version", "unknown"))
print("driver CUDA:", getattr(runtime, "driver_version", "unknown"))
print("minimum driver:", getattr(runtime, "min_driver_version", "unknown"))
print("kernel cache:", wp.config.kernel_cache_dir)
print("devices:", [str(d) for d in wp.get_devices()])
"""


class WarpAgent(RoboAgent):
    """Warp engineer: kernels, device arrays, and GPU-native pipelines.

    The CUDA toolkit a Warp build was compiled against is a ceiling on the driver
    it will run under, not just a floor — a driver newer than the toolkit fails
    inside `cuInit` with error 803 and reports "CUDA devices not available", or
    crashes inside a renderer plugin. Confirm that pair with `warp_version()`
    before you debug a kernel. Warp compiles kernels ahead of the first launch
    and caches them, so a long first-run pause with `kernel_cache()` growing is
    compilation, not a hang.
    """

    domain: ClassVar[str] = "warp"
    charter: ClassVar[str] = (
        "Warp kernels and GPU-native pipelines: kernel authoring and launch, device "
        "arrays and dtypes, differentiability and gradients, the JIT kernel cache, and "
        "Warp toolkit-versus-driver compatibility."
    )
    skill_patterns: ClassVar[tuple[str, ...]] = (
        "warp",
        "warp-eval",
        "warp-debug-gradients",
        "tensor-bindings-gpu",
        "physics-simulation",
    )
    skill_categories: ClassVar[tuple[str, ...]] = ("warp", "physx")
    tier: ClassVar[Tier] = "worker"

    # -- deterministic domain knowledge ----------------------------------

    async def warp_version(self, python: str = "", warp_path: str = "") -> str:
        """Probe a Warp build: version, the CUDA toolkit it was built against, driver.

        Defaults to this process's interpreter; pass `python` for another venv and
        `warp_path` for a PYTHONPATH entry — take that from `bundled_builds()`,
        because on this machine Warp only exists inside Isaac Sim.

        "built against CUDA" is the number that matters. It sets the driver
        ceiling for every Isaac Sim built on that Warp: Isaac Sim 5.x ships Warp
        1.7/1.8 compiled against CUDA 12.8 and dies on a CUDA 13 driver, while
        6.x ships Warp 1.13 compiled against 12.9 and is fine there. "minimum
        driver" is only the floor, so a run can satisfy it and still fail.
        """
        interpreter = python or sys.executable
        prefix = f"PYTHONPATH={shlex.quote(warp_path)} " if warp_path else ""
        command = (
            f"{prefix}{shlex.quote(interpreter)} - <<'ROBOAGENTS_PROBE'\n"
            f"{_WARP_PROBE}\nROBOAGENTS_PROBE"
        )
        output = await self.env._sh(command, timeout=180.0)
        return f"interpreter: {interpreter}\n{output.strip() or '(no output)'}"

    def bundled_builds(self) -> str:
        """Warp copies shipped inside each Isaac Sim install, with the path to import them.

        No virtualenv on this machine carries Warp of its own: it arrives as the
        ``omni.warp.core`` Kit extension, and even Isaac Sim's own ``python.sh -c
        'import warp'`` fails because Kit only adds that path once an app is
        loading. Pass the PYTHONPATH printed here to
        ``warp_version(warp_path=...)`` together with the Python that matches the
        Isaac Sim line (5.x needs 3.11, 6.x needs 3.12).
        """
        rows: list[str] = []
        for install in sorted(self.workdir.glob("isaacsim-*")):
            for ext in sorted((install / "extscache").glob("omni.warp.core-*")):
                version = ext.name.removeprefix("omni.warp.core-").split("+", 1)[0]
                rows.append(f"{install.name}: warp {version}\n    PYTHONPATH={ext}")
        return "\n".join(rows) or f"No bundled Warp found under {self.workdir}"

    async def kernel_cache(self) -> str:
        """Size of the Warp kernel cache, split per Warp version.

        Warp caches compiled kernels under ~/.cache/warp/<version>. A first launch
        that looks frozen is compiling if this is growing, and genuinely hung if it
        is not. Deleting one version directory forces a clean recompile, which is
        the fix after a toolchain or driver change leaves stale objects behind.
        """
        return await self.env._sh(
            "du -sh ~/.cache/warp/* ~/.cache/warp 2>/dev/null "
            "|| echo 'no Warp kernel cache yet — nothing has compiled a kernel'"
        )
