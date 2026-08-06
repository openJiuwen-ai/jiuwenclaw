# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for team config loading."""

from pathlib import Path

import pytest

from jiuwenclaw.agentserver.team.config_loader import (
    TeamTemplateNotFoundError,
    _resolve_enable_permissions,
    load_team_spec_dict,
    resolve_team_sqlite_db_path,
)


def test_resolve_enable_permissions_follows_global_only() -> None:
    """Team rails follow permissions.enabled; team-local flag is ignored."""
    assert _resolve_enable_permissions(
        {"permissions": {"enabled": True}},
        {"enable_permissions": False},
    ) is True
    assert _resolve_enable_permissions(
        {"permissions": {"enabled": False}},
        {"enable_permissions": True},
    ) is False
    assert _resolve_enable_permissions({}, {"enable_permissions": True}) is False


def test_resolve_team_sqlite_db_path_defaults_to_agent_teams_home(monkeypatch, tmp_path):
    """Missing connection_string should fall back to openjiuwen agent-teams team.db."""
    config = {
        "modes": {
            "team": {
                "jiuwen_team": {
                    "storage": {
                        "type": "sqlite",
                        "params": {},
                    }
                }
            }
        }
    }

    monkeypatch.setattr(
        "jiuwenclaw.agentserver.team.config_loader.get_config",
        lambda: config,
    )
    monkeypatch.setattr(
        "jiuwenclaw.agentserver.team.config_loader.get_agent_teams_home",
        lambda: tmp_path / ".agent_teams",
    )

    db_path = resolve_team_sqlite_db_path()

    assert db_path == Path(tmp_path / ".agent_teams" / "team.db")


def test_load_team_spec_dict_selects_bound_template_not_first_entry():
    """chat.send team_name must select that modes.team key, not the first preset."""
    config = {
        "modes": {
            "team": {
                "oc_team_preset-product-research": {
                    "team_name": "oc_team_preset-product-research",
                    "lifecycle": "persistent",
                    "leader": {"agent_key": "product", "name": "product", "display_name": "产品"},
                },
                "oc_team_team-user": {
                    "team_name": "oc_team_team-user",
                    "lifecycle": "persistent",
                    "leader": {"agent_key": "office", "name": "office", "display_name": "助手"},
                },
            }
        },
        "models": {"defaults": []},
    }

    assert load_team_spec_dict(config)["team_name"] == "oc_team_preset-product-research"
    assert (
        load_team_spec_dict(config, template_id="oc_team_team-user")["team_name"]
        == "oc_team_team-user"
    )
    with pytest.raises(TeamTemplateNotFoundError):
        load_team_spec_dict(config, template_id="oc_team_missing")


def test_load_team_spec_dict_maps_persona_and_prompt_hint_to_desc_prompt():
    """Relay syncs persona/prompt_hint; TeamMemberSpec only keeps desc/prompt."""
    config = {
        "modes": {
            "team": {
                "oc_team_debate": {
                    "team_name": "oc_team_debate",
                    "lifecycle": "persistent",
                    "leader": {
                        "member_name": "team_leader",
                        "name": "Leader",
                        "persona": "天才项目管理专家",
                    },
                    "predefined_members": [
                        {
                            "member_name": "assistant",
                            "name": "助理",
                            "persona": "逻辑清晰的综合助理",
                            "prompt_hint": "先认领再写",
                        },
                        {
                            "member_name": "user-research",
                            "name": "用研",
                            "persona": "用户研究专家",
                        },
                    ],
                }
            }
        },
        "models": {"defaults": []},
    }

    spec = load_team_spec_dict(config, template_id="oc_team_debate")
    assert spec["leader"]["desc"] == "天才项目管理专家"
    members = {m["member_name"]: m for m in spec["predefined_members"]}
    assert members["assistant"]["desc"] == "逻辑清晰的综合助理"
    assert "先认领再写" in members["assistant"]["prompt"]
    assert "persona" not in members["assistant"]
    assert "prompt_hint" not in members["assistant"]
    assert members["user-research"]["desc"] == "用户研究专家"
