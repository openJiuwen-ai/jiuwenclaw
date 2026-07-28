# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Resolve team pruning / AgentDropout config with multi-strategy support."""

from __future__ import annotations

from typing import Any

# Registered pruning strategies. Add new names here as strategies ship.
TEAM_PRUNING_STRATEGIES: tuple[str, ...] = ("agent_dropout",)
DEFAULT_TEAM_PRUNING_STRATEGY = "agent_dropout"


def _section(config: dict[str, Any] | None, key: str) -> dict[str, Any]:
    raw = (config or {}).get(key)
    return raw if isinstance(raw, dict) else {}


def resolve_team_pruning(config: dict[str, Any] | None) -> dict[str, Any]:
    """Resolve effective team pruning settings.

    Prefers ``team_pruning`` (multi-strategy). Falls back to legacy
    ``agent_dropout.enabled`` when ``team_pruning`` is absent/disabled.
    """
    pruning = _section(config, "team_pruning")
    legacy = _section(config, "agent_dropout")

    strategy = str(pruning.get("strategy") or DEFAULT_TEAM_PRUNING_STRATEGY).strip()
    if strategy not in TEAM_PRUNING_STRATEGIES:
        strategy = DEFAULT_TEAM_PRUNING_STRATEGY

    enabled = bool(pruning.get("enabled", False))
    if not enabled and not pruning and bool(legacy.get("enabled", False)):
        # Legacy single-flag path.
        enabled = True
        strategy = "agent_dropout"

    strategies = pruning.get("strategies")
    strategies = strategies if isinstance(strategies, dict) else {}
    strategy_cfg = strategies.get(strategy)
    if not isinstance(strategy_cfg, dict):
        strategy_cfg = dict(legacy) if strategy == "agent_dropout" else {}

    return {
        "enabled": enabled,
        "strategy": strategy,
        "strategy_config": dict(strategy_cfg),
    }


def resolve_agent_dropout_config(config: dict[str, Any] | None) -> dict[str, Any]:
    """Return AgentDropout knobs with ``enabled`` reflecting team_pruning selection."""
    resolved = resolve_team_pruning(config)
    strategy_cfg = dict(resolved.get("strategy_config") or {})
    legacy = _section(config, "agent_dropout")
    # Prefer nested strategy config; fill gaps from top-level agent_dropout.
    merged = {**legacy, **strategy_cfg}
    enabled = bool(resolved.get("enabled")) and resolved.get("strategy") == "agent_dropout"
    merged["enabled"] = enabled
    return merged
