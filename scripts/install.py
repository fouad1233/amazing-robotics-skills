#!/usr/bin/env python3
"""Install skills from this catalogue into your agent's skills directory.

    python3 scripts/install.py --list
    python3 scripts/install.py --category isaac-sim
    python3 scripts/install.py --name isaac-sim-remote
    python3 scripts/install.py --all

Vendored skills are copied from this repo. Link-only skills — those whose
upstream licence does not permit redistribution — are fetched directly from
their source repo at install time, so the copy lands on your machine under
your own licence to use the upstream software. Nothing restricted is ever
redistributed by this repo.

Default target is ~/.claude/skills (Claude Code). Override with --dest.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
SOURCES = ROOT / "sources.json"
DEFAULT_DEST = pathlib.Path.home() / ".claude" / "skills"


def load() -> list[dict]:
    if not SOURCES.exists():
        sys.exit("sources.json missing — run: python3 scripts/sync.py")
    return json.loads(SOURCES.read_text())["skills"]


def fetch(url: str) -> bytes | None:
    try:
        with urllib.request.urlopen(url, timeout=60) as r:
            return r.read()
    except Exception as exc:
        print(f"    fetch failed: {exc}")
        return None


def install_one(entry: dict, dest_root: pathlib.Path) -> bool:
    """Install a single skill as <dest>/<name>/SKILL.md."""
    dest = dest_root / entry["name"] / "SKILL.md"
    dest.parent.mkdir(parents=True, exist_ok=True)

    if entry["vendored"]:
        src = ROOT / "skills" / entry["category"] / f"{entry['name']}.md"
        if not src.exists():
            print(f"  !! vendored file missing: {src}")
            return False
        dest.write_bytes(src.read_bytes())
        print(f"  + {entry['name']}  (from repo, {entry['license']})")
        return True

    body = fetch(entry["raw_url"])
    if body is None:
        print(f"  !! {entry['name']}: could not fetch from upstream")
        return False
    note = (
        f"<!-- Fetched from {entry['source_repo']} @ {entry['source_ref']}\n"
        f"     Licence: {entry['license']} — not redistributable, fetched on demand.\n"
        f"     Source:  {entry['url']} -->\n\n"
    ).encode()
    dest.write_bytes(note + body)
    print(f"  + {entry['name']}  (fetched upstream, {entry['license']})")
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true", help="list available skills")
    ap.add_argument("--category", help="install every skill in a category")
    ap.add_argument("--name", action="append", help="install a named skill (repeatable)")
    ap.add_argument("--all", action="store_true", help="install everything")
    ap.add_argument("--dest", type=pathlib.Path, default=DEFAULT_DEST)
    args = ap.parse_args()

    skills = load()

    if args.list or not (args.category or args.name or args.all):
        by_cat: dict[str, list[dict]] = {}
        for s in skills:
            by_cat.setdefault(s["category"], []).append(s)
        for cat in sorted(by_cat):
            print(f"\n{cat}  ({len(by_cat[cat])})")
            for s in sorted(by_cat[cat], key=lambda e: e["name"]):
                tag = "" if s["vendored"] else "  [fetched on demand]"
                print(f"  {s['name']:<38} {s['license']}{tag}")
        print(f"\nTotal: {len(skills)} skills")
        print("\nInstall with:  python3 scripts/install.py --category isaac-sim")
        return 0

    if args.all:
        chosen = skills
    elif args.category:
        chosen = [s for s in skills if s["category"] == args.category]
    else:
        wanted = set(args.name or [])
        chosen = [s for s in skills if s["name"] in wanted]
        missing = wanted - {s["name"] for s in chosen}
        for m in sorted(missing):
            print(f"  !! no such skill: {m}")

    if not chosen:
        print("Nothing matched.")
        return 1

    print(f"Installing {len(chosen)} skill(s) into {args.dest}")
    ok = sum(install_one(s, args.dest) for s in chosen)
    print(f"\nInstalled {ok}/{len(chosen)}.")
    return 0 if ok == len(chosen) else 1


if __name__ == "__main__":
    raise SystemExit(main())
