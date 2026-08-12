# SPDX-License-Identifier: Apache-2.0
"""Workstation facts every robotics agent needs before it guesses.

Most wasted agent effort on a robotics box comes from assuming the environment
instead of reading it: which driver is loaded, whether CUDA actually
initialises, which Isaac Sim builds exist, which venv matches which major
version. These are deterministic probes — cheap, exact, and far more reliable
than asking a model to infer them from a log.
"""

from __future__ import annotations

import asyncio
import ctypes
import json
import os
import shutil
from datetime import datetime
from pathlib import Path

from nooa import Skill

from ..config import progress_log

#: CUDA driver API error codes worth naming, because the numbers are opaque.
_CUDA_ERRORS = {
    0: "ok",
    3: "not initialized",
    100: "no CUDA-capable device",
    101: "invalid device",
    304: "OS call failed",
    803: "system driver mismatch — kernel module and userspace libraries "
    "are different versions; reboot after a driver change",
    999: "unknown",
}


class WorkspaceSkill(Skill):
    """Read the machine's real state instead of assuming it.

        print(await self.workspace.gpu())          # driver, CUDA, per-GPU memory
        print(await self.workspace.cuda_health())  # does cuInit() actually succeed
        print(self.workspace.isaac_installs())     # which Isaac Sim builds exist
        print(await self.workspace.which("ros2"))  # is a tool on PATH
        self.workspace.note("what you did")        # append to the progress log

    Call ``cuda_health()`` before blaming a GPU workload for crashing. A driver
    mismatch reports as a segfault deep inside a renderer plugin, which sends
    you hunting in entirely the wrong place.
    """

    def __init__(self, root: Path | str | None = None) -> None:
        # No `content=`: Skill.__init__ reassigns self.__class__ when given obj
        # or content, which would replace this subclass with a bare Skill and
        # silently drop every method below. A subclass passes nothing and lets
        # its own docstring be the guide.
        super().__init__()
        self.root = Path(root).expanduser() if root else Path.home() / "robotics"

    # -- GPU / CUDA ------------------------------------------------------

    async def gpu(self) -> str:
        """`nvidia-smi` summary: driver version, and each GPU's name and memory."""
        if not shutil.which("nvidia-smi"):
            return "nvidia-smi not found — no NVIDIA driver installed."
        result = await self._sh(
            "nvidia-smi --query-gpu=index,name,driver_version,memory.total,"
            "memory.used,utilization.gpu --format=csv,noheader"
        )
        return result.strip() or "nvidia-smi returned nothing."

    async def cuda_health(self) -> str:
        """Call ``cuInit(0)`` for real and report what the driver says.

        ``nvidia-smi`` can look healthy while CUDA is broken, so this is the
        check that matters before running any GPU workload.
        """
        return await asyncio.to_thread(self._cuda_health_sync)

    @staticmethod
    def _cuda_health_sync() -> str:
        try:
            lib = ctypes.CDLL("libcuda.so.1")
        except OSError as exc:
            return f"libcuda.so.1 not loadable: {exc}"

        code = lib.cuInit(0)
        version = ctypes.c_int()
        try:
            lib.cuDriverGetVersion(ctypes.byref(version))
        except (AttributeError, OSError):  # pragma: no cover - very old driver
            version.value = 0

        major, minor = divmod(version.value, 1000)
        cuda = f"CUDA {major}.{minor // 10}"
        if code == 0:
            return f"cuInit ok — driver supports {cuda}"
        return f"cuInit FAILED ({code}: {_CUDA_ERRORS.get(code, 'see CUDA docs')}) — driver {cuda}"

    # -- toolchain -------------------------------------------------------

    def isaac_installs(self) -> str:
        """Isaac Sim builds under the robotics root, with their bundled Python."""
        found = []
        for path in sorted(self.root.glob("isaacsim-*")):
            python = path / "python.sh"
            found.append(f"{path.name}: {'python.sh present' if python.is_file() else 'incomplete'}")
        for path in sorted(self.root.glob("IsaacLab*")):
            found.append(f"{path.name}: isaac lab")
        return "\n".join(found) or f"No Isaac Sim installs under {self.root}"

    def venvs(self) -> str:
        """Python virtualenvs and the interpreter version each was built with."""
        rows = []
        for base in (Path.home() / "envs", self.root / "envs"):
            if not base.is_dir():
                continue
            for env in sorted(base.iterdir()):
                cfg = env / "pyvenv.cfg"
                if not cfg.is_file():
                    continue
                version = next(
                    (
                        line.split("=", 1)[1].strip()
                        for line in cfg.read_text().splitlines()
                        if line.startswith("version")
                    ),
                    "unknown",
                )
                rows.append(f"{env}: python {version}")
        return "\n".join(rows) or "No virtualenvs found."

    async def which(self, tool: str) -> str:
        """Where a command lives and what version it reports."""
        path = shutil.which(tool)
        if not path:
            return f"{tool}: not on PATH"
        version = await self._sh(f"{tool} --version")
        return f"{tool}: {path}\n{version.strip().splitlines()[0] if version.strip() else ''}"

    async def disk(self) -> str:
        """Free space on the filesystem holding the robotics root."""
        return (await self._sh(f"df -h {self.root}")).strip()

    def env_report(self) -> str:
        """Everything above except the async probes, as one JSON blob."""
        return json.dumps(
            {
                "robotics_root": str(self.root),
                "isaac": self.isaac_installs().splitlines(),
                "venvs": self.venvs().splitlines(),
                "cuda": self._cuda_health_sync(),
                "user": os.environ.get("USER", "?"),
            },
            indent=2,
        )

    # -- reporting -------------------------------------------------------

    def note(self, message: str) -> str:
        """Append a timestamped line to the shared progress log."""
        # Local time with an offset: this log is read by a human on this box.
        line = f"[{datetime.now().astimezone():%Y-%m-%d %H:%M:%S %z}] {message}\n"
        log = progress_log()
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a") as handle:
            handle.write(line)
        return f"logged to {log}"

    # -- plumbing --------------------------------------------------------

    @staticmethod
    async def _sh(command: str, timeout: float = 60.0) -> str:
        process = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            out, _ = await asyncio.wait_for(process.communicate(), timeout=timeout)
        except TimeoutError:
            process.kill()
            return f"(timed out after {timeout}s)"
        return out.decode(errors="replace")
