# SPDX-License-Identifier: Apache-2.0
"""USD expert."""

from __future__ import annotations

import shlex
import shutil
from pathlib import Path
from typing import ClassVar

from ..base import RoboAgent
from ..llm import Tier

#: The USD command-line tools worth having, and the question each one answers.
USD_TOOLS: dict[str, str] = {
    "usdcat": "print or re-encode a stage; --flatten resolves the whole composition",
    "usdchecker": "validate a stage; --arkit adds the package rules",
    "usdzip": "pack a stage and every dependency it resolves into one .usdz",
    "usdedit": "open a stage as .usda in $EDITOR and write it back in place",
    "usdtree": "print the prim hierarchy without the property noise",
}

#: Suffix -> what it actually is on disk. Confusing these is the usual reason a
#: "text" stage turns out to be unreadable: .usd is a wrapper over either
#: encoding, so the name tells you nothing.
USD_SUFFIXES: dict[str, str] = {
    ".usda": "ASCII — diffable and editable, but slow to load and large",
    ".usdc": "binary crate — fast and compact, opaque to git",
    ".usd": "either encoding; read the first bytes, not the extension",
    ".usdz": "uncompressed zip package — treat as read-only",
}


class USDAgent(RoboAgent):
    """USD engineer. You compose stages; you do not flatten them.

    Author in layers and treat `usdcat --flatten` as a diagnostic only — a
    flattened stage has thrown away the arcs you need to make the next edit, so
    never write one back over its own source. When a value is not what you
    expect, read the layer stack before you read the prim: sublayer strength
    order and a stronger opinion in a session layer explain most surprises.
    Reference and payload paths resolve relative to the layer that authored
    them, so a stage that opens here can still break once it is moved.
    """

    domain: ClassVar[str] = "usd"
    charter: ClassVar[str] = (
        "USD authoring and composition — layers, references, payloads and variants — "
        "plus stage inspection, validation, format conversion and optimisation."
    )
    skill_patterns: ClassVar[tuple[str, ...]] = (
        "usd-authoring",
        "inspect-asset",
        "flatten-usd",
        "compare-stages",
        "run-validators",
        "content-workflow-convert-to-usd",
    )
    skill_categories: ClassVar[tuple[str, ...]] = ("usd",)
    tier: ClassVar[Tier] = "worker"

    # -- deterministic domain knowledge ----------------------------------

    def usd_tools(self) -> str:
        """Which USD command-line tools are on PATH, and what to do if none are.

        Call this before proposing any usdcat or usdchecker command. No Isaac
        Sim build on this machine ships these binaries — an Isaac Sim install
        being present says nothing about the tools being available.
        """
        rows = [f"{'tool':12s}{'location':<42s}purpose"]
        found = False
        for tool, purpose in USD_TOOLS.items():
            path = shutil.which(tool)
            found = found or bool(path)
            rows.append(f"{tool:12s}{path or 'NOT ON PATH':<42s}{purpose}")
        if found:
            return "\n".join(rows)

        rows += [
            "",
            (
                "None installed. `pip install usd-core` into the venv that matches the "
                "Isaac Sim line you are targeting (5.x -> Python 3.11, 6.x -> 3.12) "
                "puts usdcat, usdchecker and usdzip on PATH. usdview is not in that "
                "wheel — it needs PySide, which usd-core does not ship."
            ),
        ]
        return "\n".join(rows)

    async def inspect(self, path: str, lines: int = 60) -> str:
        """Validate a stage and show the head of what its composition resolves to.

        Runs usdchecker for correctness and `usdcat --flatten` for the composed
        result. Output is truncated in Python rather than piped through `head`,
        so a usdcat failure stays visible instead of being masked by the pager's
        exit status. Skips any tool that is not installed and says so.
        """
        target = Path(path).expanduser()
        if not target.is_file():
            return f"{target}: no such file"

        quoted = shlex.quote(str(target))
        kind = USD_SUFFIXES.get(target.suffix, "unrecognised suffix")
        out = [f"# {target}  ({kind})"]

        if shutil.which("usdchecker"):
            out.append(f"\n$ usdchecker {quoted}")
            report = (await self.env._sh(f"usdchecker {quoted}", timeout=300.0)).strip()
            out.append(report or "(no output — the stage passed)")
        else:
            out.append("\nusdchecker not on PATH — validity unchecked. Call usd_tools().")

        if shutil.which("usdcat"):
            out.append(f"\n$ usdcat --flatten {quoted}")
            body = (await self.env._sh(f"usdcat --flatten {quoted}", timeout=300.0)).splitlines()
            out.extend(body[:lines])
            if len(body) > lines:
                out.append(f"... {len(body) - lines} further lines truncated")
        else:
            out.append("\nusdcat not on PATH — cannot flatten. Call usd_tools().")

        return "\n".join(out)

    def convert_command(self, source: str, target: str, flatten: bool = False) -> str:
        """Build the command to re-encode a stage between USD formats.

        Returns the command; it does not run it. usdcat picks the output
        encoding from the target suffix, so .usda gives text and .usdc binary.
        Set `flatten` only for a deliverable — it bakes every arc away.
        """
        src = Path(source).expanduser()
        dst = Path(target).expanduser()

        if dst.suffix == ".usdz":
            return (
                f"usdzip {shlex.quote(str(dst))} {shlex.quote(str(src))}"
                "  # usdcat cannot write .usdz; usdzip walks and packs the dependencies"
            )
        if flatten and src.resolve() == dst.resolve():
            return (
                f"# refused: flattening {src} onto itself destroys its composition arcs. "
                "Write the flattened stage to a separate output path."
            )

        parts = ["usdcat"]
        if flatten:
            parts.append("--flatten")
        parts += [shlex.quote(str(src)), "-o", shlex.quote(str(dst))]
        return " ".join(parts)

    def asset_warnings_note(self) -> str:
        """What the primvar warnings from NVIDIA's shipped sample assets mean.

        Read this before chasing a "corrupted data" line in a Kit log. These are
        warnings in assets you did not author, the run continues, and there is
        nothing to fix on this machine.
        """
        return (
            "Kit logs from the shipped sample and Omniverse CDN assets contain lines "
            "like:\n"
            "  [Warning] [rtx.hydra] Mesh '/__Prototype_.../mesh_0' has corrupted data "
            "in primvar 'st': buffer size 540 doesn't match expected size 77241 in "
            "faceVarying primvars\n\n"
            "What it means: a UV set (st, st_1, st_2) is declared with faceVarying "
            "interpolation but its array is sized for a different topology than the "
            "mesh it sits on. Hydra reports it, drops that primvar for the prim, and "
            "carries on rendering.\n\n"
            "Why it does not matter here: the severity is [Warning], not [Error]. The "
            "stage still opens, the frame still renders, and the affected prim is "
            "sample geometry, not your scene. A run that actually stopped stopped for "
            "another reason — grep the log for '[Error]' and for the last line before "
            "the process exited, not for these.\n\n"
            "When it IS yours: the same warning on an asset your own conversion "
            "produced means the exporter emitted mismatched primvars. Fix it at the "
            "exporter and confirm with usdchecker on the converted file; do not "
            "hand-patch the flattened stage."
        )
