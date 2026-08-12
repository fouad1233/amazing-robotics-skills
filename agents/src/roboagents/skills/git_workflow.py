# SPDX-License-Identifier: Apache-2.0
"""Git control for agents — safe by construction.

The agent never gets a raw shell for git. It gets these methods, which enforce
the rules in deterministic Python:

* commits never land on a protected branch — you must branch first;
* pushing requires the policy to allow it;
* history rewrites, force pushes, hard resets and branch deletion are refused
  outright unless ``allow_destructive_git`` is set;
* every command runs against one fixed repository, chosen by the caller.

Guardrails that live in a prompt are suggestions. These are not.
"""

from __future__ import annotations

import asyncio
import shlex
from dataclasses import dataclass
from pathlib import Path

from nooa import Skill

from ..config import Policy

#: Subcommands and flag combinations that can destroy work irrecoverably.
_DESTRUCTIVE = (
    ("push", "--force"),
    ("push", "-f"),
    ("reset", "--hard"),
    ("clean", "-fd"),
    ("clean", "-fdx"),
    ("branch", "-D"),
    ("filter-branch",),
    ("rebase",),
)


@dataclass
class GitResult:
    """Outcome of one git invocation."""

    command: str
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    def __str__(self) -> str:
        body = self.stdout.strip() or self.stderr.strip() or "(no output)"
        return body if self.ok else f"[git exit {self.returncode}] {body}"


class GitWorkflow(Skill):
    """Version control for this repository.

    Work on a branch, commit with a real message, and prove the change before
    you claim it works.

        await self.git.status()
        await self.git.create_branch("agent/fix-camera-intrinsics")
        await self.git.stage(["src/camera.py"])
        await self.git.commit("Fix camera intrinsics for RtxCamera")
        print(await self.git.diff())            # review before committing
        await self.git.push()                   # only if the policy allows it

    Refused unless explicitly permitted: force push, hard reset, branch
    deletion, rebase, filter-branch, and committing to a protected branch.
    """

    def __init__(self, repo: Path | str, policy: Policy | None = None) -> None:
        # No `content=` — see the note in WorkspaceSkill.__init__. Passing it
        # would swap this class out for a bare Skill and every guard below
        # would quietly disappear, which is the worst possible failure here.
        super().__init__()
        self.repo = Path(repo).expanduser().resolve()
        self.policy = policy or Policy.default()

    # -- plumbing --------------------------------------------------------

    async def _run(self, *args: str, timeout: float = 120.0) -> GitResult:
        command = "git " + " ".join(shlex.quote(a) for a in args)
        process = await asyncio.create_subprocess_exec(
            "git",
            "-C",
            str(self.repo),
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            out, err = await asyncio.wait_for(process.communicate(), timeout=timeout)
        except TimeoutError:
            process.kill()
            return GitResult(command, 124, "", f"timed out after {timeout}s")
        return GitResult(command, process.returncode or 0, out.decode(), err.decode())

    def _refuse_if_destructive(self, args: tuple[str, ...]) -> str | None:
        if self.policy.allow_destructive_git:
            return None
        lowered = [a.lower() for a in args]
        for pattern in _DESTRUCTIVE:
            if all(token in lowered for token in pattern):
                return (
                    f"Refused: `git {' '.join(pattern)}` is destructive and "
                    "allow_destructive_git is False. Ask the human to run it."
                )
        return None

    # -- read-only -------------------------------------------------------

    async def status(self) -> str:
        """Short working-tree status plus the current branch."""
        branch = await self.current_branch()
        result = await self._run("status", "--short", "--branch")
        return f"branch: {branch}\n{result}"

    async def current_branch(self) -> str:
        """Name of the checked-out branch."""
        result = await self._run("rev-parse", "--abbrev-ref", "HEAD")
        return result.stdout.strip() or "(detached)"

    async def diff(self, staged: bool = False, paths: list[str] | None = None) -> str:
        """Unified diff of the working tree, or of the index when staged=True."""
        args = ["diff"]
        if staged:
            args.append("--cached")
        if paths:
            args += ["--", *paths]
        return str(await self._run(*args))

    async def log(self, count: int = 10) -> str:
        """Recent commits, one per line."""
        return str(await self._run("log", f"-{int(count)}", "--oneline", "--no-decorate"))

    async def show_file(self, path: str, ref: str = "HEAD") -> str:
        """Contents of a file at a given ref — useful for comparing against HEAD."""
        return str(await self._run("show", f"{ref}:{path}"))

    async def is_clean(self) -> bool:
        """True when there is nothing to commit."""
        result = await self._run("status", "--porcelain")
        return result.ok and not result.stdout.strip()

    # -- mutating --------------------------------------------------------

    async def create_branch(self, name: str, base: str | None = None) -> str:
        """Create and check out a branch. Names are prefixed ``agent/`` if bare."""
        if not name.strip():
            return "Refused: branch name is empty."
        if "/" not in name:
            name = f"agent/{name}"
        args = ["checkout", "-b", name]
        if base:
            args.append(base)
        return str(await self._run(*args))

    async def checkout(self, ref: str) -> str:
        """Switch to an existing branch or ref."""
        return str(await self._run("checkout", ref))

    async def stage(self, paths: list[str]) -> str:
        """Stage specific paths. Staging everything with '.' is allowed but noisy."""
        if not paths:
            return "Refused: pass the paths to stage explicitly."
        return str(await self._run("add", "--", *paths))

    async def commit(self, message: str, allow_protected: bool = False) -> str:
        """Commit the staged changes.

        Refuses on a protected branch unless the caller has already branched.
        """
        if not message.strip():
            return "Refused: empty commit message."

        branch = await self.current_branch()
        if branch in self.policy.protected_branches and not allow_protected:
            return (
                f"Refused: {branch!r} is protected. "
                f"Run create_branch('agent/<topic>') first, then commit."
            )

        staged = await self._run("diff", "--cached", "--name-only")
        if not staged.stdout.strip():
            return "Refused: nothing staged. Call stage([...]) first."

        return str(await self._run("commit", "-m", message))

    async def push(self, remote: str = "origin", set_upstream: bool = True) -> str:
        """Push the current branch. Requires ``allow_push`` in the policy."""
        if not self.policy.allow_push:
            return (
                "Refused: pushing is disabled by policy. The change is committed "
                "locally — a human can review and push it."
            )
        branch = await self.current_branch()
        args = ["push"]
        if set_upstream:
            args += ["--set-upstream"]
        args += [remote, branch]
        return str(await self._run(*args, timeout=300.0))

    async def run(self, *args: str) -> str:
        """Escape hatch for read-only git commands not covered above.

        Destructive invocations are refused here too, so this cannot be used to
        route around the other methods.
        """
        refusal = self._refuse_if_destructive(args)
        if refusal:
            return refusal
        return str(await self._run(*args))
