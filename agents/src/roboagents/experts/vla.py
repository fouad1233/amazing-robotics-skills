# SPDX-License-Identifier: Apache-2.0
"""Vision-language-action expert."""

from __future__ import annotations

import os
import re
import shlex
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar

from ..base import RoboAgent
from ..llm import Tier

#: A single reliable probe, run once with the venv's own interpreter. Checking
#: torch with the wrong interpreter (bare `python3`, the system one) is the
#: single most common false "missing" report this class of task produces —
#: the answer looks identical whether the package is truly absent or just
#: absent from *that* interpreter. Route everything through this instead of
#: letting the model improvise a chain of ad hoc shell probes: a multi-step
#: exploration a weaker local model has to get exactly right, every step, is
#: exactly where it drops an `await` or gives up and reports "missing".
_READINESS_PROBE = (
    "import importlib.util, sys\n"
    "print('python', sys.version.split()[0])\n"
    "print('torch', 'present' if importlib.util.find_spec('torch') else 'ABSENT')\n"
    "try:\n"
    "    import torch\n"
    "    print('torch_version', torch.__version__)\n"
    "    print('cuda_available', torch.cuda.is_available())\n"
    "except Exception as exc:\n"
    "    print('torch_import_error', repr(exc))\n"
)

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

_VAR_REF = re.compile(r"\$\{(\w+)\}|\$(\w+)")


def _expand_shell_vars(value: str, known: dict[str, str]) -> str:
    """Resolve `$VAR`/`${VAR}` against already-parsed pins, then `$HOME`/env.

    A raw captured value like ``"$HOME/robotics/models/InternVL3-1B"`` is not a
    path — it is a shell expression. Treating the literal string as a
    filesystem path reports a real, present directory as MISSING, which is
    exactly the false-negative this method exists to prevent.
    """
    def repl(match: re.Match[str]) -> str:
        name = match.group(1) or match.group(2)
        if name in known:
            return known[name]
        if name == "HOME":
            return str(Path.home())
        return os.environ.get(name, match.group(0))

    previous: str | None = None
    while previous != value:
        previous = value
        value = _VAR_REF.sub(repl, value)
    return value


class VLAAgent(RoboAgent):
    """Vision-language-action researcher. This is the thesis work; be exact.

    Source ~/robotics/TIC-VLA/env.sh before any TIC-VLA command — it selects
    Isaac Sim 5.0.0 over the 5.1.0 installed beside it and Isaac Lab v2.2.1, and
    the wrong pair fails inside a compiled module far from the real cause.
    Train on GPU1 and leave GPU0 for simulation, and prefer two independent
    single-GPU runs to DDP: these cards have no NVLink and no peer-to-peer.
    Report the metric a run actually printed, never the one you expected.

    Asked to run TIC-VLA: call `self.tic_vla_readiness()` first — one reliable
    check of the real paths, not a guess from the system interpreter — then
    `self.benchmark_command(...)` for the exact command to run it. "CoT" is
    already baked into the released checkpoint; there is no separate flag for
    it, only `--debug_print` to surface the reasoning as it runs. Isaac Sim
    5.0.0 is confirmed working on this machine's current driver — do not
    report a driver mismatch as the cause of a failure without checking
    IsaacSimAgent's `driver_compatibility()` first.

    A CUDA OOM during a closed-loop benchmark is a placement bug, not a
    too-big model: the scene, not the checkpoint, fills the card. Read
    `self.gpu_split()` and put inference on the card the simulator is not
    rendering on, then re-run — do not report it as a hardware limit.

    `gpu_split()`'s GPU0-for-simulation default assumes a clean card. It is
    not: this whole session is driven by an Ollama model, and Ollama commonly
    keeps its weights resident on GPU0. Call `self.env.gpu()` before building
    `self.benchmark_command(...)` — if GPU0 is already carrying enough that
    Isaac Sim's ~25 GiB renderer footprint would not fit beside it, swap the
    roles for this run (`gpu=1`, `inference_device="cuda:0"`) rather than
    running the default and letting it OOM on a collision you could see coming.
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
            "",
            (
                "Closed-loop evaluation puts a model and a simulator in ONE process, and "
                "they compete for one card. Measured 2026-08-13: the DynaNav office scene "
                "left Isaac Sim holding 25.4 GiB of GPU0's 31.35 GiB, and loading the "
                "TIC-VLA checkpoint onto the same device died with "
                "'CUDA out of memory. Tried to allocate 260.00 MiB'. The checkpoint is "
                "only ~1.8 GiB — the model is not what is large, the scene is."
            ),
            (
                "So split them: simulation on GPU0, inference on GPU1 "
                "(TICVLA_MODEL_DEVICE=cuda:1). The x4 link does not matter here — per step "
                "it carries one camera image over and nine floats back. Read an OOM in a "
                "closed-loop run as a placement bug, not as 'the model does not fit'."
            ),
            "",
            (
                "That default assumes GPU0 is otherwise empty. It usually is not — call "
                "`self.env.gpu()` and check, before trusting it. This very session runs on an "
                "Ollama model, and Ollama keeps its weights wherever it started, commonly "
                "GPU0; ~12 GiB of Ollama plus Isaac Sim's ~25 GiB renderer footprint does not "
                "fit in 32 GiB. If GPU0 is already carrying real memory when you check, swap "
                "roles for this run instead of running the default into a foreseeable OOM: "
                "`self.benchmark_command(gpu=1, inference_device='cuda:0')` puts the empty "
                "card under Isaac Sim and the checkpoint beside whatever is already on GPU0."
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

    async def tic_vla_readiness(self) -> str:
        """One reliable answer for "is TIC-VLA ready to run", instead of a
        chain of shell commands the model has to improvise correctly.

        Checks, against the exact paths ``env.sh`` pins — not guessed paths,
        not the system interpreter: the `tic-vla` venv's own torch and CUDA,
        the InternVL3-1B base model directory, the TIC-VLA checkpoint file,
        and the DynaNav benchmark runner and its `generate_commands.py`. TIC-VLA
        is trained on CoT-annotated data already — "CoT" here is baked into the
        checkpoint, not a runtime flag, so there is nothing to enable beyond
        pointing the benchmark at it. Call this BEFORE reporting anything as
        missing; a claim of "torch not found" or "model missing" that did not
        come from this method's output is very likely checking the wrong place.
        """
        root = self.workdir / "TIC-VLA"
        rows = [f"TIC-VLA repo: {'present' if root.is_dir() else 'MISSING'} at {root}"]

        env_sh = root / "env.sh"
        pins: dict[str, str] = {}
        if env_sh.is_file():
            # env.sh exports build on each other (TICVLA_DYNANAV_ROOT expands
            # ${TICVLA_ROOT}, for instance), so each value is resolved against
            # what has already been parsed, in file order, before moving on.
            for verb, name, value in _PIN.findall(env_sh.read_text(errors="replace")):
                if verb == "export" and value.strip():
                    pins[name] = _expand_shell_vars(value.strip().strip('"'), pins)
            rows.append(f"env.sh: present, {len(pins)} exports")
        else:
            rows.append(f"env.sh: MISSING at {env_sh}")

        venv_python = Path.home() / "envs" / "tic-vla" / "bin" / "python"
        if venv_python.is_file():
            probe = await self.env._sh(f"{shlex.quote(str(venv_python))} -c {shlex.quote(_READINESS_PROBE)}")
            rows.append(f"tic-vla venv ({venv_python}):")
            rows.extend(f"  {line}" for line in probe.strip().splitlines())
        else:
            rows.append(f"tic-vla venv: MISSING interpreter at {venv_python}")

        base_model = Path(pins.get("TICVLA_BASE_MODEL_PATH", str(self.workdir / "models" / "InternVL3-1B")))
        if base_model.is_dir():
            n = sum(1 for _ in base_model.iterdir())
            rows.append(f"base model: present, {n} files at {base_model}")
        else:
            rows.append(f"base model: MISSING at {base_model}")

        ckpt = Path(pins.get("TICVLA_CHECKPOINT_PATH", str(self.workdir / "models" / "TIC-VLA-model.ckpt")))
        if ckpt.is_file():
            rows.append(f"checkpoint: present, {self._human(ckpt.stat().st_size)} at {ckpt}")
        else:
            rows.append(f"checkpoint: MISSING at {ckpt}")

        dynanav = root / "DynaNav"
        for name in ("benchmark.py", "run_benchmark.sh", "generate_commands.py"):
            path = dynanav / name
            rows.append(f"DynaNav/{name}: {'present' if path.is_file() else 'MISSING'}")

        return "\n".join(rows)

    def benchmark_command(
        self,
        config: str = "benchmark_local.yaml",
        gpu: int = 0,
        inference_device: str = "cuda:1",
    ) -> str:
        """The exact, safe command to run the DynaNav/TIC-VLA benchmark.

        Returns the command; it does not run it. `--debug_print` is what
        surfaces the model's step-by-step reasoning during the run — that is
        the "thinking" visualisation, not a separate flag. GPU is pinned with
        `--/physics/cudaDevice=N`, never `CUDA_VISIBLE_DEVICES`: Kit warns at
        startup that setting it for a process that launches Isaac Sim can
        cause crashes.

        The two devices must differ. Simulation and inference share one process
        here, and the scene is the memory hog — see `self.gpu_split()` for the
        measurement. `run_benchmark.sh` already builds this correctly; this
        method exists so the model does not have to reconstruct it by hand and
        risk reintroducing either unsafe form.
        """
        script = self.workdir / "TIC-VLA" / "DynaNav" / "run_benchmark.sh"
        cfg = config if config.startswith("/") else f"DynaNav/configs/{config}"
        if inference_device == f"cuda:{int(gpu)}":
            return (
                f"# refusing to build this: inference_device={inference_device} is the same "
                f"card Isaac Sim renders on (gpu={gpu}). That is the CUDA OOM of 2026-08-13. "
                f"Call self.gpu_split() for why, then pass a different inference_device."
            )
        return (
            f"cd {shlex.quote(str(self.workdir / 'TIC-VLA'))} && source env.sh && "
            f"TICVLA_GPU={int(gpu)} TICVLA_MODEL_DEVICE={shlex.quote(inference_device)} "
            f"{shlex.quote(str(script))} {shlex.quote(cfg)} --debug_print"
        )

    # -- internals -------------------------------------------------------

    @staticmethod
    def _human(size: int) -> str:
        value = float(size)
        for unit in ("B", "K", "M", "G"):
            if value < 1024.0 or unit == "G":
                return f"{value:.1f}{unit}"
            value /= 1024.0
        return f"{value:.1f}G"
