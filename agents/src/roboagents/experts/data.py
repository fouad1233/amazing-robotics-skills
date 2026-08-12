# SPDX-License-Identifier: Apache-2.0
"""Dataset, Hub and storage expert."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import ClassVar

from ..base import RoboAgent
from ..llm import Tier

#: Environment variables that can hold a Hub token. Their *presence* is
#: reported; their value is never read, printed, or passed to a subprocess.
_TOKEN_ENV: tuple[str, ...] = ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN", "HUGGINGFACEHUB_API_TOKEN")

#: Below this, a single dataset pull can fill the disk. Everything on this box
#: — home, the robotics tree and the Hub cache — is on one filesystem, so
#: filling it takes the desktop down with it, not just the download.
LOW_DISK_GB: float = 100.0

#: aria2c connection count. Bandwidth here is throttled per TCP connection, so
#: throughput scales with streams until the server's own per-file cap bites.
ARIA2_CONNECTIONS: int = 16


class DataAgent(RoboAgent):
    """Dataset engineer: Hugging Face Hub, format conversion, and storage.

    Check free space before starting any download — one filesystem holds home,
    the robotics tree and the Hub cache, so a dataset that fills it takes the
    machine down, not just the transfer. This network throttles per connection,
    so a single-stream pull is the slowest option available and never the right
    default. Never read, echo, or interpolate a token, a credentials file, or a
    URL that carries one.
    """

    domain: ClassVar[str] = "data"
    charter: ClassVar[str] = (
        "Datasets and the Hugging Face Hub: downloading and uploading, format "
        "conversion, cache management, and disk space for large corpora."
    )
    skill_patterns: ClassVar[tuple[str, ...]] = (
        "huggingface-datasets",
        "hf-cli",
        "dataset",
        "hf-mcp",
        "fiftyone",
    )
    skill_categories: ClassVar[tuple[str, ...]] = ("huggingface",)
    tier: ClassVar[Tier] = "worker"

    # -- deterministic domain knowledge ----------------------------------

    async def hf_status(self) -> str:
        """Whether a Hub credential exists, and which account it authenticates as.

        Reports presence and the account name only — no token is read or
        printed, here or anywhere else. Run this before blaming a repo: an
        unauthenticated client gets 404 rather than 403 for a private or gated
        repo, because the Hub will not confirm that a repo you cannot see
        exists. "Repo not found" plus "no credential" is one problem, not two.
        """
        rows = [
            f"{name}: {'set' if os.environ.get(name) else 'unset'}"
            for name in _TOKEN_ENV
        ]
        token_file = self._hf_home() / "token"
        rows.append(f"{token_file}: {'present' if token_file.is_file() else 'absent'}")

        cli = self._hf_cli()
        if cli is None:
            rows.append("hf CLI: not found on PATH or in any virtualenv under ~/envs")
            return "\n".join(rows)

        raw = await self.env._sh(f"{cli} auth whoami")
        answer = [
            line.strip()
            for line in raw.splitlines()
            if line.strip() and not line.startswith("Hint:")
        ]
        rows.append(f"{cli} auth whoami -> {answer[-1] if answer else '(no output)'}")
        return "\n".join(rows)

    async def hf_cache(self) -> str:
        """Size of the Hugging Face cache, broken down by subdirectory.

        Reclaiming space here has one trap worth knowing: snapshot directories
        are symlinks into blobs/, so deleting a snapshot frees nothing at all
        and only detaches the blob from its name. Use `hf cache delete` (or
        remove the whole `models--*` / `datasets--*` directory) so the blob goes
        with it. The `xet` directory is a separate chunk store and is not freed
        by deleting anything under `hub`.
        """
        home = self._hf_home()
        if not home.is_dir():
            return f"No Hugging Face cache at {home}"
        total = await self.env._sh(f"du -sh {home}", timeout=300.0)
        parts = await self.env._sh(f"du -sh {home}/*", timeout=300.0)
        return f"{total.strip()}\n{parts.strip()}"

    def disk_headroom(self) -> str:
        """Free space on the filesystems that matter, with a warning when it is thin.

        Check this before a download, a dataset conversion, or a training run
        that writes checkpoints. Compare the free figure against the download
        size *doubled* when a conversion is involved — the source and the
        converted copy exist at the same time, and the Hub cache keeps the
        original blob after an extraction unless you delete it explicitly.
        """
        targets = (
            ("robotics root", self.workdir),
            ("hf cache", self._hf_home()),
            ("home", Path.home()),
        )
        rows = []
        devices = set()
        for label, path in targets:
            probe = self._nearest_existing(path)
            usage = shutil.disk_usage(probe)
            devices.add(probe.stat().st_dev)
            free_gb = usage.free / 1024**3
            used_pct = 100.0 * (usage.total - usage.free) / usage.total
            flag = "  LOW" if free_gb < LOW_DISK_GB else ""
            rows.append(
                f"{label:<14} {path}  free {free_gb:.0f}G of "
                f"{usage.total / 1024**3:.0f}G ({used_pct:.0f}% used){flag}"
            )
        if len(devices) == 1:
            rows.append(
                "All three are the same filesystem — there is no separate scratch disk "
                "to spill onto, so one runaway download stalls the whole machine."
            )
        return "\n".join(rows)

    def download_note(self) -> str:
        """How to download on this network, and why the obvious way is the slow way.

        Read this before pulling any model or dataset. The default single-stream
        path is not merely slower here, it is slower by a large constant factor,
        and no amount of waiting fixes it.
        """
        aria = shutil.which("aria2c") or "aria2c (NOT INSTALLED: sudo apt install aria2)"
        return "\n".join(
            [
                (
                    "Bandwidth here is throttled per TCP connection, not per host. One stream "
                    "gets one share and stops there; N streams get roughly N shares. Measured "
                    "on the same files, a parallel fetcher beat a serial one by ~2.6x."
                ),
                "",
                (
                    "Hugging Face: install hf_transfer and set HF_HUB_ENABLE_HF_TRANSFER=1. "
                    "It issues many parallel range requests per file. The cost is that it "
                    "drops the detailed progress bar and cannot resume — on a link that keeps "
                    "dropping, turn it off and take the slower but restartable path."
                ),
                "  HF_HUB_ENABLE_HF_TRANSFER=1 hf download <repo> --local-dir <dir>",
                "",
                f"Plain URLs: {aria}",
                f"  aria2c -x{ARIA2_CONNECTIONS} -s{ARIA2_CONNECTIONS} -k1M -c <url>",
                (
                    "  -c resumes a partial file; always pass it, and always re-run rather "
                    "than restarting from zero."
                ),
                "  If you must use curl, `curl -C -` at least resumes — but it is one stream.",
                "",
                (
                    "Exception: pypi.nvidia.com serves ~31 KB/s from here no matter how many "
                    "connections you open, so `pip install isaacsim[all]` cannot complete. "
                    "Take the standalone zips from download.isaacsim.omniverse.nvidia.com "
                    "(~7.8 MB/s) instead."
                ),
            ]
        )

    # -- internals -------------------------------------------------------

    @staticmethod
    def _hf_home() -> Path:
        base = os.environ.get("HF_HOME")
        if base:
            return Path(base).expanduser()
        return Path.home() / ".cache" / "huggingface"

    @staticmethod
    def _hf_cli() -> str | None:
        found = shutil.which("hf")
        if found:
            return found
        # The CLI ships inside whichever venv has huggingface_hub, not on PATH.
        for candidate in sorted((Path.home() / "envs").glob("*/bin/hf")):
            if os.access(candidate, os.X_OK):
                return str(candidate)
        return None

    @staticmethod
    def _nearest_existing(path: Path) -> Path:
        for candidate in (path, *path.parents):
            if candidate.exists():
                return candidate
        return Path("/")
