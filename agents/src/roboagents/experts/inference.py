# SPDX-License-Identifier: Apache-2.0
"""Inference expert: ONNX export, TensorRT, quantisation, edge deployment."""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from ..base import RoboAgent
from ..llm import Tier

#: The packages worth knowing about before proposing an export or build command.
#: `onnx` writes a graph but cannot check it; parity needs `onnxruntime`, and
#: shape or layer debugging needs `polygraphy`, which ships with TensorRT rather
#: than with onnx — so a venv holding only `onnx` cannot verify its own output.
ONNX_STACK: tuple[str, ...] = (
    "torch",
    "onnx",
    "onnxruntime",
    "onnxruntime-gpu",
    "onnxslim",
    "onnx-simplifier",
    "onnx-graphsurgeon",
    "polygraphy",
    "tensorrt",
    "torch-tensorrt",
)

#: Hard-won: every line here is a failure that a standard accuracy metric hid.
PRECISION_NOTE = """\
Precision on a VLA policy — what it costs and where:

  bf16 -> fp16 is not a free cast. VLA backbones (SigLIP or DINOv2 towers, an
  LLM decoder) are trained in bf16, which carries fp32's exponent range. fp16
  has five exponent bits; activations that were comfortable in bf16 overflow to
  inf. Check for inf per layer after the cast before blaming the engine builder.

  fp16 is usually safe on the vision tower and the language backbone, and unsafe
  on the action head. Action deltas are small numbers near zero and the head's
  output range is narrow enough that the quantisation step swallows the signal.
  Keep the final action projection and the layernorms in fp32.

  Error compounds over the chunk. A VLA emits an action chunk that is executed
  open-loop. A per-step bias too small to see in a single-step metric is visible
  drift by step 30-50. Evaluate over a whole episode, not one forward pass.

  int8 is a calibration problem, not a build flag. The calibration set must come
  from the deployment scene — same camera, same lighting, same table. Calibrate
  on training renders, deploy on real frames, and you get an engine that scores
  well offline and misses the grasp.

  Quantise from the front. int8 on the vision encoder buys most of the latency
  because that is where the FLOPs are; int8 on the action head buys little and
  costs the most accuracy.

  Pin precision per layer with TensorRT strong typing instead of letting the
  builder's autotuner choose. Otherwise the layer mix changes between builds and
  the accuracy you measured is not the accuracy you ship.

  Measure with task success rate over real rollouts. Action or logit MSE is not
  monotone with success rate, so an MSE that barely moved is not evidence that
  the policy still works."""


class InferenceAgent(RoboAgent):
    """Deployment engineer for trained policies: ONNX export, TensorRT, quantisation.

    An engine is built for exactly one TensorRT version, one GPU architecture and
    one shape profile — build it on the machine that will run it and never ship a
    .plan between boxes. Export to ONNX and check numerical parity against the
    PyTorch graph before touching precision, so an accuracy regression can be
    blamed on the export or on the quantisation rather than on both at once.
    Judge a VLA policy by task success over full episodes, never by action MSE.
    """

    domain: ClassVar[str] = "inference"
    charter: ClassVar[str] = (
        "ONNX export, TensorRT engine building and profiling, fp16 and int8 "
        "quantisation, and making a trained VLA policy run fast enough to close "
        "the loop at the edge."
    )
    skill_patterns: ClassVar[tuple[str, ...]] = (
        "trt-onnx-quickstart",
        "trt-torch-quickstart",
        "trt-perf-analysis",
        "tensorrt",
        "onnx",
    )
    skill_categories: ClassVar[tuple[str, ...]] = ("inference",)
    tier: ClassVar[Tier] = "worker"

    # -- deterministic domain knowledge ----------------------------------

    async def trt_version(self) -> str:
        """Where TensorRT lives on this box, if anywhere: trtexec, apt, and per venv.

        Run this before writing any build command. TensorRT arrives by three
        unrelated routes — the apt repo (which provides /usr/src/tensorrt and
        trtexec), a pip wheel inside one venv, and whatever Isaac Sim bundles —
        and those routes do not share a version. An engine built by one of them
        will refuse to deserialise in another, reporting a version mismatch that
        reads like a corrupt file.
        """
        trtexec = await self.env._sh("command -v trtexec 2>/dev/null || echo 'not on PATH'")
        packages = await self.env._sh(
            "dpkg-query -W -f='${db:Status-Abbrev} ${binary:Package} ${Version}\\n' "
            "'tensorrt*' 'libnvinfer*' 2>/dev/null | grep '^ii' || true"
        )
        samples = "present" if Path("/usr/src/tensorrt").is_dir() else "absent"

        rows = [
            "== command line ==",
            f"trtexec: {trtexec.strip() or 'not on PATH'}",
            f"/usr/src/tensorrt: {samples}",
            "",
            "== apt packages ==",
            packages.strip() or "(none — TensorRT was not installed from the apt repo)",
            "",
            "== python packages, per venv ==",
        ]
        rows.extend(self._per_venv(("tensorrt", "torch-tensorrt", "polygraphy")))
        return "\n".join(rows)

    def onnx_tools(self) -> str:
        """Which ONNX and TensorRT Python packages each venv holds, and their versions.

        Read from *.dist-info on disk, so it launches nothing and works even when
        a venv is broken. Use it to choose the venv to export from. Watch for the
        common gap: a venv with `onnx` but no `onnxruntime` can write a graph and
        cannot prove the graph is correct, which is how a bad export reaches the
        engine builder and gets blamed on TensorRT.
        """
        rows = self._per_venv(ONNX_STACK)
        return "\n".join(rows)

    def precision_note(self) -> str:
        """What fp16 and int8 actually cost a VLA policy, and where the cost lands.

        Read this before proposing a precision change. The headline is that the
        usual proxy metrics do not predict the thing you care about: action MSE
        stays flat while task success falls, because the error lands in the part
        of the network with the smallest dynamic range.
        """
        return PRECISION_NOTE

    # -- internals -------------------------------------------------------

    def _per_venv(self, packages: tuple[str, ...]) -> list[str]:
        """One line per venv listing which of `packages` it has installed."""
        rows: list[str] = []
        seen: set[Path] = set()
        for base in (Path.home() / "envs", self.workdir / "envs"):
            if not base.is_dir():
                continue
            for env in sorted(base.iterdir()):
                # ~/robotics/envs is a symlink onto ~/envs here; report each once.
                if not (env / "pyvenv.cfg").is_file() or env.resolve() in seen:
                    continue
                seen.add(env.resolve())
                found = _dist_versions(env, packages)
                body = ", ".join(f"{n} {v}" for n, v in sorted(found.items())) or "(none)"
                rows.append(f"{env}: {body}")
        return rows or ["No virtualenvs found."]


def _dist_versions(env: Path, packages: tuple[str, ...]) -> dict[str, str]:
    """Installed versions of the named distributions inside a venv.

    Read off ``*.dist-info`` directory names so nothing has to be imported and
    that venv's interpreter never has to be launched.
    """
    wanted = {_normalise(name) for name in packages}
    found: dict[str, str] = {}
    for info in env.glob("lib/python*/site-packages/*.dist-info"):
        name, _, version = info.name.removesuffix(".dist-info").rpartition("-")
        key = _normalise(name)
        if key in wanted:
            found[key] = version
    return found


def _normalise(name: str) -> str:
    """PEP 503-ish name folding, so `onnx-graphsurgeon` matches `onnx_graphsurgeon`."""
    return name.replace("-", "_").replace(".", "_").lower()
