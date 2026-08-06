# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Relay team sync payload delimitation and mode-normalization unit tests."""

from __future__ import annotations

from jiuwenclaw.agentserver.agent_ws_server import (
    _normalize_relay_team_payload,
    resolve_agent_request_mode,
)


def test_normalize_drops_non_whitelisted_team_scalars() -> None:
    """Keep only whitelisted team-level scalars; nested structs pass through."""
    payload = {
        "agents": {"expert_tpl": {"model": {"provider": "OpenAI", "api_base": "x",
                                            "api_key": "y", "model": "glm-5"}, "skills": ["coding"]}},
        "team": [{
            "team_name": "debate_A",
            "lifecycle": "persistent",
            "team_mode": "hybrid",
            "enable_swarmflow": True,
            "max_debate_rounds": 3,
            "enable_permissions": False,
            "roster": ["x"],
            "avatar": "a",
            "nickname": "n",
            "leader": {"member_name": "moderator", "persona": "p",
                       "agent_key": "expert_tpl", "avatar": "z"},
            "predefined_members": [{"member_name": "eco", "role_type": "teammate",
                                    "persona": "pe", "prompt_hint": "ph",
                                    "agent_key": "expert_tpl", "color": "red"}],
        }],
    }
    out = _normalize_relay_team_payload(payload)
    team = out["team"][0]
    assert "roster" not in team
    assert "avatar" not in team
    assert "nickname" not in team
    assert "enable_permissions" not in team
    # Nested fields are not filtered here (_build_modes_team_mapping owns that).
    assert team["leader"]["avatar"] == "z"
    assert team["predefined_members"][0]["color"] == "red"


def test_normalize_forces_enable_swarmflow_false() -> None:
    """Relay sync always forces enable_swarmflow=False, even when input is True."""
    payload = {"team": [{"team_name": "t1", "enable_swarmflow": True,
                         "leader": {"member_name": "l", "agent_key": "expert_tpl"}}]}
    out = _normalize_relay_team_payload(payload)
    assert out["team"][0]["enable_swarmflow"] is False


def test_normalize_defaults_team_mode_predefined_when_absent() -> None:
    """Missing team_mode defaults to predefined; explicit values are kept."""
    out_absent = _normalize_relay_team_payload(
        {"team": [{"team_name": "t2", "leader": {"member_name": "l", "agent_key": "expert_tpl"}}]}
    )
    assert out_absent["team"][0]["team_mode"] == "predefined"

    out_explicit = _normalize_relay_team_payload(
        {"team": [{"team_name": "t3", "team_mode": "hybrid",
                   "leader": {"member_name": "l", "agent_key": "expert_tpl"}}]}
    )
    assert out_explicit["team"][0]["team_mode"] == "hybrid"


def test_normalize_drops_team_enable_permissions() -> None:
    """Team-local enable_permissions is stripped; runtime uses global permissions.enabled."""
    payload = {
        "team": [{
            "team_name": "t",
            "enable_permissions": True,
            "leader": {"member_name": "l", "agent_key": "expert_tpl"},
        }]
    }
    out = _normalize_relay_team_payload(payload)
    assert "enable_permissions" not in out["team"][0]


def test_normalize_passes_through_max_debate_rounds() -> None:
    """max_debate_rounds is passed through when present; not invented when absent."""
    with_value = _normalize_relay_team_payload(
        {"team": [{"team_name": "t", "max_debate_rounds": 5,
                   "leader": {"member_name": "l", "agent_key": "expert_tpl"}}]}
    )
    assert with_value["team"][0]["max_debate_rounds"] == 5
    without = _normalize_relay_team_payload(
        {"team": [{"team_name": "t", "leader": {"member_name": "l", "agent_key": "expert_tpl"}}]}
    )
    assert "max_debate_rounds" not in without["team"][0]


def test_normalize_does_not_mutate_input() -> None:
    payload = {"team": [{"team_name": "t", "enable_swarmflow": True,
                         "leader": {"member_name": "l", "agent_key": "expert_tpl"}}]}
    _normalize_relay_team_payload(payload)
    assert payload["team"][0]["enable_swarmflow"] is True


def test_normalize_passthrough_when_team_not_list() -> None:
    """Non-list team payloads are returned unchanged (agents-only sync)."""
    payload = {"agents": {"k": {"model": {"provider": "OpenAI", "api_base": "x",
                                          "api_key": "y", "model": "m"}}}}
    assert _normalize_relay_team_payload(payload) is payload


def test_resolve_agent_team_canonicalizes_to_bare_team() -> None:
    """agent.team canonicalizes to bare team for manager routing."""
    manager_mode, sub_mode, canonical = resolve_agent_request_mode("agent.team")
    assert (manager_mode, sub_mode, canonical) == ("team", None, "team")


def test_resolve_team_plan_keeps_sub_mode() -> None:
    """team.plan / code.team keep a sub-mode (drive SDK code rails)."""
    manager_mode, sub_mode, canonical = resolve_agent_request_mode("team.plan")
    assert manager_mode == "code"
    assert canonical == "team.plan"


def test_build_single_agent_params_materializes_standalone_experts() -> None:
    """sync agents[] materializes CreateAgentParams (name/prompt/skills/model)."""
    from jiuwenclaw.agentserver.agent_config_service import build_single_agent_params

    agents = [
        {
            "agent_id": "office",
            "prompt": "你是通用助手，从全局梳理角度分析。",
            "description": "小鸥",
            "runtime": {"model_name": "glm-5.1", "skills": ["document-writing"]},
        },
        {
            "agent_id": "assistant",
            "prompt": "你是执行助手。",
            "description": "小助",
            "runtime": {"model_name": "glm-5"},
        },
    ]
    params = build_single_agent_params(agents)
    by_name = {p.name: p for p in params}
    assert set(by_name) == {"office", "assistant"}
    office = by_name["office"]
    assert office.model == "glm-5.1"
    assert office.skills == ["document-writing"]
    assert office.prompt == "你是通用助手，从全局梳理角度分析。"
    assert office.description == "小鸥"
    assert office.tools == ["*"]
    assert office.location == "user"


def test_build_single_agent_params_rejects_invalid_name() -> None:
    """Reject agent_id outside [a-zA-Z0-9_-]{3,50}."""
    import pytest

    from jiuwenclaw.agentserver.agent_config_service import build_single_agent_params

    with pytest.raises(ValueError):
        build_single_agent_params([{"agent_id": "bad name!", "runtime": {}}])
    with pytest.raises(ValueError):
        build_single_agent_params([{"agent_id": "", "runtime": {}}])
