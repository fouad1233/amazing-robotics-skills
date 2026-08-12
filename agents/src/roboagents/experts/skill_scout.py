# SPDX-License-Identifier: Apache-2.0
"""Catalogue scout — keeps the skill catalogue living."""

from __future__ import annotations

import json
from typing import ClassVar

from ..base import RoboAgent
from ..llm import Tier

#: The domains this workstation actually works in. Coverage is judged against
#: these, not against the catalogue's own category names — a category exists
#: because someone published skills, not because the work needs them.
WATCHED_DOMAINS: tuple[str, ...] = (
    "isaac-sim",
    "isaac-lab",
    "newton",
    "physx",
    "warp",
    "usd",
    "ros2",
    "lerobot",
    "vla",
    "manipulation",
    "teleop",
    "sim2real",
    "urdf",
    "perception",
    "jetson",
    "inference",
    "ml-infra",
    "huggingface",
    "visualization",
)

#: Below this many matching skills a domain is thin: enough to look covered in a
#: category listing, not enough to answer a real question without improvising.
THIN = 6


class SkillScoutAgent(RoboAgent):
    """Catalogue scout. You keep the skill catalogue useful: you find robotics
    skills worth adding and you report where coverage is thin, in evidence, with
    counts read off the index rather than impressions. You never copy a skill
    into this repository yourself — redistribution depends on the upstream
    licence, and only the SPDX ids in the allow-list may be vendored; everything
    else is catalogued link-only and fetched on demand. Propose the source repo
    and let `scripts/sync.py` apply the rule.
    """

    domain: ClassVar[str] = "skills"
    charter: ClassVar[str] = (
        "The skill catalogue itself: coverage gaps, robotics skills worth adding, "
        "sync health, and what may or may not be vendored."
    )
    skill_patterns: ClassVar[tuple[str, ...]] = (
        "meta-skills",
        "skill-inspector",
        "writing-skills",
        "nemoclaw-skills-guide",
        "skill-distillation",
        "capture-discovered-knowledge",
    )
    skill_categories: ClassVar[tuple[str, ...]] = ()
    tier: ClassVar[Tier] = "worker"

    # -- deterministic domain knowledge ----------------------------------

    def stats(self) -> str:
        """Catalogue size, per-category counts, and whether the last sync finished.

        The `missing vendored` list is catalogue entries that sources.json marks
        as vendored but whose file is not on disk. A non-empty list means
        `scripts/sync.py` did not finish — coverage numbers are understated until
        it is rerun, so do not report a gap on the strength of them.
        """
        catalogue = self._index
        rows = [f"{len(catalogue)} skills on disk under {catalogue.root / 'skills'}"]
        for category, count in catalogue.categories.items():
            rows.append(f"  {category:<18} {count}")

        missing = catalogue.missing_vendored
        if not missing:
            rows.append("missing vendored: none — the catalogue matches sources.json")
            return "\n".join(rows)

        rows.append(f"MISSING VENDORED ({len(missing)}) — sync.py did not finish:")
        rows += [f"  {name}" for name in missing[:20]]
        if len(missing) > 20:
            rows.append(f"  ... and {len(missing) - 20} more")
        rows.append(f"Rerun: {self.sync_command()}")
        return "\n".join(rows)

    def sync_command(self, dry_run: bool = False) -> str:
        """The exact command that refreshes the catalogue from upstream.

        Returns the command; it does not run it. Needs an authenticated `gh`
        (`gh auth status`) because discovery goes through the GitHub API. Run it
        with dry_run=True first: a real run rewrites sources.json and the whole
        skills tree, and a half-finished sweep is what leaves entries in
        `stats()`'s missing-vendored list. The subshell keeps `self.shell`'s
        persistent working directory where it was.
        """
        flag = " --dry-run" if dry_run else ""
        return f"(cd {self._index.root} && python3 scripts/sync.py{flag})"

    def gaps(self, domains: list[str] | None = None) -> str:
        """Which domains the catalogue barely covers.

        Defaults to the domains this workstation works in. Two numbers per
        domain, because they disagree in a way that matters: `category` counts
        skills filed under that name, `matches` counts every skill whose id, name
        or description mentions it. A domain with matches and no category is
        scattered across other people's repos and is a candidate for its own
        category; ABSENT with zero of both is a genuine hole worth sourcing.
        """
        catalogue = self._index
        categories = catalogue.categories
        rows = []
        for domain in domains or list(WATCHED_DOMAINS):
            key = domain.strip().lower()
            in_category = categories.get(key, 0)
            matches = len(catalogue.select([key]))
            if matches == 0:
                verdict = "ABSENT"
            elif matches < THIN:
                verdict = "thin"
            else:
                verdict = "covered"
            rows.append(f"{domain:<16} {verdict:<8} category={in_category:<5} matches={matches}")
        return "\n".join(rows)

    def vendoring_rules(self) -> str:
        """Which licences may be copied into this repository, read from sources.json.

        Read this before proposing anything. A skill whose upstream SPDX id is
        not on this list is catalogued link-only and fetched on demand — that is
        a complete outcome, not a failure. NOASSERTION means GitHub could not
        classify the LICENSE file, and it is never treated as permission; a human
        reads the licence and adds the repo to MANUAL_LICENCE_REVIEW in sync.py.
        Do not copy any skill file yourself: propose the repo, and let sync.py
        apply the rule.
        """
        sources = self._index.root / "sources.json"
        if not sources.is_file():
            return f"No sources.json at {sources} — the catalogue provenance is missing."
        try:
            data = json.loads(sources.read_text())
        except json.JSONDecodeError as exc:
            return f"sources.json is not valid JSON ({exc}) — rerun {self.sync_command()}"

        allowed = data.get("redistributable_licenses", [])
        counts = data.get("counts", {})
        return "\n".join(
            [
                "May be vendored (SPDX allow-list): " + (", ".join(allowed) or "(none)"),
                "Anything else, including NOASSERTION and every LicenseRef-, is link-only.",
                (
                    f"Current split: {counts.get('vendored', '?')} vendored, "
                    f"{counts.get('link_only', '?')} link-only."
                ),
                f"Orgs swept: {', '.join(data.get('orgs_scanned', [])) or '(none recorded)'}",
            ]
        )
