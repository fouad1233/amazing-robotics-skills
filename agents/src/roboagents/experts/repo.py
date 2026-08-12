# SPDX-License-Identifier: Apache-2.0
"""Repository hygiene expert."""

from __future__ import annotations

import shlex
from pathlib import Path
from typing import ClassVar

from ..base import RoboAgent
from ..llm import Tier

#: House commit rules. Kept as data so the wording is identical everywhere it is
#: quoted — in a prompt, in a review, and in the message an agent actually writes.
COMMIT_RULES: tuple[str, ...] = (
    (
        "Subject in the imperative, under 72 characters, no trailing period: "
        "'Fix camera intrinsics for RtxCamera', not 'Fixed' or 'Fixes'."
    ),
    (
        "The body explains WHY, not what. The diff already says what changed; "
        "it cannot say which failure you were chasing or what you ruled out."
    ),
    (
        "One logical change per commit. A formatting sweep and a behaviour fix "
        "in the same commit cannot be reverted independently."
    ),
    (
        "Stage named paths, never '.'. Staging everything is how build output, "
        "notebooks and stray logs end up in history."
    ),
    (
        "Name the evidence in the body when the change was verified by running "
        "something — the command and its result, not 'tested locally'."
    ),
)

#: Directory names that must never be committed from the robotics root. These
#: sit next to real source and are tens of gigabytes: Isaac Sim installs, the
#: venvs pinned to each Isaac major version, and the Omniverse asset cache.
NEVER_COMMIT: tuple[str, ...] = (
    "isaacsim-*",
    "envs/",
    ".cache/",
    "*.usd",
    "*.usdc",
    "*.pt",
    "*.safetensors",
    "logs/",
)


class RepoAgent(RoboAgent):
    """Repository maintainer. Branch before you commit — `self.git` refuses a
    commit on main, and that refusal is the policy, not an obstacle to route
    around. Stage named paths so one commit carries one logical change, and
    never stage the multi-gigabyte neighbours in the robotics root: Isaac Sim
    installs, venvs, asset caches and checkpoints. Run the repository's own
    formatter first — in an Isaac Lab checkout that is `./isaaclab.sh -f`, which
    runs pre-commit inside Isaac Lab's environment, not bare `pre-commit`.
    """

    domain: ClassVar[str] = "repo"
    charter: ClassVar[str] = (
        "Branches, commits and commit messages, pull requests, changelogs and "
        "release notes, and repository hygiene across the checkouts on this machine."
    )
    skill_patterns: ClassVar[tuple[str, ...]] = (
        "create-github-pr",
        "pre-submit-pr",
        "changelog-audit",
        "update-docs-from-commits",
        "bump-version-and-release",
    )
    skill_categories: ClassVar[tuple[str, ...]] = ("ml-infra",)
    tier: ClassVar[Tier] = "worker"

    # -- deterministic domain knowledge ----------------------------------

    async def repos(self) -> str:
        """Every git checkout at the robotics root, with its branch and dirt.

        Reports one line per repository: branch and upstream tracking, then how
        many files are modified. Read this before touching anything — work
        started in the wrong checkout is the most common way an agent loses a
        change, and a repository that is already dirty needs the human's
        uncommitted work dealt with before you add to it.
        """
        rows: list[str] = []
        for path in self._repo_dirs():
            raw = await self.env._sh(
                f"git -C {shlex.quote(str(path))} status --porcelain=v1 --branch"
            )
            lines = raw.splitlines()
            if not lines:
                rows.append(f"{path.name}: git returned nothing (not a usable repo?)")
                continue
            if not lines[0].startswith("## "):
                # A fatal error (dubious ownership, corrupt index) prints here.
                rows.append(f"{path.name}: {lines[0].strip()}")
                continue
            branch = lines[0][3:].strip()
            changed = [line for line in lines[1:] if line.strip()]
            state = "clean" if not changed else f"{len(changed)} file(s) modified"
            rows.append(f"{path.name:<32} {branch:<40} {state}")
        return "\n".join(rows) or f"No git repositories at or under {self.workdir}"

    def commit_style(self) -> str:
        """The house rules for commits here. Follow these when you write a message.

        Also lists the paths that must never be staged from the robotics root.
        """
        rows = ["Commit rules:"]
        rows += [f"  {i}. {rule}" for i, rule in enumerate(COMMIT_RULES, 1)]
        rows.append("Never stage from the robotics root:")
        rows.append("  " + "  ".join(NEVER_COMMIT))
        return "\n".join(rows)

    def pre_commit_command(self, repo: str = "") -> str:
        """The formatter/lint command to run before committing in a repository.

        Returns the command; it does not run it. Defaults to the repository
        `self.git` is bound to. An Isaac Lab checkout must use `./isaaclab.sh -f`
        — bare `pre-commit` runs against the wrong interpreter and its hooks
        fail or, worse, reformat against the wrong style. The command is wrapped
        in a subshell because `self.shell` keeps its working directory between
        calls and a bare `cd` would silently move every later command.
        """
        root = Path(repo).expanduser() if repo else self.git.repo
        if (root / "isaaclab.sh").is_file():
            return f"(cd {shlex.quote(str(root))} && ./isaaclab.sh -f)"
        if (root / ".pre-commit-config.yaml").is_file():
            return f"(cd {shlex.quote(str(root))} && pre-commit run --all-files)"
        return f"# {root}: no pre-commit config and no isaaclab.sh — nothing to run"

    async def commits_since(self, ref: str = "origin/main", count: int = 50) -> str:
        """Commits on the current branch that a reference does not have.

        The raw material for a changelog or a pull-request description. Merges
        are excluded because they carry no subject of their own. If the ref does
        not exist you get git's error back rather than an empty list — an empty
        changelog and a typo'd ref look identical otherwise.
        """
        return await self.git.run(
            "log",
            "--no-merges",
            f"-{int(count)}",
            "--pretty=format:%h %an %ad %s",
            "--date=short",
            f"{ref}..HEAD",
        )

    # -- plumbing --------------------------------------------------------

    def _repo_dirs(self) -> list[Path]:
        """The robotics root and its immediate children that are git checkouts."""
        found: list[Path] = []
        if not self.workdir.is_dir():
            return found
        if (self.workdir / ".git").exists():
            found.append(self.workdir)
        for child in sorted(self.workdir.iterdir()):
            if child.is_dir() and (child / ".git").exists():
                found.append(child)
        return found
