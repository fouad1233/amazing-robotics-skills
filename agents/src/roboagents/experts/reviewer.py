# SPDX-License-Identifier: Apache-2.0
"""Adversarial reviewer."""

from __future__ import annotations

import re
import shlex
from typing import ClassVar

from ..base import RoboAgent
from ..llm import Tier
from ..types import Finding, Verdict

#: Patterns that have actually produced a false "it worked" on this machine,
#: with what each one really proves. Regex rather than prompt: a model asked to
#: spot these misses them exactly when the evidence is long enough to matter.
SMELLS: tuple[tuple[str, str], ...] = (
    (
        r"\|\s*(tail|head|less|more)\b",
        (
            "exit status read through a pipe: $? is the pager's status, so a "
            "failed command reads as success. This already shipped a broken "
            "Isaac Sim install as 'installed'. Re-run under `bash -o pipefail`."
        ),
    ),
    (
        r"\bp(grep|kill)\s+-f\b",
        (
            "`pgrep -f` / `pkill -f` match the invoking shell's own command "
            "line, so the pattern finds itself. A count of 1 usually means zero "
            "real processes. Use `pgrep -x <name>` or an explicit "
            "`ps -eo args | grep -c '^...'`."
        ),
    ),
    (
        r"is_available\(\)",
        (
            "`torch.cuda.is_available()` returns True on a cu126 wheel that then "
            "dies at the first kernel launch on sm_120. Only a real matmul on "
            "the device proves CUDA works here."
        ),
    ),
    (
        r"2\s*>\s*/dev/null|\|\|\s*true|\|\|\s*:",
        (
            "the failure path was swallowed, so this output is identical whether "
            "the command succeeded or not."
        ),
    ),
    (
        r"--dry-run|--check\b|\bnoop\b",
        (
            "a dry run changes nothing. It proves the plan parses, not that the "
            "work was done."
        ),
    ),
    (
        r"\b(should|presumably|expected to|ought to|likely|appears to)\b",
        (
            "hedged prose where a command's output belongs. Hedging is an "
            "assertion wearing evidence's clothes."
        ),
    ),
    (
        r"^\s*(echo|printf)\b.*\b(ok|success|done|passed|complete)",
        (
            "an echoed success message. The string was printed unconditionally; "
            "it says nothing about the command before it."
        ),
    ),
    (
        r"nvidia-smi",
        (
            "`nvidia-smi` reports healthy across a driver/userspace mismatch. "
            "Only `cuInit(0)` returning 0 proves CUDA initialises — see "
            "`await self.env.cuda_health()`."
        ),
    ),
)

#: What actually counts as proof for the claims that recur on this workstation.
#: Each entry is the one command whose output settles the question.
PROOF: dict[str, str] = {
    "cuda": (
        "A real matmul on the device, not a capability flag:\n"
        "  python -c \"import torch;x=torch.randn(64,64,device='cuda');"
        'print((x@x).sum().item())"\n'
        "RTX 5090 is sm_120 and needs a cu128 build; a cu126 wheel imports, "
        "reports is_available() True, then dies at the first kernel launch."
    ),
    "driver": (
        "cuInit(0) returning 0 — `await self.env.cuda_health()`. Error 803 is a "
        "kernel-module/userspace version mismatch and needs a reboot; nvidia-smi "
        "looks perfectly healthy while it is true."
    ),
    "isaac-sim": (
        "A headless run that exits 0 and prints the Kit version banner, launched "
        "through that build's own python.sh. Isaac Sim 5.x must be driven with "
        "Python 3.11 and 6.x with 3.12 — a run under the wrong interpreter fails "
        "in imports far from the real cause."
    ),
    "install": (
        "An import from the exact interpreter that will run the code:\n"
        '  /path/to/venv/bin/python -c "import pkg; print(pkg.__version__)"\n'
        "`pip list` in an unnamed shell proves nothing about which venv was hit."
    ),
    "process": (
        "`pgrep -x <name>` or `ps -eo args | grep -c '^python.*script\\.py'`. "
        "`pgrep -f` matches its own command line and reports phantoms."
    ),
    "download": (
        "The file's size and checksum on disk (`ls -l`, `sha256sum`). A curl that "
        "printed a progress bar is not a completed download; resume with "
        "`curl -C -` rather than assuming."
    ),
    "commit": (
        "`git log -1 --stat` on the branch that was supposed to receive it, plus "
        "`git status --porcelain` coming back empty. A commit refused on a "
        "protected branch prints a refusal and leaves the work staged."
    ),
}


class ReviewerAgent(RoboAgent):
    """Adversarial reviewer. You do not implement anything; you decide whether
    other experts proved what they claimed, and you start from the assumption
    that they did not. Evidence has to show the claimed thing happening — a
    command that was never run, output about something adjacent, or a success
    asserted with no output at all is not evidence. When one command would
    settle it, run that command yourself and quote what you got.
    """

    domain: ClassVar[str] = "review"
    charter: ClassVar[str] = (
        "Adversarial verification of other experts' claims, plans and evidence: "
        "does the output actually demonstrate what was asserted."
    )
    skill_patterns: ClassVar[tuple[str, ...]] = (
        "review-checklist",
        "review-github-pr",
        "nemoclaw-maintainer-security-code-review",
        "alignment-review",
        "testing-conventions",
    )
    skill_categories: ClassVar[tuple[str, ...]] = ("ml-infra",)
    tier: ClassVar[Tier] = "planner"

    # -- review ----------------------------------------------------------

    async def verify(self, claim: str, evidence: str) -> Verdict:
        """Decide whether the evidence actually demonstrates the claim.

        Default to NOT holding up. Set holds_up only when the evidence shows the
        claimed thing itself happening. It does not hold up when: the command
        that would prove it was never run; the output proves something adjacent
        (the package imports, but the claim was that it runs on the GPU); success
        is asserted with no output at all; or an exit status was read through a
        pipe, because piping into tail or head makes $? the pager's status and a
        failed command reads as success.

        Run `self.evidence_smells(evidence)` first — it names the traps that have
        already produced false successes here — and `self.standard_of_proof(...)`
        for what would settle this class of claim. When one command would decide
        it, run it with `await self.recheck(...)` and quote the real output in
        your reason. List every gap in missing_evidence as the exact command that
        would close it. Severity is major when acting on the claim would break
        something or waste a rebuild, minor when the claim is probably true but
        under-evidenced.
        """
        ...  # noqa: PIE790 - the ellipsis IS the body; NOOA reads it as agentic

    async def critique(self, plan_summary: str) -> list[Finding]:
        """Find what is wrong with this plan, worst first.

        Attack the plan, not its wording. Look for: a step whose check cannot
        fail; a step that would report success on a machine where the work never
        happened; verification ordered before the thing it verifies exists; a
        destructive step with no way back; an assumption about this workstation
        that nobody read off it. Each finding names the step and what it would
        cost, and quotes the plan text it objects to in evidence. Returning an
        empty list is a real answer — say nothing rather than manufacture a
        concern about a sound plan.
        """
        ...  # noqa: PIE790 - the ellipsis IS the body; NOOA reads it as agentic

    # -- deterministic domain knowledge ----------------------------------

    def evidence_smells(self, evidence: str) -> str:
        """Scan an evidence blob for the ways proof has been faked here before.

        Call this before judging any claim. Each hit names a pattern that has
        actually produced a false success on this workstation and says what the
        output really proves. A clean scan is not proof — it only means these
        specific traps are absent.
        """
        text = evidence or ""
        if not text.strip():
            return "EMPTY: no evidence at all. Nothing to verify — the claim fails."

        hits = [
            f"- {message}"
            for pattern, message in SMELLS
            if re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
        ]
        if len(text.strip()) < 40:
            hits.append(
                "- the evidence is a single short line. Real command output is "
                "rarely one sentence; this is likely a summary of a result, not "
                "the result."
            )
        if not hits:
            return "No known evidence smells. This is not proof — judge the content."
        return "\n".join([f"{len(hits)} problem(s) in this evidence:", *hits])

    async def recheck(self, command: str, timeout: float = 120.0) -> str:
        """Re-run a check yourself and report its true exit status.

        Use this when one command would settle a claim. The command runs under
        `bash -o pipefail` in its own process, so a failure inside a pipeline is
        reported instead of being masked by the last stage, and no shell option
        or working directory leaks into `self.shell`'s persistent session. The
        returned exit status is the real one — quote it in your verdict.
        """
        result = await self.shell.run(
            "bash -o pipefail -c " + shlex.quote(command), timeout=timeout
        )
        status = "PASS" if result.success else "FAIL"
        return f"$ {command}\n{result}\n[{status}] real exit status: {result.returncode}"

    def standard_of_proof(self, claim_kind: str = "") -> str:
        """What counts as proof for a class of claim on this machine.

        Pass one of: cuda, driver, isaac-sim, install, process, download, commit.
        Omit it to see all of them. Each entry is the command whose output
        settles the question, and why the obvious cheaper check does not.
        """
        key = claim_kind.strip().lower()
        if key and key in PROOF:
            return f"{key}:\n{PROOF[key]}"
        if key:
            return (
                f"No standard recorded for {claim_kind!r}. Known kinds: "
                + ", ".join(PROOF)
                + ". Judge it on the general rule: the evidence must show the "
                "claimed thing happening, from the exact environment that will "
                "run it."
            )
        return "\n\n".join(f"{name}:\n{body}" for name, body in PROOF.items())
