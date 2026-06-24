# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tool Calling Guard: strip tools/tool_choice from LLM API requests when disabled."""

from __future__ import annotations

from dataclasses import dataclass

from jiuwenclaw.agentserver.permissions.core import _to_bool
from jiuwenclaw.config import get_config
from jiuwenclaw.local_env_config import read_env, read_env_if_set

# Tri-state env override for OFFICE_CLAW_DISABLE_TOOL_CALLING: truthy / falsy / unset.
# Unset or unrecognized values fall through to the MODEL_NAME channel — do not use
# _to_bool here; it treats '' and unknown strings as False/True (binary), which would
# break the escape hatch and model-name inference paths.
_TRUTHY = frozenset({"true", "1", "yes", "on"})
_FALSY = frozenset({"false", "0", "no", "off"})

_GUARD_ENABLED_ENV = "TOOL_CALLING_GUARD_ENABLED"
_DISABLE_TOOL_CALLING_ENV = "OFFICE_CLAW_DISABLE_TOOL_CALLING"
_SIMPLE_CHAT_MODE_REASON_ENV = "OFFICE_CLAW_SIMPLE_CHAT_MODE_REASON"
_MODEL_NAME_ENV = "MODEL_NAME"


@dataclass(frozen=True)
class ToolCallingGuardDecision:
    strip_tools: bool
    reason: str = ""


def is_tool_calling_guard_enabled() -> bool:
    """Return whether the tool calling guard master switch is on."""
    env_raw = read_env_if_set(_GUARD_ENABLED_ENV)
    if env_raw is not None:
        return _to_bool(env_raw)
    guard_cfg = get_config().get("react", {}).get("tool_calling_guard") or {}
    return _to_bool(guard_cfg.get("enabled", False))


def _limited_models_from_config() -> frozenset[str]:
    guard_cfg = get_config().get("react", {}).get("tool_calling_guard") or {}
    models = guard_cfg.get("limited_models") or []
    return frozenset(str(item).strip().lower() for item in models if str(item).strip())


def resolve_tool_calling_guard() -> ToolCallingGuardDecision:
    """Decide whether to strip tools/tool_choice from the next LLM request."""
    if not is_tool_calling_guard_enabled():
        return ToolCallingGuardDecision(strip_tools=False)

    # Tri-state: truthy → strip; falsy → force / escape hatch; else → MODEL_NAME channel.
    env_raw = read_env(_DISABLE_TOOL_CALLING_ENV, "").strip().lower()
    if env_raw in _TRUTHY:
        reason = read_env(_SIMPLE_CHAT_MODE_REASON_ENV, "").strip()
        return ToolCallingGuardDecision(True, reason or "env_override")
    if env_raw in _FALSY:
        return ToolCallingGuardDecision(strip_tools=False)

    model = read_env(_MODEL_NAME_ENV, "").strip().lower()
    if model in _limited_models_from_config():
        return ToolCallingGuardDecision(True, "model_name_without_function_call")

    return ToolCallingGuardDecision(strip_tools=False)
