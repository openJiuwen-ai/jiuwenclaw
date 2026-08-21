# coding: utf-8
"""Reasoning levels, provider detection, and per-provider level tables.

``reasoning_level`` (``off|low|medium|high``) is product-facing, not a provider
parameter. Mapping:

| level      | OpenAI                       | Anthropic                 | DeepSeek / DashScope              |
| ---------- | ---------------------------- | ------------------------- | --------------------------------- |
| ``off``    | omit ``reasoning_effort``    | omit ``thinking``         | thinking disabled                 |
| ``low``    | ``reasoning_effort=low``     | ``budget_tokens=1024``    | thinking enabled                  |
| ``medium`` | ``reasoning_effort=medium``  | ``budget_tokens=8000``    | thinking enabled                  |
| ``high``   | ``reasoning_effort=high``    | ``budget_tokens=16000``   | thinking enabled + effort         |

Anthropic Claude 4.6+ minors are excluded (adaptive thinking / budget reject).
Anthropic payloads also need agent-core to forward ``thinking``.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Any, Literal
from urllib.parse import urlparse


ReasoningProviderKind = Literal[
    "deepseek_official",
    "dashscope_bailian",
    "openai_reasoning",
    "anthropic",
]
ReasoningLevel = Literal["off", "low", "medium", "high"]

# Anthropic is detected separately; do not gate it on this set.
OPENAI_SDK_REASONING_PROVIDERS = {
    "openai",
    "deepseek",
    "dashscope",
}

SUPPORTED_DEEPSEEK_V4_MODELS = {
    "deepseek-v4-pro",
    "deepseek-v4-flash",
}

ANTHROPIC_PROVIDER_NAMES = {"anthropic", "claude"}
ANTHROPIC_API_HOSTS = {"api.anthropic.com"}
OPENAI_API_HOSTS = {"api.openai.com"}

# Models that accept thinking.budget_tokens (Claude 3.7 / 4.x below 4.6).
ANTHROPIC_THINKING_MODEL_PREFIXES: tuple[str, ...] = (
    "claude-3-7",
    "claude-opus-4",
    "claude-sonnet-4",
    "claude-haiku-4",
)

# Match on hyphen boundary so "o1" does not match "o1lama".
OPENAI_REASONING_MODEL_PREFIXES: tuple[str, ...] = (
    "o1",
    "o3",
    "o4",
    "gpt-5",
)

# ChatGPT-instant ids inside the gpt-5 family (``gpt-5-chat-latest``,
# ``gpt-5.1-chat``, ...). These are not reasoning models and 400 on
# ``reasoning_effort``, the same reason ``gpt-4o`` is refused below.
_GPT5_CHAT_VARIANT_RE = re.compile(r"(?:^|[-.])chat(?:[-.]|$)")

ANTHROPIC_BUDGET_TOKENS: dict[str, int] = {
    "low": 1024,
    "medium": 8000,
    "high": 16000,
}

# Anthropic requires budget_tokens < max_tokens; keep room for the answer.
ANTHROPIC_MAX_TOKENS_HEADROOM = 4096

# Claude 4.x: ``-4``, ``-4-5``, or date stamp; minor >= 6 is excluded.
_CLAUDE_4_FAMILY_RE = re.compile(r"^claude-(?:opus|sonnet|haiku)-4(?:-(\d+))?(?:-|$)")


def _normalize_provider(provider: Any) -> str:
    if isinstance(provider, Enum):
        provider = provider.value
    return str(provider or "").strip().lower()


def _parse_api_base(api_base: str | None):
    value = str(api_base or "").strip()
    if value and "://" not in value:
        value = f"https://{value}"
    return urlparse(value)


def _host_of(api_base: str | None) -> str:
    return (_parse_api_base(api_base).hostname or "").lower()


def resolve_reasoning_provider_kind(
    api_base: str | None,
) -> ReasoningProviderKind | None:
    """Detect DeepSeek-family kinds from host alone."""
    parsed = _parse_api_base(api_base)
    host = (parsed.hostname or "").lower()
    path = (parsed.path or "").rstrip("/")

    if host == "api.deepseek.com":
        return "deepseek_official"

    if host == "dashscope.aliyuncs.com" and path.startswith("/compatible-mode"):
        return "dashscope_bailian"

    return None


def _matches_prefix(model: str, prefixes: tuple[str, ...]) -> bool:
    """Exact name or hyphen-boundary prefix match."""
    return any(model == prefix or model.startswith(f"{prefix}-") for prefix in prefixes)


def _claude_4_minor_allows_budget_tokens(model: str) -> bool:
    """Allow Claude 4.x minors below 6; treat 8-digit segments as date stamps."""
    match = _CLAUDE_4_FAMILY_RE.match(model)
    if match is None:
        return True
    segment = match.group(1)
    if segment is None:
        return True
    if len(segment) >= 8:
        return True
    return int(segment) < 6


def is_anthropic_thinking_model(model_name: str | None) -> bool:
    """Whether this model accepts a ``thinking`` budget block."""
    model = str(model_name or "").strip().lower()
    if not model:
        return False
    if not _matches_prefix(model, ANTHROPIC_THINKING_MODEL_PREFIXES):
        return False
    return _claude_4_minor_allows_budget_tokens(model)


def is_openai_reasoning_model(model_name: str | None) -> bool:
    """Whether this model accepts ``reasoning_effort``."""
    model = str(model_name or "").strip().lower()
    if not model:
        return False
    if model == "gpt-5" or model.startswith("gpt-5-") or model.startswith("gpt-5."):
        # ``gpt-5-chat-latest`` / ``gpt-5.1-chat`` are ChatGPT-instant ids, not
        # reasoning models; sending them ``reasoning_effort`` is a 400.
        return not _GPT5_CHAT_VARIANT_RE.search(model)
    return _matches_prefix(model, tuple(p for p in OPENAI_REASONING_MODEL_PREFIXES if p != "gpt-5"))


def normalize_reasoning_level(raw: Any) -> ReasoningLevel | None:
    if raw is None:
        return None

    key = str(raw).strip().lower()
    if key == "":
        return None
    if key in {"off", "none", "false", "disable", "disabled"}:
        return "off"
    if key in {"on", "true", "enable", "enabled", "low"}:
        return "low"
    if key in {"medium", "med", "mid"}:
        return "medium"
    if key == "high":
        return "high"
    return None


def resolve_reasoning_target(
    *,
    client_provider: Any,
    api_base: str | None,
    model_name: str | None,
) -> tuple[ReasoningProviderKind, str] | None:
    """Return ``(kind, model)`` for the provider knob, or ``None``."""
    provider = _normalize_provider(client_provider)
    host = _host_of(api_base)
    model = str(model_name or "").strip().lower()

    if provider in OPENAI_SDK_REASONING_PROVIDERS:
        deepseek_kind = resolve_reasoning_provider_kind(api_base)
        if deepseek_kind is not None:
            if model not in SUPPORTED_DEEPSEEK_V4_MODELS:
                return None
            return deepseek_kind, model

    if provider in ANTHROPIC_PROVIDER_NAMES or host in ANTHROPIC_API_HOSTS:
        if not is_anthropic_thinking_model(model):
            return None
        return "anthropic", model

    if host in OPENAI_API_HOSTS and is_openai_reasoning_model(model):
        return "openai_reasoning", model

    return None


__all__ = [
    "ANTHROPIC_BUDGET_TOKENS",
    "ANTHROPIC_MAX_TOKENS_HEADROOM",
    "ANTHROPIC_THINKING_MODEL_PREFIXES",
    "OPENAI_REASONING_MODEL_PREFIXES",
    "OPENAI_SDK_REASONING_PROVIDERS",
    "SUPPORTED_DEEPSEEK_V4_MODELS",
    "ReasoningLevel",
    "ReasoningProviderKind",
    "is_anthropic_thinking_model",
    "is_openai_reasoning_model",
    "normalize_reasoning_level",
    "resolve_reasoning_provider_kind",
    "resolve_reasoning_target",
]
