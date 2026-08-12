#!/usr/bin/env python3
"""Discover and sync NVIDIA robotics agent skills.

This is the "living" part of the repo: it re-scans NVIDIA's GitHub orgs for
SKILL.md files, vendors the ones whose upstream licence permits
redistribution, and catalogues the rest as fetch-on-demand.

    python3 scripts/sync.py            # discover + sync everything
    python3 scripts/sync.py --dry-run  # show what would change

Requires the `gh` CLI, authenticated (`gh auth status`).
"""

from __future__ import annotations

import argparse
import json
import pathlib
import shutil
import subprocess
import sys
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
SKILLS = ROOT / "skills"
SOURCES = ROOT / "sources.json"

# Orgs to sweep. Add more here — sync.py picks them up on the next run.
ORGS = [
    "isaac-sim",
    "NVIDIA-Omniverse",
    "NVIDIA-ISAAC-ROS",
    "NVIDIA",
    "newton-physics",
    "NVIDIA-AI-IOT",
]

# SPDX ids we are willing to vendor (copy into this repo).
# Anything else is catalogued as link-only and fetched on demand instead.
REDISTRIBUTABLE = {
    "Apache-2.0",
    "BSD-3-Clause",
    "BSD-2-Clause",
    "MIT",
    "CC-BY-4.0",
    "BSD-3-Clause-Clear",
}

# Repos whose LICENSE file GitHub cannot classify but which we have read by
# hand and confirmed. Keeping this explicit means an unreviewed NOASSERTION
# repo is never vendored by accident.
MANUAL_LICENCE_REVIEW = {
    "isaac-sim/IsaacSim": "Apache-2.0",
    "NVIDIA/Megatron-LM": "BSD-3-Clause",
    "NVIDIA/TensorRT-LLM": "Apache-2.0",
    "NVIDIA-AI-IOT/jetson-device-skills": "CC-BY-4.0",
    "NVIDIA-AI-IOT/jetson-bsp-skills": "CC-BY-4.0",
    "NVIDIA-AI-IOT/DeepStream_Coding_Agent": "CC-BY-4.0",
    # Explicitly NOT redistributable — proprietary NVIDIA EULA. Clause 5
    # forbids distribution "in any publicly accessible software repositories".
    "NVIDIA-Omniverse/ovrtx": "LicenseRef-NVIDIA-Proprietary",
    # No LICENSE file at all -> all rights reserved by default.
    "newton-physics/newton-asv": "LicenseRef-None",
}

# Where each repo's skills land locally. Ordered roughly by how central the
# repo is to robotics work, which is what this catalogue is actually for.
CATEGORY = {
    # --- simulation ---
    "isaac-sim/IsaacSim": "isaac-sim",
    "isaac-sim/IsaacAutomator": "isaac-sim",
    "isaac-sim/isaac-launchable": "isaac-sim",
    "isaac-sim/IsaacLab": "isaac-lab",
    "isaac-sim/IsaacLab-Arena": "isaac-lab",
    # --- physics ---
    "newton-physics/newton": "newton",
    "newton-physics/newton-asv": "newton",
    "NVIDIA-Omniverse/PhysX": "physx",
    "NVIDIA/warp": "warp",
    # --- USD / scene authoring ---
    "NVIDIA-Omniverse/usd-content-agents": "usd",
    "NVIDIA-Omniverse/usd-optimize": "usd",
    "NVIDIA-Omniverse/usd-exchange": "usd",
    # --- omniverse runtime ---
    "NVIDIA-Omniverse/ovrtx": "omniverse",
    "NVIDIA-Omniverse/kit-cae": "omniverse",
    # --- edge / robot hardware ---
    "NVIDIA-AI-IOT/jetson-device-skills": "jetson",
    "NVIDIA-AI-IOT/jetson-bsp-skills": "jetson",
    "NVIDIA-AI-IOT/jetson-ai-lab": "jetson",
    "NVIDIA-AI-IOT/DeepStream_Coding_Agent": "jetson",
    "NVIDIA-AI-IOT/inference_builder": "jetson",
    "NVIDIA-AI-IOT/auto-magic-calib": "perception",
    # --- deployment / inference ---
    "NVIDIA/TensorRT": "inference",
    "NVIDIA/TensorRT-LLM": "inference",
    "NVIDIA/DALI": "inference",
    # --- model training infrastructure (secondary to robotics) ---
    "NVIDIA/Megatron-LM": "ml-infra",
    "NVIDIA/NemoClaw": "ml-infra",
    "NVIDIA/cudf": "ml-infra",
    "NVIDIA/OpenShell": "ml-infra",
    "NVIDIA/SkillSpector": "ml-infra",
}
DEFAULT_CATEGORY = "other"

# Categories that are the point of this repo, in display order. Anything not
# listed here is still catalogued, just presented as secondary.
ROBOTICS_CATEGORIES = [
    "isaac-sim", "isaac-lab", "newton", "physx", "warp",
    "usd", "omniverse", "jetson", "perception",
]


def gh(*args: str) -> str:
    """Run a gh command and return stdout, or '' on failure."""
    try:
        return subprocess.run(
            ["gh", *args], capture_output=True, text=True, timeout=120, check=False
        ).stdout.strip()
    except Exception:
        return ""


def discover() -> dict[str, list[str]]:
    """Return {repo: [skill paths]} across all orgs."""
    found: dict[str, list[str]] = {}
    for org in ORGS:
        raw = gh(
            "search", "code", "--owner", org, "--filename", "SKILL.md",
            "--limit", "100", "--json", "repository,path",
        )
        if not raw:
            continue
        try:
            hits = json.loads(raw)
        except json.JSONDecodeError:
            continue
        for hit in hits:
            repo = hit["repository"]["nameWithOwner"]
            found.setdefault(repo, []).append(hit["path"])
    return {r: sorted(set(p)) for r, p in found.items()}


def licence_of(repo: str) -> str:
    """SPDX id for a repo, preferring our hand review over GitHub's guess."""
    if repo in MANUAL_LICENCE_REVIEW:
        return MANUAL_LICENCE_REVIEW[repo]
    spdx = gh("api", f"repos/{repo}", "--jq", ".license.spdx_id // \"NOASSERTION\"")
    return spdx or "NOASSERTION"


def default_branch(repo: str) -> str:
    return gh("api", f"repos/{repo}", "--jq", ".default_branch") or "main"


def fetch(url: str) -> bytes | None:
    try:
        with urllib.request.urlopen(url, timeout=60) as r:
            return r.read()
    except Exception:
        return None


def skill_name(path: str) -> str:
    """'skills/foo/SKILL.md' -> 'foo'."""
    parts = [p for p in path.split("/") if p and p != "SKILL.md"]
    return parts[-1] if parts else "unnamed"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not gh("auth", "status"):
        print("gh CLI not authenticated. Run: gh auth login", file=sys.stderr)

    print("Discovering skills across", len(ORGS), "orgs...")
    found = discover()
    if not found:
        print("No skills found (is gh authenticated?)", file=sys.stderr)
        return 1

    catalog: list[dict] = []
    vendored = linked = 0

    for repo in sorted(found):
        spdx = licence_of(repo)
        branch = default_branch(repo)
        can_vendor = spdx in REDISTRIBUTABLE
        category = CATEGORY.get(repo, DEFAULT_CATEGORY)

        print(f"\n{repo}  [{spdx}]  {'vendor' if can_vendor else 'LINK ONLY'}")

        for path in found[repo]:
            name = skill_name(path)
            raw_url = f"https://raw.githubusercontent.com/{repo}/{branch}/{path}"
            entry = {
                "name": name,
                "category": category,
                "source_repo": repo,
                "source_path": path,
                "source_ref": branch,
                "url": f"https://github.com/{repo}/blob/{branch}/{path}",
                "raw_url": raw_url,
                "license": spdx,
                "vendored": bool(can_vendor),
            }

            if can_vendor and not args.dry_run:
                dest = SKILLS / category / f"{name}.md"
                dest.parent.mkdir(parents=True, exist_ok=True)
                body = fetch(raw_url)
                if body is None:
                    print(f"  !! fetch failed: {name}")
                    entry["vendored"] = False
                    linked += 1
                    catalog.append(entry)
                    continue
                header = (
                    f"<!-- Vendored from {repo} @ {branch}\n"
                    f"     Path:    {path}\n"
                    f"     Licence: {spdx}\n"
                    f"     Source:  {entry['url']}\n"
                    f"     Unmodified copy. See NOTICE for attribution. -->\n\n"
                ).encode()
                dest.write_bytes(header + body)
                vendored += 1
                print(f"  + {category}/{name}.md")
            else:
                linked += 1
                if not can_vendor:
                    print(f"  ~ {name} (catalogued, fetch on demand)")

            catalog.append(entry)

    if args.dry_run:
        print(f"\nDRY RUN: would vendor {vendored}, link {linked}")
        return 0

    SOURCES.write_text(
        json.dumps(
            {
                "generated_by": "scripts/sync.py",
                "orgs_scanned": ORGS,
                "redistributable_licenses": sorted(REDISTRIBUTABLE),
                "counts": {"vendored": vendored, "link_only": linked},
                "skills": sorted(catalog, key=lambda e: (e["category"], e["name"])),
            },
            indent=2,
        )
        + "\n"
    )
    print(f"\nDone. Vendored {vendored}, catalogued {linked} link-only.")
    print(f"Wrote {SOURCES.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
