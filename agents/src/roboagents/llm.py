# SPDX-License-Identifier: Apache-2.0
"""Model provisioning — local first.

Three tiers, so an orchestrator can spend a big model where judgement matters
and a small one where it does not:

    planner  — routing, decomposition, review. Needs the most reasoning.
    worker   — the domain experts doing the actual work.
    fast     — classification, summarisation, cheap structured extraction.

By default all three resolve to whatever Ollama is serving locally. Point them
at different models in ``~/.config/roboagents/models.yaml``::

    planner: qwen3-coder:30b
    worker:  qwen3-coder:30b
    fast:    qwen3:8b

Note on Kimi: Kimi K2 is a ~1T-parameter MoE with 32B *active* parameters. The
"32B" is the active count, not a downloadable size — there is no 32B Kimi to
pull, and on Ollama it exists only as a `:cloud` tag, which is not local.

An entry may also be a full mapping for non-Ollama endpoints::

    planner:
      model: nvidia_nim/moonshotai/kimi-k2-instruct
      api_key_env: NVIDIA_API_KEY   # hosted; leaves the machine
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Literal

from .config import config_dir

Tier = Literal["planner", "worker", "fast"]
TIERS: tuple[Tier, ...] = ("planner", "worker", "fast")

OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/")

#: Preference order when auto-picking an Ollama model. Matched as a substring
#: against the model tag, best first. Agentic work is mostly code generation
#: (NOOA's default strategy is CodeAct), so coder-tuned models rank high.
_PREFERRED = (
    "kimi",
    "qwen3-coder",
    "qwen2.5-coder",
    "deepseek-coder",
    "glm-4",
    "devstral",
    "codestral",
    "qwen3",
    "llama3.3",
    "mistral-small",
    "gemma3",
)


@dataclass(frozen=True)
class ModelSpec:
    """A resolved model, in litellm terms."""

    model: str
    api_base: str | None = None
    api_key_env: str | None = None
    #: Rough usable context. Used to size skill activation and truncation.
    context_window: int = 32_768

    @property
    def is_local(self) -> bool:
        return self.model.startswith(("ollama/", "ollama_chat/"))

    def describe(self) -> str:
        where = "local" if self.is_local else (self.api_base or "hosted")
        return f"{self.model} ({where})"


class OllamaUnavailable(RuntimeError):
    """Raised when no local model server is reachable and no fallback is set."""


# --------------------------------------------------------------------------
# Ollama discovery
# --------------------------------------------------------------------------


def ollama_tags(timeout: float = 2.0) -> list[dict[str, Any]]:
    """Models currently pulled into Ollama. Empty list if the server is down."""
    try:
        with urllib.request.urlopen(f"{OLLAMA_HOST}/api/tags", timeout=timeout) as resp:
            return json.loads(resp.read()).get("models", [])
    except (urllib.error.URLError, OSError, json.JSONDecodeError, TimeoutError):
        return []


def ollama_is_up(timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(f"{OLLAMA_HOST}/api/tags", timeout=timeout):
            return True
    except (urllib.error.URLError, OSError, TimeoutError):
        return False


def start_ollama(wait: float = 20.0) -> bool:
    """Best-effort start of a local Ollama server. Returns True if it is up.

    Tries the systemd unit first (the packaged install path), then falls back to
    a detached ``ollama serve``. Never raises — callers decide what to do.
    """
    if ollama_is_up():
        return True

    if shutil.which("systemctl"):
        subprocess.run(
            ["systemctl", "start", "ollama"],
            capture_output=True,
            check=False,
            timeout=30,
        )
        if _wait_for_ollama(5.0):
            return True

    if shutil.which("ollama"):
        log = open(os.devnull, "wb")  # noqa: SIM115 - handed to a detached child
        subprocess.Popen(
            ["ollama", "serve"],
            stdout=log,
            stderr=log,
            start_new_session=True,
        )
        return _wait_for_ollama(wait)

    return False


def _wait_for_ollama(timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if ollama_is_up(timeout=1.0):
            return True
        time.sleep(0.5)
    return False


def _rank(tag: str) -> tuple[int, int]:
    """Sort key: preference index, then bigger parameter count first."""
    name = tag.lower()
    pref = next((i for i, p in enumerate(_PREFERRED) if p in name), len(_PREFERRED))
    size = 0
    for token in name.replace(":", "-").split("-"):
        if token.endswith("b") and token[:-1].replace(".", "", 1).isdigit():
            size = max(size, int(float(token[:-1])))
    return (pref, -size)


def best_local_model() -> str | None:
    """The most capable Ollama model currently pulled, or None."""
    tags = [m.get("name", "") for m in ollama_tags() if m.get("name")]
    if not tags:
        return None
    return min(tags, key=_rank)


def _context_window_for(tag: str) -> int:
    """Conservative usable context, keyed off the model family."""
    name = tag.lower()
    if "kimi" in name:
        return 128_000
    if "qwen3" in name or "glm-4" in name or "devstral" in name:
        return 64_000
    if "llama3.3" in name or "mistral" in name:
        return 32_768
    return 32_768


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------


def _models_yaml() -> dict[str, Any]:
    path = config_dir() / "models.yaml"
    if not path.is_file():
        return {}
    try:
        import yaml

        return yaml.safe_load(path.read_text()) or {}
    except (OSError, ImportError, ValueError) as exc:
        # A broken or unreadable config must not take the whole CLI down; the
        # `doctor` subcommand is often the thing you run *because* it is broken.
        print(f"warning: ignoring {path}: {exc}")
        return {}


def _spec_from_entry(entry: Any) -> ModelSpec:
    """Turn a models.yaml value into a ModelSpec.

    A bare string is interpreted as an Ollama tag unless it already carries a
    litellm provider prefix (``provider/model``).
    """
    if isinstance(entry, str):
        if "/" in entry:
            return ModelSpec(model=entry)
        return ModelSpec(
            model=f"ollama_chat/{entry}",
            api_base=OLLAMA_HOST,
            context_window=_context_window_for(entry),
        )
    if isinstance(entry, dict):
        model = entry["model"]
        return ModelSpec(
            model=model,
            api_base=entry.get("api_base"),
            api_key_env=entry.get("api_key_env"),
            context_window=int(entry.get("context_window", _context_window_for(model))),
        )
    raise TypeError(f"Unsupported model entry: {entry!r}")


@lru_cache(maxsize=8)
def resolve(tier: Tier = "worker") -> ModelSpec:
    """Resolve a tier to a concrete model.

    Order: ``ROBOAGENTS_<TIER>_MODEL`` env var, then ``models.yaml``, then the
    best model Ollama currently has, then a hosted fallback if an API key is set.
    """
    env = os.environ.get(f"ROBOAGENTS_{tier.upper()}_MODEL")
    if env:
        return _spec_from_entry(env)

    configured = _models_yaml().get(tier)
    if configured:
        return _spec_from_entry(configured)

    local = best_local_model()
    if local:
        return _spec_from_entry(local)

    for env_key, model in (
        ("NVIDIA_API_KEY", "nvidia_nim/moonshotai/kimi-k2-instruct"),
        ("ANTHROPIC_API_KEY", "claude-sonnet-4-5"),
        ("OPENAI_API_KEY", "gpt-4o-mini"),
    ):
        if os.environ.get(env_key):
            return ModelSpec(model=model, api_key_env=env_key, context_window=128_000)

    raise OllamaUnavailable(
        f"No model available. Start Ollama and pull one "
        f"(e.g. `ollama pull qwen3-coder:30b`), or set ROBOAGENTS_{tier.upper()}_MODEL."
    )


def get_llm(tier: Tier = "worker", **overrides: Any):
    """Build a NOOA client for a tier.

    Imported lazily so that ``roboagents doctor`` and the tests can inspect model
    resolution without pulling in litellm.
    """
    from nooa.unifiedllm import CompletionClient

    spec = resolve(tier)
    config: dict[str, Any] = dict(overrides)
    if spec.api_base:
        config.setdefault("api_base", spec.api_base)
    if spec.api_key_env:
        key = os.environ.get(spec.api_key_env)
        if key:
            config.setdefault("api_key", key)
    elif spec.is_local:
        # litellm still wants *something* for OpenAI-compatible endpoints.
        config.setdefault("api_key", "ollama")

    if spec.is_local:
        # NOOA's CodeAct strategy sends `parallel_tool_calls`, which litellm's
        # `ollama_chat` provider rejects outright:
        #   UnsupportedParamsError: ollama_chat does not support parameters:
        #   ['parallel_tool_calls'], for model=qwen3-coder:30b
        # That kills every agentic call after three retries. Dropping unsupported
        # params is exactly the documented remedy, and it is safe here because
        # the parameters in question only ever *narrow* tool-calling behaviour —
        # losing them costs nothing against a single-tool-call-at-a-time model.
        config.setdefault("drop_params", True)

    return CompletionClient(model=spec.model, **config)


def status() -> dict[str, Any]:
    """Everything ``roboagents doctor`` needs to explain the model setup."""
    tags = [m.get("name") for m in ollama_tags()]
    out: dict[str, Any] = {
        "ollama_host": OLLAMA_HOST,
        "ollama_up": bool(tags) or ollama_is_up(),
        "ollama_models": tags,
        "tiers": {},
    }
    for tier in TIERS:
        try:
            out["tiers"][tier] = resolve(tier).describe()
        except OllamaUnavailable as exc:
            out["tiers"][tier] = f"unresolved: {exc}"
    return out
