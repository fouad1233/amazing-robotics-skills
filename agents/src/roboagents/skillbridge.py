# SPDX-License-Identifier: Apache-2.0
"""Bridge the skill catalogue into the shape NOOA can load.

The catalogue stores skills as flat, per-category markdown files with a
provenance HTML comment above the YAML frontmatter::

    skills/isaac-sim/isaac-camera.md
        <!-- Vendored from isaac-sim/IsaacSim @ main ... -->
        ---
        name: isaac-camera
        description: ...
        ---
        <body>

NOOA's ``TextSkill`` wants a *directory* whose ``SKILL.md`` begins with
frontmatter, and derives the skill id from the directory name. So this module
materialises the catalogue into ``~/.cache/roboagents/skills/<id>/SKILL.md`` —
frontmatter first, provenance moved to the foot of the body where it stays
readable but no longer breaks the parser.

Materialisation is content-addressed and incremental: re-running it after a
``sync.py`` update only rewrites what changed.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from .config import cache_dir, catalogue_root

_FRONTMATTER = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_HTML_COMMENT = re.compile(r"^\s*<!--.*?-->\s*", re.DOTALL)
_NAME = re.compile(r"^name:\s*(.+?)\s*$", re.MULTILINE)
_DESCRIPTION = re.compile(r"^description:\s*(.*?)(?=^\S|\Z)", re.MULTILINE | re.DOTALL)


@dataclass(frozen=True)
class SkillEntry:
    """One catalogue skill, as it exists on disk."""

    id: str  # unique, safe for a directory name
    name: str  # frontmatter name (may collide across categories)
    category: str
    path: Path  # the flat .md file in the catalogue
    description: str = ""
    source_repo: str = ""
    source_url: str = ""
    license: str = ""

    @property
    def summary(self) -> str:
        """One line, for a router prompt. Long descriptions are truncated."""
        text = " ".join(self.description.split())
        return text[:200] + ("…" if len(text) > 200 else "")

    def matches(self, pattern: str) -> bool:
        """Substring match against id, name and category. Case-insensitive."""
        p = pattern.lower()
        return p in self.id.lower() or p in self.name.lower() or p in self.category.lower()


def _split(text: str) -> tuple[str, str, str]:
    """Return (provenance_comment, frontmatter_body, markdown_body)."""
    provenance = ""
    match = _HTML_COMMENT.match(text)
    if match:
        provenance = match.group(0).strip()
        text = text[match.end() :]

    fm = _FRONTMATTER.match(text)
    if fm:
        return provenance, fm.group(1), text[fm.end() :]
    return provenance, "", text


def _parse_description(frontmatter: str) -> str:
    match = _DESCRIPTION.search(frontmatter)
    if not match:
        return ""
    raw = match.group(1).strip()
    # folded/literal block scalars: `description: >` then an indented block
    if raw.startswith((">", "|")):
        raw = raw.split("\n", 1)[1] if "\n" in raw else ""
    return " ".join(raw.split())


def _yaml_quote(value: str) -> str:
    return json.dumps(value)


class SkillIndex:
    """The catalogue, indexed and ready to materialise.

    The filesystem is the source of truth — ``sources.json`` only supplies
    provenance. That way a partially-synced catalogue still works, and the gap
    is reported rather than silently swallowed.
    """

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or catalogue_root()
        self._entries: dict[str, SkillEntry] = {}
        self._provenance: dict[tuple[str, str], dict] = {}
        self._missing: list[str] = []
        self._load()

    # -- loading ---------------------------------------------------------

    def _load(self) -> None:
        sources = self.root / "sources.json"
        if sources.is_file():
            data = json.loads(sources.read_text())
            for record in data.get("skills", []):
                self._provenance[(record.get("category", ""), record.get("name", ""))] = record

        files = sorted((self.root / "skills").glob("*/*.md"))
        by_name: dict[str, list[tuple[str, Path, str, str]]] = {}
        for path in files:
            category = path.parent.name
            provenance, frontmatter, _body = _split(path.read_text(errors="replace"))
            name_match = _NAME.search(frontmatter) if frontmatter else None
            name = name_match.group(1).strip() if name_match else path.stem
            name = name.strip("\"'")
            by_name.setdefault(name, []).append(
                (category, path, _parse_description(frontmatter), provenance)
            )

        for name, hits in by_name.items():
            # Collisions across categories get a category-prefixed id so that
            # every skill still lands in its own directory.
            collide = len(hits) > 1
            for category, path, description, _prov in hits:
                skill_id = _slug(f"{category}-{name}" if collide else name)
                record = self._provenance.get((category, name), {})
                self._entries[skill_id] = SkillEntry(
                    id=skill_id,
                    name=name,
                    category=category,
                    path=path,
                    description=description or record.get("description", ""),
                    source_repo=record.get("source_repo", ""),
                    source_url=record.get("url", ""),
                    license=record.get("license", ""),
                )

        vendored = {
            (r.get("category"), r.get("name"))
            for r in self._provenance.values()
            if r.get("vendored")
        }
        present = {(e.category, e.name) for e in self._entries.values()}
        self._missing = sorted(f"{c}/{n}" for c, n in (vendored - present))

    # -- querying --------------------------------------------------------

    def __len__(self) -> int:
        return len(self._entries)

    def __iter__(self) -> Iterator[SkillEntry]:
        return iter(self._entries.values())

    def get(self, skill_id: str) -> SkillEntry | None:
        return self._entries.get(_slug(skill_id))

    @property
    def categories(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for entry in self._entries.values():
            counts[entry.category] = counts.get(entry.category, 0) + 1
        return dict(sorted(counts.items()))

    @property
    def missing_vendored(self) -> list[str]:
        """Catalogue entries marked vendored whose file is absent on disk.

        A non-empty list means ``scripts/sync.py`` did not finish. Surfaced by
        ``roboagents doctor`` instead of being silently ignored.
        """
        return list(self._missing)

    def select(
        self,
        patterns: Iterable[str] = (),
        *,
        categories: Iterable[str] = (),
        limit: int | None = None,
    ) -> list[SkillEntry]:
        """Skills matching any pattern or category, best-scoring first.

        Scoring favours an exact id hit, then a name hit, then a category hit,
        so that a narrow pattern beats a broad one when ``limit`` bites.
        """
        patterns = [p.lower() for p in patterns]
        categories = {c.lower() for c in categories}
        scored: list[tuple[int, SkillEntry]] = []

        for entry in self._entries.values():
            score = 0
            if entry.category.lower() in categories:
                score = max(score, 1)
            for pattern in patterns:
                if entry.id.lower() == pattern or entry.name.lower() == pattern:
                    score = max(score, 4)
                elif pattern in entry.id.lower() or pattern in entry.name.lower():
                    score = max(score, 3)
                elif pattern in entry.category.lower():
                    score = max(score, 2)
                elif pattern in entry.description.lower():
                    score = max(score, 1)
            if score:
                scored.append((score, entry))

        scored.sort(key=lambda pair: (-pair[0], pair[1].id))
        chosen = [entry for _score, entry in scored]
        return chosen[:limit] if limit else chosen

    # -- materialisation -------------------------------------------------

    def materialize(self, entries: Iterable[SkillEntry] | None = None) -> dict[str, Path]:
        """Write NOOA-loadable ``<id>/SKILL.md`` directories; return id -> dir.

        Incremental: a skill whose source file is unchanged is left alone.
        """
        target_root = cache_dir() / "skills"
        target_root.mkdir(parents=True, exist_ok=True)
        out: dict[str, Path] = {}

        for entry in entries if entries is not None else self._entries.values():
            directory = target_root / entry.id
            skill_md = directory / "SKILL.md"
            raw = entry.path.read_text(errors="replace")
            digest = hashlib.sha256(raw.encode()).hexdigest()[:16]
            stamp = directory / ".source-digest"

            if not (skill_md.is_file() and stamp.is_file() and stamp.read_text() == digest):
                directory.mkdir(parents=True, exist_ok=True)
                skill_md.write_text(self._render(entry, raw))
                stamp.write_text(digest)
            out[entry.id] = directory

        return out

    def _render(self, entry: SkillEntry, raw: str) -> str:
        """Frontmatter first, body next, provenance last."""
        provenance, frontmatter, body = _split(raw)

        if frontmatter and _NAME.search(frontmatter):
            # Rewrite `name:` so it agrees with the directory id NOOA derives.
            frontmatter = _NAME.sub(f"name: {entry.id}", frontmatter, count=1)
        else:
            description = entry.description or f"{entry.category} skill: {entry.name}"
            frontmatter = f"name: {entry.id}\ndescription: {_yaml_quote(description)}"

        footer = [""]
        if provenance:
            footer += ["---", "", provenance]
        if entry.source_url:
            footer += ["", f"Source: {entry.source_repo} — {entry.source_url}"]
        if entry.license:
            footer += [f"Licence: {entry.license}"]

        return f"---\n{frontmatter.strip()}\n---\n\n{body.strip()}\n" + "\n".join(footer) + "\n"

    def text_skills(self, entries: Iterable[SkillEntry]) -> dict[str, object]:
        """Materialise and wrap as NOOA ``TextSkill`` objects, keyed by id."""
        from nooa import TextSkill

        dirs = self.materialize(entries)
        return {skill_id: TextSkill(path=path) for skill_id, path in dirs.items()}


def _slug(value: str) -> str:
    """Directory-safe skill id: lowercase, hyphen-separated, no dots."""
    value = value.strip().lower()
    value = re.sub(r"\.(skill\.)?md$", "", value)
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-") or "skill"


@lru_cache(maxsize=1)
def index() -> SkillIndex:
    """Process-wide catalogue index."""
    return SkillIndex()
