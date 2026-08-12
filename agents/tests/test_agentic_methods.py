# SPDX-License-Identifier: Apache-2.0
"""Guard against the docstring-only regression.

On 2026-08-12, ``ruff check --fix`` silently deleted the ``...`` body from
every agentic method it touched, because its PIE790 rule reads a bare
Ellipsis after a docstring as "unnecessary". In NOOA that Ellipsis is not
decorative — ``AgentMeta`` keys ``needs_generation`` off it being literally
there. Strip it and the method still imports, still type-checks, still passes
every existing lint rule; it just quietly returns ``None`` forever instead of
ever calling the model. Nothing at import time or lint time catches that.

This test is the catch: it parses every module under ``roboagents`` with
``ast`` (no import, no LLM, no network) and fails on any method whose entire
body is its docstring — since a real deterministic helper always has more
than that, and every agentic method here is deliberately docstring-then-`...`.

``pyproject.toml`` also ignores PIE790 project-wide now, so a future
``ruff --fix`` cannot reintroduce this. This test is the belt to that braces:
it catches a hand-edit that drops the ellipsis by some other route.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "roboagents"


def _docstring_only_methods(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    hits = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
            continue
        body = node.body
        if not body:
            continue
        first = body[0]
        is_docstring = (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        )
        if is_docstring and not body[1:]:
            hits.append(f"{node.name}() at line {node.lineno}")
    return hits


def test_no_docstring_only_methods_anywhere():
    offenders: dict[str, list[str]] = {}
    for path in sorted(SRC.rglob("*.py")):
        hits = _docstring_only_methods(path)
        if hits:
            offenders[str(path.relative_to(SRC.parents[1]))] = hits

    assert not offenders, (
        "Method(s) with nothing but a docstring for a body — if these are "
        "meant to be agentic (`...` after the docstring), the ellipsis is "
        "missing and the method will silently return None:\n"
        + "\n".join(f"  {file}: {', '.join(names)}" for file, names in offenders.items())
    )


def test_ruff_config_ignores_pie790():
    """The specific rule that caused this must stay off, or this regresses."""
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    text = pyproject.read_text()
    assert '"PIE790"' in text, (
        "pyproject.toml no longer ignores ruff's PIE790 — `ruff check --fix` "
        "will again delete the `...` body of every agentic method it touches."
    )
