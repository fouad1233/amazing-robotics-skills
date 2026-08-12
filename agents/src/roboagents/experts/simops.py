# SPDX-License-Identifier: Apache-2.0
"""Platform expert: drivers, CUDA, DKMS, kernel modules and virtualenvs."""

from __future__ import annotations

import re
from pathlib import Path
from typing import ClassVar

from ..base import RoboAgent
from ..llm import Tier

#: The running kernel module reports itself here. Two layouts exist, and the
#: flavour is readable from which one you get:
#:   open   -> "NVIDIA UNIX Open Kernel Module for x86_64  595.84  Release Build"
#:   closed -> "NVIDIA UNIX x86_64 Kernel Module  580.65.06  Tue ..."
NVRM_VERSION_FILE = Path("/proc/driver/nvidia/version")
_NVRM_VERSION = re.compile(r"Kernel Module\s+(?:for\s+\S+\s+)?(\d+(?:\.\d+)+)")

#: ``libcuda.so.1`` is a symlink onto the versioned userspace driver library.
#: The link target is what the dynamic loader actually binds, so resolve it
#: instead of trusting the package database — apt can have unpacked a newer
#: version whose ldconfig link has not moved, or vice versa.
LIBCUDA_LINKS = (
    Path("/usr/lib/x86_64-linux-gnu/libcuda.so.1"),
    Path("/usr/lib64/libcuda.so.1"),
    Path("/usr/lib/libcuda.so.1"),
)
_LIBCUDA_VERSION = re.compile(r"libcuda\.so\.(\d+(?:\.\d+)+)")

#: RTX 5090 is sm_120. A torch wheel built against an older toolkit imports
#: fine and reports is_available() == True, then dies at the first kernel
#: launch — so the CUDA build version, not the import, is what must be checked.
BLACKWELL_CUDA_FLOOR = (12, 8)

#: torch records its own build in version.py as `__version__ = '2.7.0+cu128'`
#: and `cuda: Optional[str] = '12.8'`. Compiled once here rather than per call.
_TORCH_VERSION = re.compile(r"^__version__\s*=\s*['\"]([^'\"]+)", re.MULTILINE)
_TORCH_CUDA = re.compile(r"^cuda\s*(?::[^=]+)?=\s*['\"]([^'\"]+)", re.MULTILINE)


class SimOpsAgent(RoboAgent):
    """Platform engineer for this workstation: drivers, CUDA, DKMS, kernels, venvs.

    Never `modprobe nvidia` into a live desktop session — it pulls nvidia_drm in
    underneath the running compositor and blacks out the screen. After any driver
    change, reboot; do not try to reload the stack in place. Read the real
    versions before theorising: most "random segfault" reports on this box are a
    kernel-module against libcuda mismatch, and the fix is a reboot, not a
    reinstall.
    """

    domain: ClassVar[str] = "simops"
    charter: ClassVar[str] = (
        "NVIDIA drivers and their flavour, CUDA and DKMS, kernel modules, "
        "virtualenvs and torch builds — the layer underneath every other "
        "expert, and the one that turns a working setup into a segfault."
    )
    skill_patterns: ClassVar[tuple[str, ...]] = (
        "torch-install",
        "cosmos3-env-troubleshoot",
        "isaac-sim-troubleshooting",
        "ko-module-build",
        "gpu-selection",
    )
    skill_categories: ClassVar[tuple[str, ...]] = ("ml-infra",)
    #: Planner tier on purpose. This expert is handed symptoms, not procedures —
    #: the same segfault is a driver mismatch, a cu126 wheel or a wrong-Python
    #: venv, and telling them apart is inference rather than following a runbook.
    tier: ClassVar[Tier] = "planner"

    # -- deterministic domain knowledge ----------------------------------

    async def driver_report(self) -> str:
        """The whole NVIDIA stack in one place: nvidia-smi, apt packages, DKMS, verdict.

        Start here for any GPU failure, before reading application logs. The last
        block is the part that matters: if it says MISMATCH then nothing
        GPU-related will work until the machine is rebooted, and reinstalling the
        driver cannot change it because the running kernel keeps the module it
        loaded at boot.
        """
        smi = await self.env._sh(
            "nvidia-smi --query-gpu=index,name,driver_version,memory.total "
            "--format=csv,noheader 2>&1"
        )
        packages = await self.env._sh(
            "dpkg-query -W -f='${db:Status-Abbrev} ${binary:Package} ${Version}\\n' "
            "'nvidia-*' 'libnvidia-compute-*' 2>/dev/null | grep '^ii' || true"
        )
        dkms = await self.env._sh("dkms status 2>&1 || true")

        loaded = self._loaded_module_version()
        userspace = self._userspace_driver_version()
        verdict = [
            f"loaded kernel module: {loaded or 'none loaded'}",
            f"userspace libcuda:    {userspace or 'not found'}",
        ]
        if self.reboot_required():
            verdict.append(
                "MISMATCH -- every CUDA call will return error 803 "
                "(CUDA_ERROR_SYSTEM_DRIVER_MISMATCH). REBOOT. Do not reinstall, "
                "do not modprobe; the running kernel cannot swap the module."
            )
        elif loaded and userspace:
            verdict.append("match -- kernel module and userspace agree.")
        else:
            verdict.append("cannot compare -- one side is unreadable, see the blocks above.")

        return "\n".join(
            [
                "== nvidia-smi ==",
                smi.strip() or "(no output)",
                "",
                "== installed nvidia packages ==",
                packages.strip() or "(none)",
                "",
                "== dkms ==",
                dkms.strip() or "(none)",
                "",
                "== verdict ==",
                *verdict,
            ]
        )

    def reboot_required(self) -> bool:
        """True when the loaded nvidia kernel module and libcuda are different versions.

        This is the single most confusing failure on this box. An apt upgrade
        replaces the userspace libraries and builds a new DKMS module on disk,
        but the running kernel keeps the module it loaded at boot. Every CUDA
        call then fails with error 803, CUDA_ERROR_SYSTEM_DRIVER_MISMATCH, which
        surfaces as `nvidia-smi` printing "Driver/library version mismatch", as a
        torch import that segfaults, or as an Isaac Sim crash deep inside a
        renderer plugin — three symptoms that look unrelated and send you hunting
        in the wrong place. Only a reboot clears it. This also returns False when
        neither version can be read, so call `driver_report()` for the numbers
        before you act on it.
        """
        loaded = _components(self._loaded_module_version())
        userspace = _components(self._userspace_driver_version())
        if not loaded or not userspace:
            return False
        # The two sides are stamped at different precision (595.84 against
        # 580.173.02), so compare only the components both actually carry.
        width = min(len(loaded), len(userspace))
        return loaded[:width] != userspace[:width]

    def venv_matrix(self) -> str:
        """Every virtualenv on this box: Python version, torch version, CUDA build.

        Read this before installing anything or choosing an interpreter. Two
        traps show up here. Isaac Sim 5.x must be driven from a Python 3.11 env
        and 6.x from a Python 3.12 env, never merged. And a torch row marked NOT
        cu128 will import, report is_available() == True, and then die at the
        first kernel launch on sm_120 — the venv looks healthy right up to the
        moment real work starts.
        """
        rows: list[str] = []
        seen: set[Path] = set()
        for base in (Path.home() / "envs", self.workdir / "envs"):
            if not base.is_dir():
                continue
            for env in sorted(base.iterdir()):
                config = env / "pyvenv.cfg"
                # ~/robotics/envs is a symlink onto ~/envs here, so the same venv
                # arrives twice under two names. Report it once.
                if not config.is_file() or env.resolve() in seen:
                    continue
                seen.add(env.resolve())
                python = _venv_python(config)
                rows.append(f"{env}\n    python {python}  |  {_torch_build(env)}")
        if not rows:
            return f"No virtualenvs under {Path.home() / 'envs'} or {self.workdir / 'envs'}."
        return "\n".join(rows)

    def driver_flavour_note(self) -> str:
        """Which NVIDIA driver flavour this box must run, and what is loaded right now.

        Read this before touching apt or proposing a driver version. It states
        the one flavour that works on Blackwell and the one package name that
        does not mean what it says.
        """
        text = _read(NVRM_VERSION_FILE)
        if not text:
            loaded = "no nvidia kernel module is loaded right now"
        elif "Open Kernel Module" in text:
            loaded = f"loaded now: OPEN kernel module {self._loaded_module_version()} -- correct"
        else:
            loaded = (
                f"loaded now: CLOSED kernel module {self._loaded_module_version()} -- WRONG "
                "on Blackwell, it will enumerate zero devices"
            )
        return "\n".join(
            [
                "Driver flavour on this workstation:",
                "  Always the -open flavour. The GPUs are RTX 5090 (sm_120, Blackwell)",
                "  and the closed/proprietary modules do not enumerate Blackwell at all:",
                "  they load without complaint and then report no devices, which reads",
                "  like dead hardware rather than a wrong package.",
                "",
                "  On noble, `nvidia-driver-575-open` is a TRANSITIONAL package. apt",
                "  accepts the name, then pulls in the 580 branch instead. Never read",
                "  the number you typed as the version you got -- confirm against dpkg",
                "  and /proc/driver/nvidia/version afterwards.",
                "",
                f"  {loaded}",
            ]
        )

    # -- internals -------------------------------------------------------

    def _loaded_module_version(self) -> str:
        """Version of the nvidia kernel module the running kernel actually holds."""
        return _search(_NVRM_VERSION, _read(NVRM_VERSION_FILE))

    def _userspace_driver_version(self) -> str:
        """Version of the libcuda the dynamic loader will bind, via the so.1 symlink."""
        for link in LIBCUDA_LINKS:
            if link.exists():
                return _search(_LIBCUDA_VERSION, link.resolve().name)
        return ""


def _read(path: Path) -> str:
    """File contents, or empty string when it is absent or unreadable."""
    try:
        return path.read_text(errors="replace")
    except OSError:
        return ""


def _search(pattern: re.Pattern[str], text: str) -> str:
    match = pattern.search(text)
    return match.group(1) if match else ""


def _components(version: str) -> tuple[int, ...]:
    """``"580.173.02"`` -> ``(580, 173, 2)``. Empty tuple when unparseable."""
    return tuple(int(part) for part in version.split(".") if part.isdigit())


def _venv_python(config: Path) -> str:
    """Interpreter version a venv was built with, from its pyvenv.cfg."""
    for line in _read(config).splitlines():
        if line.startswith("version"):
            return line.split("=", 1)[1].strip()
    return "unknown"


def _torch_build(env: Path) -> str:
    """Describe the torch inside a venv, read from its own version.py.

    Parsed off disk rather than imported: launching every venv's interpreter is
    slow, and a venv whose torch is broken is exactly the one worth reporting.
    """
    candidates = sorted(env.glob("lib/python*/site-packages/torch/version.py"))
    if not candidates:
        return "torch: absent"
    text = _read(candidates[0])
    version = _search(_TORCH_VERSION, text) or "?"
    toolkit = _search(_TORCH_CUDA, text)
    if not toolkit:
        return f"torch {version}: no CUDA in this build (CPU-only or ROCm)"
    tag = "cu" + toolkit.replace(".", "")
    if _components(toolkit) >= BLACKWELL_CUDA_FLOOR:
        return f"torch {version}: {tag} -- ok on sm_120"
    return (
        f"torch {version}: {tag} -- NOT cu128, imports and reports is_available() "
        "True, then dies at the first kernel launch on RTX 5090"
    )
