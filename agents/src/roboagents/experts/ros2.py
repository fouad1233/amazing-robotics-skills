# SPDX-License-Identifier: Apache-2.0
"""ROS 2 expert."""

from __future__ import annotations

import os
from pathlib import Path
from typing import ClassVar

from ..base import RoboAgent
from ..llm import Tier

#: Ubuntu release -> the ROS 2 distribution whose binary debs are published for
#: it. Off this table there are no packages to install, only a source build.
DISTRO_FOR_UBUNTU: dict[str, str] = {
    "20.04": "foxy",
    "22.04": "humble",
    "24.04": "jazzy",
}

#: Environment variables set by a ROS 2 setup.bash, and why each one matters.
ROS_ENV_VARS: dict[str, str] = {
    "ROS_DISTRO": "which installation is active — the single best signal",
    "AMENT_PREFIX_PATH": "colon-separated package prefixes; empty means nothing sourced",
    "ROS_DOMAIN_ID": "DDS partition, default 0 — collides with every other host on the LAN",
    "RMW_IMPLEMENTATION": "DDS vendor; must match on both ends of every topic",
    "ROS_LOCALHOST_ONLY": "1 confines discovery to this host; Jazzy prefers "
    "ROS_AUTOMATIC_DISCOVERY_RANGE",
}

#: Directories never worth walking when hunting for packages in a workspace.
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
}


class ROS2Agent(RoboAgent):
    """ROS 2 engineer on Ubuntu 24.04, which means Jazzy and nothing else.

    Source exactly one ROS installation per shell and check ROS_DISTRO before
    you debug an import — a workspace built under one distro and sourced under
    another fails as if packages were missing. Keep the Isaac Sim bridge and a
    system ROS 2 apart; read `bridge_note()` before you put anything ROS on
    PYTHONPATH. Express bringup as a launch file so parameters, remappings and
    namespaces stay in version control rather than in a shell command.
    """

    domain: ClassVar[str] = "ros2"
    charter: ClassVar[str] = (
        "ROS 2 packages and workspaces, bringup and launch files, topics, nodes and "
        "parameters, and the Isaac Sim ROS 2 bridge."
    )
    skill_patterns: ClassVar[tuple[str, ...]] = (
        "ros2",
        "robot-bringup",
        "write-launch-file",
        "isaac-sim-ros2-bridge",
        "docker-ros2-development",
    )
    skill_categories: ClassVar[tuple[str, ...]] = ("ros2",)
    tier: ClassVar[Tier] = "worker"

    # -- deterministic domain knowledge ----------------------------------

    def distro(self) -> str:
        """ROS 2 installations under /opt/ros, and the one this Ubuntu expects.

        Check this before writing any command that assumes `ros2` exists. A
        distro directory with no setup.bash is a partial install and will not
        source. No installations at all is a normal state on a box that only
        drives Isaac Sim's internal bridge — see `bridge_note()`.
        """
        version = _ubuntu_version()
        expected = DISTRO_FOR_UBUNTU.get(version, "unknown")
        rows = [f"Ubuntu {version} -> expects ROS 2 {expected}"]
        if version == "24.04":
            rows.append("  (Kilted also targets 24.04, but Jazzy is the LTS pairing)")

        found = sorted(p for p in Path("/opt/ros").glob("*") if p.is_dir())
        if not found:
            rows.append("No ROS 2 installed under /opt/ros.")
            rows.append(
                f"  Install: sudo apt install ros-{expected}-desktop, "
                f"then source /opt/ros/{expected}/setup.bash"
            )
            return "\n".join(rows)

        for path in found:
            setup = path / "setup.bash"
            state = "sourceable" if setup.is_file() else "NO setup.bash (partial install)"
            mismatch = "" if path.name == expected else "  <- not the distro for this Ubuntu"
            rows.append(f"  {path}: {state}{mismatch}")
        return "\n".join(rows)

    def sourced(self) -> str:
        """Whether a ROS 2 environment is active in this process, and which one.

        These variables come from setup.bash. `self.shell` holds one persistent
        session, so a `source` run there is visible to later shell commands but
        never to this Python process — check both before concluding that an
        environment is missing.
        """
        distro = os.environ.get("ROS_DISTRO")
        rows = [
            f"ROS_DISTRO={distro}" if distro else "ROS_DISTRO is unset — nothing sourced here."
        ]
        for name, why in ROS_ENV_VARS.items():
            if name == "ROS_DISTRO":
                continue
            value = os.environ.get(name)
            if name == "AMENT_PREFIX_PATH" and value:
                value = f"{len(value.split(':'))} prefixes"
            rows.append(f"  {name}={value or '(unset)'}  # {why}")

        if not distro:
            rows.append(
                "Source it inside the persistent shell, not around it:\n"
                "  self.shell.run('source /opt/ros/jazzy/setup.bash && ros2 topic list')"
            )
        return "\n".join(rows)

    def bridge_note(self) -> str:
        """Why `import rclpy` fails inside Isaac Sim, and which rclpy is in play.

        Read this before adding a system ROS 2 install to PYTHONPATH to "fix" an
        import error — that is almost always what caused it. Reports the
        internal ROS 2 builds each Isaac Sim on this machine actually ships.
        """
        rows: list[str] = ["Internal ROS 2 builds shipped inside each Isaac Sim:"]
        for root in sorted(self.workdir.glob("isaacsim-*")):
            # 5.x keeps the vendored rclpy under isaacsim.ros2.bridge; 6.x moved
            # it to isaacsim.ros2.core when the bridge extension was split up.
            distros = sorted(
                {
                    path.parent.name
                    for ext in ("isaacsim.ros2.bridge", "isaacsim.ros2.core")
                    for path in (root / "exts" / ext).glob("*/rclpy")
                }
            )
            bundled = sorted(root.glob("kit/python/lib/python3.*"))
            python = bundled[-1].name if bundled else "python?"
            rows.append(
                f"  {root.name}: {', '.join(distros) or 'none found'}  "
                f"(bundled interpreter {python})"
            )

        rows += [
            "",
            (
                "Isaac Sim ships its own rclpy and RMW libraries and loads them itself "
                "when no ROS 2 environment is sourced. That path works with no system "
                "ROS 2 installed at all."
            ),
            "",
            (
                "The usual break: a system install is sourced, so ROS_DISTRO, "
                "AMENT_PREFIX_PATH and LD_LIBRARY_PATH point at debs built for the "
                "system interpreter, while Kit is running its own. Isaac Sim 5.x "
                "bundles Python 3.11 and Ubuntu 24.04 ships 3.12, so a Jazzy "
                "python3-rclpy deb is a cp312 extension module that cannot import into "
                "a cp311 Kit — the error is an ImportError or an undefined symbol out "
                "of _rclpy_pybind11, not a missing package."
            ),
            "",
            (
                "Rules that hold: pick one side and stay on it for the whole process; "
                "if you do source a system ROS 2, its distro must match a distro listed "
                "above and its Python must match the bundled interpreter; never `pip "
                "install rclpy` into an Isaac Sim environment, since the PyPI name is "
                "not the ROS client library; and run the bridge script through the "
                "Isaac Sim python.sh rather than a venv python."
            ),
        ]
        return "\n".join(rows)

    def packages(self, root: str = "") -> str:
        """ROS 2 packages in a workspace, with the build type each one declares.

        Defaults to the agent's workdir. Build directories and Isaac Sim
        extension trees are skipped, so this is safe to run on a large root. The
        build type decides the tooling: ament_python means setup.py, ament_cmake
        means CMakeLists.txt, and mixing them in one package does not work.
        """
        base = Path(root).expanduser() if root else self.workdir
        manifests = _find(base, "package.xml", limit=60)
        if not manifests:
            return f"No package.xml under {base}"

        rows = []
        for manifest in manifests:
            text = manifest.read_text(errors="replace")
            build_type = "unknown"
            for candidate in ("ament_python", "ament_cmake", "cmake"):
                if candidate in text:
                    build_type = candidate
                    break
            rows.append(f"{manifest.parent}  [{build_type}]")
        return "\n".join(rows)


def _ubuntu_version() -> str:
    """VERSION_ID from /etc/os-release, or '?' when it cannot be read."""
    try:
        for line in Path("/etc/os-release").read_text().splitlines():
            if line.startswith("VERSION_ID="):
                return line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    return "?"


def _find(root: Path, filename: str, limit: int) -> list[Path]:
    """Bounded walk for a filename, pruning build and vendor trees."""
    hits: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root, topdown=True):
        dirnames[:] = [d for d in dirnames if d not in _PRUNE and not d.startswith(".")]
        if filename in filenames:
            hits.append(Path(dirpath) / filename)
            if len(hits) >= limit:
                break
    return sorted(hits)
