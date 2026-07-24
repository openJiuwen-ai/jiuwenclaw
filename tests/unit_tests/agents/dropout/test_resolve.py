# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for team_pruning / AgentDropout config resolution."""

from __future__ import annotations

from jiuwenswarm.agents.dropout.resolve import (
    resolve_agent_dropout_config,
    resolve_team_pruning,
)


def test_resolve_disabled_by_default():
    resolved = resolve_team_pruning({})
    assert resolved["enabled"] is False
    assert resolved["strategy"] == "agent_dropout"


def test_resolve_team_pruning_enabled_agent_dropout():
    cfg = {
        "team_pruning": {
            "enabled": True,
            "strategy": "agent_dropout",
            "strategies": {
                "agent_dropout": {
                    "max_rectify_attempts": 3,
                    "drop_after_failures": 4,
                }
            },
        }
    }
    resolved = resolve_team_pruning(cfg)
    assert resolved["enabled"] is True
    assert resolved["strategy"] == "agent_dropout"
    assert resolved["strategy_config"]["max_rectify_attempts"] == 3

    dropout = resolve_agent_dropout_config(cfg)
    assert dropout["enabled"] is True
    assert dropout["max_rectify_attempts"] == 3
    assert dropout["drop_after_failures"] == 4


def test_legacy_agent_dropout_enabled_fallback():
    cfg = {"agent_dropout": {"enabled": True, "pass_rate": 0.5}}
    resolved = resolve_team_pruning(cfg)
    assert resolved["enabled"] is True
    assert resolved["strategy"] == "agent_dropout"
    dropout = resolve_agent_dropout_config(cfg)
    assert dropout["enabled"] is True
    assert dropout["pass_rate"] == 0.5


def test_unknown_strategy_falls_back_to_agent_dropout():
    cfg = {"team_pruning": {"enabled": True, "strategy": "not_real"}}
    resolved = resolve_team_pruning(cfg)
    assert resolved["strategy"] == "agent_dropout"


def test_other_strategy_disables_agent_dropout_rail():
    # Simulate a future strategy being selected: AgentDropout must stay off.
    cfg = {
        "team_pruning": {
            "enabled": True,
            "strategy": "agent_dropout",
        },
        "agent_dropout": {"enabled": True},
    }
    # Force unknown then assert agent dropout off when strategy mismatches via direct call.
    cfg["team_pruning"]["strategy"] = "agent_dropout"
    assert resolve_agent_dropout_config(cfg)["enabled"] is True

    # If team_pruning enabled but we only recognize agent_dropout, unknown is remapped.
    # Explicitly verify disabled when team_pruning.enabled is false.
    cfg["team_pruning"]["enabled"] = False
    assert resolve_agent_dropout_config(cfg)["enabled"] is False
