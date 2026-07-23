"""Availability policy for the Claude provider.

The Claude provider is **subscription-only** but otherwise a normal, visible,
default-enabled provider (comparable to Codex). It carries
no credential in Jiuwen config (``requires_api_key=False``,
``requires_api_base=False``, ``subscription_auth=False`` in
``provider_capabilities``): the child authenticates only through the operator's
own ``claude`` login, resolved natively from the environment. Every turn
positively verifies a Claude.ai subscription (see ``claude_auth_status``) and
fails closed on anything else. It routes through Jiuwen's normal model path and
does **not** use the Codex subscription-admission machinery.

Enablement is an **administrator kill switch that defaults to ENABLED**. Ordinary
operation needs no flag. This module also carries the v1 tool allowlist mirroring
Codex.
"""

from __future__ import annotations

import os
from typing import Any

from .claude_constants import CLAUDE_PROVIDER_NAME
from .errors import ClaudeProviderError

CLAUDE_SUBSCRIPTION_ENABLED_ENV = "JIUWENSWARM_CLAUDE_SUBSCRIPTION_ENABLED"
_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_FALSE_VALUES = frozenset({"0", "false", "no", "off"})

# Mirror Codex v1: expose only source-reviewed, non-LLM Jiuwen tools.
SAFE_JIUWEN_TOOL_NAMES = frozenset({"cron_list_jobs"})


def claude_subscription_enabled() -> bool:
    """Administrator kill switch that defaults to ENABLED.

    Ordinary operation requires no flag: absent, empty, or an explicit truthy
    value all enable the provider. Only an explicit disable value turns it off;
    an unrecognized value also disables (a kill switch errs toward off).
    """

    raw = os.getenv(CLAUDE_SUBSCRIPTION_ENABLED_ENV)
    if raw is None or not raw.strip():
        return True
    normalized = raw.strip().lower()
    if normalized in _TRUE_VALUES:
        return True
    if normalized in _FALSE_VALUES:
        return False
    return False


def provider_name(value: object) -> str:
    return str(getattr(value, "value", value) or "").strip()


def is_claude_provider(value: object) -> bool:
    return provider_name(value) == CLAUDE_PROVIDER_NAME


def require_claude_enabled() -> None:
    if not claude_subscription_enabled():
        raise ClaudeProviderError(
            "provider_disabled", "The Claude provider is disabled for this Jiuwen instance."
        )


def filter_claude_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    filtered: list[dict[str, Any]] = []
    for tool in tools:
        function = tool.get("function") if isinstance(tool, dict) else None
        name = function.get("name") if isinstance(function, dict) else None
        if name in SAFE_JIUWEN_TOOL_NAMES:
            filtered.append(tool)
    return filtered
