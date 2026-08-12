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
import hashlib
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
# NOTE: org-wide sweeps of `huggingface` and `rerun-io` were tried and removed.
# They pulled in 40+ unrelated repos (transformers, diffusers, chat-ui, blog,
# kernels, mlclaw...). The robotics-relevant repos from those orgs are named
# individually in EXTRA_REPOS instead. Breadth is not the goal; a robotics
# engineer finding the right skill in ten seconds is.

# Individual repos outside the swept orgs. Org sweeps are cheap but coarse;
# the robotics skill ecosystem is scattered across many small orgs.
EXTRA_REPOS = [
    "ros-claw/rosclaw",
    "spacemit-robotics/robot-skills",
    "D-Robotics/moss",
    "nebius/nebius-physical-ai",
    "ambient-robots/xlerobot_pinc",
    "Seeed-Projects/Seeed-Jetson-DevelopTool",
    "OpenGalaxea/GalaxeaVLA",
    "AgibotTech/genie_sim",
    # ROS 2. Note: the official ROS orgs (ros2, moveit, ros-planning,
    # ros-navigation, ros-controls) ship no SKILL.md files at all, and neither
    # does NVIDIA-ISAAC-ROS across its 65 repos — verified by tree walk.
    # Everything here is community work.
    "arpitg1304/robotics-agent-skills",
    "castacks/AirStack",
    "harunkurtdev/ros2-claude-code-template",
    "MIUAV/vibe-coding-ros2",
    # Hugging Face + Rerun: named individually rather than swept, see ORGS note.
    "huggingface/skills",
    "huggingface/OpenEnv",
    "rerun-io/rerun",
    "rerun-io/trossen-oss",
]

# Never ingest these, whatever a search turns up.
# claude-skill-registry is a mass scrape of ~21,000 skills across every domain;
# its "robotics" matches are false positives and it would bury this catalogue.
EXCLUDE_REPOS = {
    "majiayu000/claude-skill-registry",
    "gabrielmoreira/agent-skills-mirror",
    "LeoYeAI/openclaw-master-skills",
    "sige0002/skill_store",
    "SpectreDeath/skill-flywheel",
}

# Agent-facing docs that are not SKILL.md but serve the same purpose. Some
# major robotics repos ship AGENTS.md/CLAUDE.md instead of skills — LeRobot
# is the notable one. Vendored into reference/ rather than skills/.
AGENT_DOCS = [
    ("huggingface/lerobot", "AGENTS.md", "lerobot"),
    ("huggingface/lerobot", "CLAUDE.md", "lerobot"),
    ("huggingface/leLab", "CLAUDE.md", "lerobot"),
    ("isaac-sim/IsaacLab", "AGENTS.md", "isaac-lab"),
    ("isaac-sim/IsaacSim", "AGENTS.md", "isaac-sim"),
    ("isaac-sim/IsaacSim", "CLAUDE.md", "isaac-sim"),
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
    # --- LeRobot ecosystem ---
    "huggingface/lerobot": "lerobot",
    "ambient-robots/xlerobot_pinc": "lerobot",
    # --- ROS 2 ---
    "ros-claw/rosclaw": "ros2",
    "arpitg1304/robotics-agent-skills": "ros2",
    "castacks/AirStack": "ros2",
    "harunkurtdev/ros2-claude-code-template": "ros2",
    "MIUAV/vibe-coding-ros2": "ros2",
    # --- robot platforms & sim ---
    "spacemit-robotics/robot-skills": "robot-platforms",
    "D-Robotics/moss": "robot-platforms",
    "AgibotTech/genie_sim": "robot-platforms",
    "OpenGalaxea/GalaxeaVLA": "robot-platforms",
    "nebius/nebius-physical-ai": "robot-platforms",
    "Seeed-Projects/Seeed-Jetson-DevelopTool": "jetson",
    # --- visualisation / debugging ---
    "rerun-io/rerun": "visualization",
    "rerun-io/trossen-oss": "robot-platforms",
    # --- hugging face tooling ---
    "huggingface/skills": "huggingface",
    "huggingface/OpenEnv": "huggingface",
    # --- model training infrastructure (secondary to robotics) ---
    "NVIDIA/Megatron-LM": "ml-infra",
    "NVIDIA/NemoClaw": "ml-infra",
    "NVIDIA/cudf": "ml-infra",
    "NVIDIA/OpenShell": "ml-infra",
    "NVIDIA/SkillSpector": "ml-infra",
    "NVIDIA/skills": "nvidia-products",
}
DEFAULT_CATEGORY = "other"

# Categories that are the point of this repo, in display order. Anything not
# listed here is still catalogued, just presented as secondary.
ROBOTICS_CATEGORIES = [
    "isaac-sim", "isaac-lab", "newton", "physx", "warp",
    "usd", "omniverse", "lerobot", "ros2", "robot-platforms",
    "jetson", "perception", "visualization",
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
    """Return {repo: [skill paths]} across all orgs plus the extra repos."""
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

    # Individual repos: walk the git tree directly rather than code search,
    # which is both exact and immune to search indexing lag.
    for repo in EXTRA_REPOS:
        branch = default_branch(repo)
        raw = gh(
            "api", f"repos/{repo}/git/trees/{branch}?recursive=1",
            "--jq", '[.tree[] | select(.path|test("SKILL\\\\.md$"))] | .[].path',
        )
        for path in filter(None, (p.strip() for p in raw.splitlines())):
            found.setdefault(repo, []).append(path)

    return {
        r: sorted(set(p))
        for r, p in found.items()
        if r not in EXCLUDE_REPOS
    }


def licence_of(repo: str) -> str:
    """SPDX id for a repo, preferring our hand review over GitHub's guess."""
    if repo in MANUAL_LICENCE_REVIEW:
        return MANUAL_LICENCE_REVIEW[repo]
    spdx = gh("api", f"repos/{repo}", "--jq", ".license.spdx_id // \"NOASSERTION\"")
    return spdx or "NOASSERTION"


def default_branch(repo: str) -> str:
    return gh("api", f"repos/{repo}", "--jq", ".default_branch") or "main"


CACHE = ROOT / ".sync-cache"


def fetch(url: str) -> bytes | None:
    """Fetch a URL, caching by content hash of the URL.

    Re-running sync.py after a taxonomy change should not re-download several
    hundred files over a slow link. The cache makes a re-run essentially free;
    delete .sync-cache/ to force a refresh from upstream.
    """
    key = CACHE / (hashlib.sha256(url.encode()).hexdigest()[:32] + ".bin")
    if key.exists():
        return key.read_bytes()
    try:
        with urllib.request.urlopen(url, timeout=60) as r:
            body = r.read()
    except Exception:
        return None
    CACHE.mkdir(parents=True, exist_ok=True)
    key.write_bytes(body)
    return body


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

    # Agent-facing docs (AGENTS.md / CLAUDE.md) from repos that ship those
    # instead of skills. LeRobot is the notable one.
    print("\nAgent docs:")
    for repo, path, category in AGENT_DOCS:
        spdx = licence_of(repo)
        branch = default_branch(repo)
        raw_url = f"https://raw.githubusercontent.com/{repo}/{branch}/{path}"
        can_vendor = spdx in REDISTRIBUTABLE
        name = f"{repo.split('/')[-1]}-{path.replace('.md', '')}".lower()
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
            "kind": "agent-doc",
        }
        if can_vendor and not args.dry_run:
            dest = ROOT / "reference" / category / f"{name}.md"
            dest.parent.mkdir(parents=True, exist_ok=True)
            body = fetch(raw_url)
            if body is not None:
                header = (
                    f"<!-- Vendored from {repo} @ {branch}\n"
                    f"     Path:    {path}\n"
                    f"     Licence: {spdx}\n"
                    f"     Source:  {entry['url']}\n"
                    f"     Unmodified copy. See NOTICE for attribution. -->\n\n"
                ).encode()
                dest.write_bytes(header + body)
                vendored += 1
                print(f"  + reference/{category}/{name}.md")
            else:
                entry["vendored"] = False
                linked += 1
        else:
            linked += 1
            print(f"  ~ {name} ({spdx}, link only)")
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
