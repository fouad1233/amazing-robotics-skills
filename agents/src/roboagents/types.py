# SPDX-License-Identifier: Apache-2.0
"""Typed contracts shared across experts, the router and the reviewer.

Return types are how NOOA constrains a model: the annotation becomes the output
schema, validation failures are fed back, and the call is retried. Local models
drift more than hosted ones, so every cross-agent hand-off goes through one of
these instead of through free text.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Confidence = Literal["low", "medium", "high"]


class Step(BaseModel):
    """One concrete action in a plan."""

    action: str = Field(description="What to do, in one imperative sentence")
    command: str = Field(default="", description="Exact shell command, if there is one")
    verifies: str = Field(default="", description="How to tell this step actually worked")


class Plan(BaseModel):
    """An expert's proposed approach to a task."""

    summary: str = Field(description="One sentence: what will be done")
    steps: list[Step] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list, description="What could go wrong")
    needs_human: bool = Field(
        default=False,
        description="True when the task needs physical access, a reboot, or a "
        "decision with more than one reasonable answer",
    )


class Finding(BaseModel):
    """Something an expert discovered while investigating."""

    title: str
    detail: str
    evidence: str = Field(default="", description="Command output or file excerpt that shows it")
    confidence: Confidence = "medium"


class WorkResult(BaseModel):
    """What an expert did, and whether it proved it."""

    summary: str = Field(description="What was done, in plain language")
    succeeded: bool
    evidence: str = Field(
        default="", description="Command output proving the outcome — not a restatement"
    )
    findings: list[Finding] = Field(default_factory=list)
    files_changed: list[str] = Field(default_factory=list)
    follow_up: list[str] = Field(default_factory=list)


class Verdict(BaseModel):
    """The reviewer's adversarial check on a claimed result."""

    holds_up: bool = Field(description="False if the claim is unsupported by the evidence")
    reason: str
    missing_evidence: list[str] = Field(default_factory=list)
    severity: Literal["none", "minor", "major"] = "none"


class Assignment(BaseModel):
    """One expert, given one slice of a task."""

    expert: str = Field(description="Class name of the expert from the roster")
    task: str = Field(description="The slice of work for that expert, self-contained")
    skills: list[str] = Field(
        default_factory=list, description="Catalogue skill ids or patterns to activate"
    )
    depends_on: list[int] = Field(
        default_factory=list, description="Indices of assignments that must finish first"
    )


class RoutePlan(BaseModel):
    """How the orchestrator splits a request across the roster."""

    rationale: str = Field(description="Why these experts, in one or two sentences")
    assignments: list[Assignment] = Field(default_factory=list)
    parallel: bool = Field(
        default=True, description="False when the assignments must run in order"
    )
