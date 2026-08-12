# SPDX-License-Identifier: Apache-2.0
"""LeRobot / leLab expert."""

from __future__ import annotations

import grp
import os
import shlex
from pathlib import Path
from typing import ClassVar

from ..base import RoboAgent
from ..llm import Tier

#: The RTX 5090 is sm_120. Only a CUDA 12.8+ runtime emits sm_120 kernels, and
#: the local-wheel tag for that is ``+cu128``. A cu126 wheel is not a degraded
#: build — it has no sm_120 cubin at all, so it fails at launch, not at import.
REQUIRED_TORCH_CUDA: str = "cu128"

#: Feetech STS3215 servos in the SO-101 run at 1 Mbaud. A wrong baud rate reads
#: as "no servo answered", which looks identical to a dead bus.
SO101_BAUD: int = 1_000_000

#: Packages that tell us an environment is actually set up for arm work.
_PACKAGES: tuple[str, ...] = ("lerobot", "lelab", "feetech-servo-sdk")

_PKG_PROBE = (
    "import importlib.metadata as md\n"
    f"for pkg in {_PACKAGES!r}:\n"
    "    try:\n"
    "        print(pkg, md.version(pkg))\n"
    "    except md.PackageNotFoundError:\n"
    "        pass\n"
)

_TORCH_PROBE = (
    "import torch\n"
    "print(torch.__version__, torch.version.cuda, torch.cuda.is_available())\n"
)

# synchronize() is the point: without it the launch failure surfaces later, in
# whatever unrelated line happens to touch the stream next.
_MATMUL_PROBE = (
    "import torch\n"
    "x = torch.randn(512, 512, device='cuda')\n"
    "y = (x @ x).abs().sum()\n"
    "torch.cuda.synchronize()\n"
    "print('kernel launched on', torch.cuda.get_device_name(0), '->', float(y))\n"
)


class LeRobotAgent(RoboAgent):
    """LeRobot and leLab engineer: datasets, policy training, and SO-101 arms.

    Establish which virtualenv you are in before running anything — the lerobot
    entry points exist in exactly one of them, and on this machine only a
    `+cu128` torch survives a kernel launch. An arm shows up as a serial device
    only while powered and cabled, so a missing port is a hardware statement,
    not a bug to debug in software. Never report a policy as training until you
    have shown a step that printed a loss.
    """

    domain: ClassVar[str] = "lerobot"
    charter: ClassVar[str] = (
        "LeRobot and leLab: recording and converting datasets, training and "
        "evaluating policies, and driving SO-101 arms over USB serial."
    )
    skill_patterns: ClassVar[tuple[str, ...]] = (
        "lerobot",
        "train-policy",
        "xlerobot",
        "lerobot-env-setup",
        "robot-platforms-rerun-lerobot",
    )
    skill_categories: ClassVar[tuple[str, ...]] = ("lerobot", "robot-platforms")
    tier: ClassVar[Tier] = "worker"

    # -- deterministic domain knowledge ----------------------------------

    async def installed(self) -> str:
        """Which virtualenvs have lerobot or leLab, and at what version.

        Call this first, before any lerobot command. The CLI entry points are
        installed into one venv only; invoking them from another produces an
        import error deep inside a module rather than an honest "not
        installed", and that sends you looking for a packaging bug that is not
        there. Environments with none of these packages are listed as absent.
        """
        rows = []
        for python in self._venv_pythons():
            found = await self.env._sh(f"{shlex.quote(str(python))} -c {shlex.quote(_PKG_PROBE)}")
            clean = "; ".join(line.strip() for line in found.splitlines() if line.strip())
            rows.append(f"{python.parents[1].name}: {clean or 'absent'}")
        return "\n".join(rows) or "No virtualenvs found under ~/envs or the robotics root."

    def serial_ports(self) -> str:
        """SO-101 serial ports present right now, and whether this user may open them.

        Feetech servos reach the host through a USB-serial bridge, so each arm
        appears as /dev/ttyACM* (native USB CDC) or /dev/ttyUSB* (FTDI/CH340
        dongle) — and only while powered and plugged in. An empty list means no
        arm is connected; do not go looking for a driver. Ports present without
        dialout membership means every open() returns PermissionError, and
        adding the group takes effect only after a full logout, not in a new
        shell.
        """
        ports = sorted(Path("/dev").glob("ttyACM*")) + sorted(Path("/dev").glob("ttyUSB*"))
        rows = []
        for port in ports:
            info = port.stat()
            rows.append(
                f"{port}  group={self._group_name(info.st_gid)}  "
                f"mode={info.st_mode & 0o777:o}"
            )
        if not rows:
            rows.append("No /dev/ttyACM* or /dev/ttyUSB* — no arm is powered and cabled.")

        if "dialout" in self._groups():
            rows.append("dialout: member — ports are openable.")
        else:
            rows.append(
                "dialout: NOT a member — every port open will fail with PermissionError. "
                "Fix with `sudo usermod -aG dialout $USER`, then log out and back in."
            )
        rows.append(f"SO-101 servo bus baud rate: {SO101_BAUD}")
        return "\n".join(rows)

    async def torch_build(self, env: str = "") -> str:
        """torch version and CUDA build per virtualenv, judged against sm_120.

        This is the check that catches the most expensive failure on this
        machine. The RTX 5090 is sm_120 and needs a CUDA 12.8+ runtime; the
        default PyPI wheel is cu126, which imports cleanly, reports
        `is_available() == True`, and then aborts at the first kernel launch
        with "no kernel image is available for execution on the device".
        Anything reported as WRONG BUILD must be reinstalled from
        download.pytorch.org/whl/cu128 before training, and before installing
        anything that pins torch. Pass `env` to check a single venv by name.
        """
        rows = []
        for python in self._venv_pythons():
            name = python.parents[1].name
            if env and env != name:
                continue
            raw = await self.env._sh(f"{shlex.quote(str(python))} -c {shlex.quote(_TORCH_PROBE)}")
            rows.append(f"{name}: {self._torch_verdict(raw)}")
        if not rows:
            return f"No virtualenv named {env!r}." if env else "No virtualenvs found."
        return "\n".join(rows)

    async def kernel_check(self, env: str = "") -> str:
        """Launch a real GPU matmul and report whether a kernel actually ran.

        `torch.cuda.is_available()` is not evidence here — a cu126 wheel passes
        it and still cannot execute an sm_120 kernel. This allocates on the
        device, multiplies, and synchronises, which is the cheapest operation
        that fails the same way a training step would. Use its output as the
        evidence that an environment is usable; do not claim a GPU works
        without it. Pass `env` to check one venv by name.
        """
        rows = []
        for python in self._venv_pythons():
            name = python.parents[1].name
            if env and env != name:
                continue
            raw = await self.env._sh(
                f"{shlex.quote(str(python))} -c {shlex.quote(_MATMUL_PROBE)}", timeout=180.0
            )
            text = raw.strip()
            if "ModuleNotFoundError" in text:
                rows.append(f"{name}: torch absent")
            elif "kernel launched on" in text:
                rows.append(f"{name}: OK — {text.splitlines()[-1]}")
            else:
                rows.append(f"{name}: FAILED — {self._last_error(text)}")
        if not rows:
            return f"No virtualenv named {env!r}." if env else "No virtualenvs found."
        return "\n".join(rows)

    # -- internals -------------------------------------------------------

    def _venv_pythons(self) -> list[Path]:
        # ~/robotics/envs is a symlink to ~/envs, so dedupe on the resolved venv
        # directory or every environment gets probed, and reported, twice. The
        # interpreter path itself must stay unresolved: bin/python is a symlink
        # to the base interpreter, and following it drops the venv's
        # site-packages, which is the only thing we are trying to read.
        seen: dict[Path, Path] = {}
        for base in (Path.home() / "envs", self.workdir / "envs"):
            if not base.is_dir():
                continue
            for candidate in sorted(base.glob("*/bin/python")):
                venv = candidate.parents[1]
                if (venv / "pyvenv.cfg").is_file():
                    seen.setdefault(venv.resolve(), candidate)
        return [seen[key] for key in sorted(seen)]

    @staticmethod
    def _group_name(gid: int) -> str:
        try:
            return grp.getgrgid(gid).gr_name
        except KeyError:
            return str(gid)

    @classmethod
    def _groups(cls) -> set[str]:
        return {cls._group_name(gid) for gid in os.getgroups()}

    @staticmethod
    def _torch_verdict(raw: str) -> str:
        text = raw.strip()
        if not text:
            return "probe produced no output"
        if "ModuleNotFoundError" in text:
            return "torch absent"
        fields = text.splitlines()[-1].split()
        if len(fields) != 3:
            return f"probe failed: {text.splitlines()[-1][:120]}"
        version, cuda, available = fields
        tag = version.partition("+")[2] or "cpu-or-untagged"
        base = f"torch {version} (cuda {cuda}, is_available={available})"
        if tag != REQUIRED_TORCH_CUDA:
            return (
                f"{base} — WRONG BUILD for sm_120: {tag} has no sm_120 cubin, so it "
                f"dies at the first kernel launch, not at import. Reinstall {REQUIRED_TORCH_CUDA}."
            )
        return f"{base} — ok for sm_120"

    @staticmethod
    def _last_error(text: str) -> str:
        lines = [line for line in text.splitlines() if line.strip()]
        return lines[-1][:300] if lines else "no output"
