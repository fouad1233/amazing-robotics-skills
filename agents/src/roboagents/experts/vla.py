# SPDX-License-Identifier: Apache-2.0
"""Vision-language-action expert."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar

from ..base import RoboAgent
from ..llm import Tier

#: What each card is for, and the physical reason. The two GPUs are compute
#: identical; only their links differ, and the link is what decides placement.
GPU_ROLES: dict[int, tuple[str, str]] = {
    0: ("PCIe x16, display attached", "simulation and rendering"),
    1: ("PCIe x4, permanent chipset link", "training"),
}

#: Weight files worth listing. Anything else under models/ is config or tokeniser.
CHECKPOINT_GLOBS: tuple[str, ...] = ("*.ckpt", "*.safetensors")

#: env.sh pins versions with `export`, and deliberately clears one with `unset`.
#: The unset matters as much as the exports, so both are captured.
_PIN = re.compile(r"^(export|unset)\s+([A-Z_][A-Z0-9_]*)(?:=(.*))?$", re.MULTILINE)


class VLAAgent(RoboAgent):
    """Vision-language-action researcher. This is the thesis work; be exact.

    Source ~/robotics/TIC-VLA/env.sh before any TIC-VLA command — it selects
    Isaac Sim 5.0.0 over the 5.1.0 installed beside it and Isaac Lab v2.2.1, and
    the wrong pair fails inside a compiled module far from the real cause.
    Train on GPU1 and leave GPU0 for simulation, and prefer two independent
    single-GPU runs to DDP: these cards have no NVLink and no peer-to-peer.
    Report the metric a run actually printed, never the one you expected.
    """

    domain: ClassVar[str] = "vla"
    charter: ClassVar[str] = (
        "Vision-language-action model training and evaluation: TIC-VLA, "
        "checkpoints, GPU placement, fine-tuning runs and benchmark results."
    )
    skill_patterns: ClassVar[tuple[str, ...]] = (
        "groot",
        "train-policy",
        "cosmos3-post-training",
        "huggingface-vision-trainer",
        "physical-ai-data-factory",
    )
    skill_categories: ClassVar[tuple[str, ...]] = ("robot-platforms", "huggingface")
    tier: ClassVar[Tier] = "worker"

    # -- deterministic domain knowledge ----------------------------------

    def checkpoints(self) -> str:
        """Model weights under the models directory, largest first, with sizes.

        Use this to confirm a checkpoint exists and is complete before you point
        a run at it: a truncated download looks like a valid path and fails at
        load time with an opaque deserialisation error. A file far smaller than
        its siblings is the usual sign of an interrupted transfer.
        """
        root = self.workdir / "models"
        if not root.is_dir():
            return f"No model directory at {root}"

        found: list[tuple[int, Path]] = []
        for pattern in CHECKPOINT_GLOBS:
            for path in root.rglob(pattern):
                if path.is_file():
                    found.append((path.stat().st_size, path))
        if not found:
            return f"No {' or '.join(CHECKPOINT_GLOBS)} under {root}"

        found.sort(key=lambda pair: (-pair[0], str(pair[1])))
        return "\n".join(f"{self._human(size):>9}  {path}" for size, path in found)

    def gpu_split(self) -> str:
        """The GPU allocation this workstation requires, and why it is not a preference.

        Read this before choosing a device or reaching for torchrun. The answer
        follows from the hardware, so it does not change per job.
        """
        rows = [f"GPU{i}: {link} -> {job}" for i, (link, job) in sorted(GPU_ROLES.items())]
        rows += [
            "",
            "Why: the cards are compute-identical, so placement is decided by their links.",
            (
                "GPU0 has the display attached and the x16 slot, and rendering needs both — "
                "Isaac Sim belongs there."
            ),
            (
                "GPU1 sits on a permanent x4 chipset link. That link only ever carries input "
                "batches, and those transfers hide behind DataLoader prefetch "
                "(num_workers>0, pin_memory=True, .to(device, non_blocking=True)), so "
                "training loses effectively nothing to it."
            ),
            "",
            (
                "Do not reach for DDP. GeForce 50-series has no NVLink and no peer-to-peer, "
                "so every all-reduce is copied out to host memory and back — over x4 on one "
                "side. Two independent single-GPU experiments finish more work per hour than "
                "one two-GPU DDP run, and give two results instead of one."
            ),
            (
                "Reserve torchrun for large Isaac Lab RL rollouts, where only gradients "
                "cross PCIe and the rollout itself dominates."
            ),
            "",
            (
                "Pinning: CUDA_VISIBLE_DEVICES=1 is correct for a pure training process. "
                "Never set it for a process that starts Isaac Sim — Kit warns at startup "
                "that it causes crashes; use --/physics/cudaDevice=N and "
                "--/renderer/multiGpu/enabled=false instead."
            ),
        ]
        return "\n".join(rows)

    def env_script(self) -> str:
        """Whether TIC-VLA's env.sh exists, and every version and path it pins.

        Source it before any TIC-VLA command, including through isaaclab.sh.
        Without it, isaaclab.sh falls back to Isaac Sim's bundled interpreter,
        which has no isaaclab installed, and the run picks whichever Isaac Sim
        it finds first. Both failures surface far from their cause. If this
        reports the file missing, stop and say so rather than reconstructing
        the pins by hand.
        """
        path = self.workdir / "TIC-VLA" / "env.sh"
        if not path.is_file():
            return f"{path} is missing — TIC-VLA has no pinned environment on this machine."

        rows = [f"{path} pins:"]
        for verb, name, value in _PIN.findall(path.read_text(errors="replace")):
            if verb == "unset":
                rows.append(f"  unset {name}  (deliberately cleared, not merely absent)")
            else:
                rows.append(f"  {name}={value.strip()}")
        if len(rows) == 1:
            rows.append("  (no export or unset lines found — the file is not what we expect)")
        return "\n".join(rows)

    def runs(self, limit: int = 15) -> str:
        """Training and benchmark run directories under outputs/, newest first.

        Point an evaluation at a run directory only after seeing it here, and
        note the "wrote N files" count: a run directory that exists but holds
        nothing means the job died before its first checkpoint, whatever its
        log claimed on the way past.
        """
        root = self.workdir / "outputs"
        if not root.is_dir():
            return f"No output directory at {root}"

        entries = sorted(
            (p for p in root.iterdir() if not p.name.startswith(".")),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if not entries:
            return f"{root} is empty — no run has written anything."

        rows = []
        for path in entries[:limit]:
            stamp = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC).astimezone()
            when = stamp.strftime("%Y-%m-%d %H:%M")
            count = sum(1 for _ in path.rglob("*")) if path.is_dir() else 1
            rows.append(f"{when}  {path.name}  ({count} files)")
        return "\n".join(rows)

    # -- internals -------------------------------------------------------

    @staticmethod
    def _human(size: int) -> str:
        value = float(size)
        for unit in ("B", "K", "M", "G"):
            if value < 1024.0 or unit == "G":
                return f"{value:.1f}{unit}"
            value /= 1024.0
        return f"{value:.1f}G"
