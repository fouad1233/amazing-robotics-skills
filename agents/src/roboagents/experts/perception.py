# SPDX-License-Identifier: Apache-2.0
"""Perception expert."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import ClassVar

from ..base import RoboAgent
from ..llm import Tier

#: Keys that mark a YAML file as a camera calibration rather than a config file.
#: The first four are the ROS camera_info format; the last two cover the OpenCV
#: and Kalibr files that come out of the usual calibration tools.
_CALIBRATION_KEYS = (
    "camera_matrix",
    "distortion_coefficients",
    "projection_matrix",
    "rectification_matrix",
    "camera_model",
    "intrinsics",
)

#: Directories never worth walking when hunting for calibration files.
_PRUNE = {
    ".git",
    "__pycache__",
    "build",
    "install",
    "log",
    "exts",
    "extscache",
    "extsDeprecated",
    "extsUser",
    "kit",
    "site-packages",
    "node_modules",
    ".venv",
    "datasets",
}


class PerceptionAgent(RoboAgent):
    """Perception engineer: cameras, calibration, and the data that comes off them.

    Intrinsics belong to the lens and sensor, extrinsics to the mounting — never
    re-solve one because the other changed, and never reuse a calibration at a
    resolution it was not solved at unless you scale fx, fy, cx and cy with it.
    A rendered camera has exact, known intrinsics, so treat a synthetic-data
    mismatch as a convention bug — OpenCV versus USD camera axes, or metres
    versus stage units — rather than as a calibration problem. State which frame
    and which convention every number you report is in.
    """

    domain: ClassVar[str] = "perception"
    charter: ClassVar[str] = (
        "Cameras and sensors, intrinsic and extrinsic calibration, image and depth "
        "pipelines, and synthetic data generation from simulated sensors."
    )
    skill_patterns: ClassVar[tuple[str, ...]] = (
        "isaac-camera",
        "isaac-sim-sensor",
        "robot-perception",
        "data-collection-sim",
        "isaac-sim-rendering",
    )
    skill_categories: ClassVar[tuple[str, ...]] = ("isaac-sim",)
    tier: ClassVar[Tier] = "worker"

    # -- deterministic domain knowledge ----------------------------------

    async def cameras(self) -> str:
        """Physical video devices on this machine, with their capture formats.

        Check this before writing any code that opens /dev/video0. Note that a
        single UVC webcam usually claims two nodes — a capture node and a
        metadata node — so a device count is not a camera count. No devices at
        all is expected on this workstation and blocks nothing: simulated
        sensors in Isaac Sim never appear under /dev.
        """
        nodes = sorted(Path("/dev").glob("video*"))
        if not nodes:
            return (
                "No /dev/video* devices — no camera is attached.\n"
                "Simulated cameras need none. For a real one, confirm the kernel saw it "
                "with `lsusb` and that uvcvideo loaded with `dmesg | grep -i uvc`."
            )

        rows = [f"{len(nodes)} video node(s): " + ", ".join(n.name for n in nodes)]
        if shutil.which("v4l2-ctl"):
            rows.append("\n$ v4l2-ctl --list-devices")
            rows.append((await self.env._sh("v4l2-ctl --list-devices")).strip())
            for node in nodes:
                formats = await self.env._sh(f"v4l2-ctl -d {node} --list-formats-ext")
                rows.append(f"\n$ v4l2-ctl -d {node} --list-formats-ext")
                rows.append(formats.strip() or "(no formats reported — likely a metadata node)")
        else:
            rows.append(
                "v4l2-ctl not on PATH, so formats and resolutions are unknown. "
                "Install it with: sudo apt install v4l-utils"
            )
        return "\n".join(rows)

    def calibration_files(self, root: str = "") -> str:
        """Camera calibration YAMLs under the workdir, and what each one holds.

        Defaults to the agent's workdir. Every .yaml is opened and kept only if
        it carries calibration keys, so ordinary config files do not pollute the
        list. Use this before solving a new calibration — reusing the existing
        one is right whenever the lens and sensor have not changed.
        """
        base = Path(root).expanduser() if root else self.workdir
        rows: list[str] = []

        for path in _find(base, ".yaml", limit=400):
            try:
                head = path.read_text(errors="replace")[:4096]
            except OSError:
                continue
            hits = [key for key in _CALIBRATION_KEYS if key in head]
            if not hits:
                continue
            size = _resolution(head)
            rows.append(f"{path}  keys={','.join(hits)}  {size}")
            if len(rows) >= 40:
                rows.append("... more matches; narrow the root")
                break

        return "\n".join(rows) or f"No camera calibration YAML found under {base}"

    def warmup_note(self) -> str:
        """Why the first frames off a simulated sensor are empty, and the fix.

        Read this when an Isaac Sim capture writes black images or a depth
        buffer of zeros, before you go looking for a bug in the camera setup.
        """
        return (
            "A rendered sensor produces nothing until the renderer has actually run for "
            "it. Reading an annotator immediately after creating the camera returns an "
            "empty or all-zero buffer, and that is the expected behaviour, not a "
            "failure.\n\n"
            "What to do: step the world with rendering enabled — world.step(render=True) "
            "or rep.orchestrator.step() — for several frames before the first read. "
            "Ray-traced and denoised outputs need more warm-up than RGB, so a value that "
            "looks right in RGB and wrong in depth or segmentation usually just needs "
            "more frames.\n\n"
            "Two neighbouring causes worth ruling out at the same time. Running with the "
            "window hidden is fine, but a run with no render context at all produces no "
            "pixels — headless still renders. And an annotator attached to a render "
            "product whose resolution was changed afterwards keeps the old size, so "
            "recreate the render product rather than resizing it."
        )


def _resolution(text: str) -> str:
    """Pull image_width/image_height out of a camera_info YAML, if present."""
    width = height = ""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("image_width:"):
            width = stripped.split(":", 1)[1].strip()
        elif stripped.startswith("image_height:"):
            height = stripped.split(":", 1)[1].strip()
    return f"{width}x{height}" if width and height else "resolution not stated"


def _find(root: Path, suffix: str, limit: int) -> list[Path]:
    """Bounded walk for files with a suffix, pruning build and vendor trees."""
    hits: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root, topdown=True):
        dirnames[:] = [d for d in dirnames if d not in _PRUNE and not d.startswith(".")]
        for name in filenames:
            if name.endswith(suffix):
                hits.append(Path(dirpath) / name)
                if len(hits) >= limit:
                    return sorted(hits)
    return sorted(hits)
