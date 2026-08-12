# SPDX-License-Identifier: Apache-2.0
"""Jetson expert: bring-up, JetPack and BSP, deploying policies to the robot."""

from __future__ import annotations

import platform
import re
from pathlib import Path
from typing import ClassVar

from ..base import RoboAgent
from ..llm import Tier

#: Present only on an L4T rootfs. Its absence is how you know you are on the
#: x86_64 workstation rather than on the robot.
TEGRA_RELEASE_FILE = Path("/etc/nv_tegra_release")

#: "# R36 (release), REVISION: 4.3, GCID: ..., BOARD: generic, EABI: aarch64"
_TEGRA_RELEASE = re.compile(r"#\s*R(\d+)\s*\([^)]*\)\s*,\s*REVISION:\s*([\d.]+)")

#: L4T release -> JetPack. The file stamps the L4T release; nothing on the
#: device records a JetPack number, but that is the number people quote, so the
#: translation has to happen somewhere and it may as well be here.
JETPACK_FOR_L4T: dict[str, str] = {
    "35.3.1": "JetPack 5.1.1",
    "35.4.1": "JetPack 5.1.2",
    "35.5.0": "JetPack 5.1.3",
    "35.6.0": "JetPack 5.1.4",
    "36.3.0": "JetPack 6.0 GA",
    "36.4.0": "JetPack 6.1",
    "36.4.3": "JetPack 6.2",
    "36.4.4": "JetPack 6.2.1",
}

#: Fallback when the point release is not in the table. Better a correct line
#: than a confidently wrong point version.
JETPACK_LINE: dict[str, str] = {
    "32": "JetPack 4.x",
    "35": "JetPack 5.x",
    "36": "JetPack 6.x",
    "38": "JetPack 7.x (Thor)",
}


class JetsonAgent(RoboAgent):
    """Jetson engineer. This workstation is not a Jetson — treat it as the host.

    Everything for the robot is either cross-built here into an aarch64 container
    or built on the device over ssh; wheels and .debs from this x86_64 box do not
    run on L4T. Read the L4T release off the target before assuming a JetPack
    version, and never start a flash on a board you cannot physically reach to
    put back into recovery mode.
    """

    domain: ClassVar[str] = "jetson"
    charter: ClassVar[str] = (
        "Jetson bring-up and flashing, JetPack and L4T BSP versions, on-device "
        "toolchains, and deploying a trained policy from this workstation onto "
        "the robot."
    )
    skill_patterns: ClassVar[tuple[str, ...]] = (
        "jetpack",
        "jetson-flash-image",
        "l4t-differences",
        "jetson-diagnostic",
        "gr00t-n1-6-deploy-agx",
    )
    skill_categories: ClassVar[tuple[str, ...]] = ("jetson",)
    tier: ClassVar[Tier] = "worker"

    # -- deterministic domain knowledge ----------------------------------

    def is_jetson(self) -> bool:
        """True when *this* machine is a Jetson, False on the x86_64 workstation.

        Call it before running anything that assumes tegra. It returns False
        here, which means every Jetson command has to be prefixed with ssh or run
        inside an aarch64 container — see `cross_note()`. A False result is not a
        fault to work around; it is the reason the work is remote.
        """
        if TEGRA_RELEASE_FILE.is_file():
            return True
        # Some custom rootfs images drop the release stamp. The device tree does
        # not lie about the SoC, so use it as the second opinion.
        model = _read(Path("/proc/device-tree/model")).lower()
        return ("jetson" in model) or ("tegra" in model)

    def jetpack_version(self) -> str:
        """L4T release on this machine and the JetPack it corresponds to.

        Only meaningful on a Jetson; on the workstation it says so rather than
        guessing. The JetPack number is derived, not stored — /etc/nv_tegra_release
        holds the L4T release, so a device that reports R36.4.3 and a user who
        says "JetPack 6.2" are agreeing, not contradicting each other. To read
        the robot instead of this box, run `cat /etc/nv_tegra_release` over ssh.
        """
        text = _read(TEGRA_RELEASE_FILE)
        if not text:
            return (
                f"{TEGRA_RELEASE_FILE} is absent — this is not a Jetson "
                f"({platform.machine()}). Read it on the target over ssh."
            )

        match = _TEGRA_RELEASE.search(text)
        if not match:
            return f"Could not parse {TEGRA_RELEASE_FILE}: {text.strip().splitlines()[0]}"

        major, revision = match.group(1), match.group(2)
        l4t = f"{major}.{revision}"
        jetpack = JETPACK_FOR_L4T.get(l4t)
        if not jetpack:
            line = JETPACK_LINE.get(major, "unknown JetPack line")
            jetpack = f"{line} (exact point release not in the table)"
        return f"L4T R{l4t} -> {jetpack}"

    def cross_note(self) -> str:
        """Why Jetson work cannot just run here, and which ROS 2 pairs with JetPack.

        Read this before proposing any build or install command for the robot. It
        states the architecture of this machine and the three pairings that get
        assumed wrong most often — arch, TensorRT provenance, and ROS 2 distro.
        """
        return "\n".join(
            [
                f"This machine is {platform.machine()}, Ubuntu 24.04. It is the host,",
                "not the target. Jetson work is cross-compilation or remote work.",
                "",
                "  Architecture. L4T is aarch64 and links against the tegra CUDA,",
                "  cuDNN and TensorRT that JetPack ships, not against the desktop",
                "  packages here. Nothing built on this box runs on the robot.",
                "  Build on the device over ssh, or cross-build in an aarch64",
                "  container (qemu-user-static plus binfmt_misc).",
                "",
                "  TensorRT. There is no desktop `pip install tensorrt` that works",
                "  on Jetson — on L4T it comes from the JetPack apt repo and is",
                "  pinned to the BSP. Export a policy to ONNX here if you like, but",
                "  the engine must be built on the device: engines do not port",
                "  across GPU architectures or TensorRT versions.",
                "",
                "  ROS 2. JetPack 6 is Ubuntu 22.04 (jammy), so it pairs with ROS 2",
                "  Humble, not Jazzy. Jazzy needs 24.04 and no JetPack 6 image",
                "  provides it. If this workstation runs Jazzy, the two ends are on",
                "  different distros: talk over the wire via DDS, or containerise.",
                "  Do not expect one colcon workspace to build for both.",
            ]
        )


def _read(path: Path) -> str:
    """File contents, or empty string when absent or unreadable."""
    try:
        return path.read_text(errors="replace")
    except OSError:
        return ""
