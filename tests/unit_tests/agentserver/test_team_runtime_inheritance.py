# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for team runtime inheritance helpers."""

from jiuwenclaw.agentserver.deep_agent.rails.runtime_prompt_rail import RuntimePromptRail
from jiuwenclaw.agentserver.team.team_runtime_inheritance import (
    resolve_member_catalog_agent_id,
)


def test_runtime_prompt_rail_office_tenant_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "jiuwenclaw.agentserver.deep_agent.rails.runtime_prompt_rail.get_multi_tenant_user_workspace_dir",
        lambda sid, aid: tmp_path / f"service_{sid}" / f"agent_{aid}",
    )
    rail = RuntimePromptRail(service_id="default", agent_id="office")
    dirs = rail._get_workspace_dirs()
    config = dirs["config"].replace("\\", "/")
    workspace = dirs["workspace"].replace("\\", "/")
    assert config.endswith("service_default/agent_office/config")
    assert "service_default/agent_office/agent/jiuwenclaw_workspace" in workspace
    assert dirs["memory"].replace("\\", "/").endswith(
        "service_default/agent_office/agent/jiuwenclaw_workspace/memory"
    )


def test_runtime_prompt_rail_none_tenant_normalizes_to_default(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "jiuwenclaw.agentserver.deep_agent.rails.runtime_prompt_rail.get_multi_tenant_user_workspace_dir",
        lambda sid, aid: tmp_path / f"service_{sid}" / f"agent_{aid}",
    )
    rail = RuntimePromptRail()
    dirs = rail._get_workspace_dirs()
    assert dirs["config"].replace("\\", "/").endswith(
        "service_default/agent_default/config"
    )


_TEAM_CONFIG = {
    "modes": {
        "team": {
            "oc_team_preset-research-insight": {
                "team_name": "oc_team_preset-research-insight",
                "leader": {
                    "member_name": "chief-researcher",
                    "agent_id": "expert-chief-researcher",
                },
                "predefined_members": [
                    {
                        "member_name": "market-intelligence-researcher",
                        "agent_id": "expert-market-intelligence",
                    },
                ],
            }
        }
    }
}


def test_resolve_catalog_agent_id_with_template_team_id():
    aid = resolve_member_catalog_agent_id(
        _TEAM_CONFIG,
        member_name="chief-researcher",
        role="leader",
        team_id="oc_team_preset-research-insight",
    )
    assert aid == "expert-chief-researcher"


def test_resolve_catalog_agent_id_with_session_scoped_team_id():
    scoped = (
        "oc_team_preset-research-insight_officeclaw_134023b3821c1f54f6159713"
    )
    aid = resolve_member_catalog_agent_id(
        _TEAM_CONFIG,
        member_name="chief-researcher",
        role="leader",
        team_id=scoped,
    )
    assert aid == "expert-chief-researcher"


def test_resolve_catalog_agent_id_predefined_member_session_scoped():
    scoped = (
        "oc_team_preset-research-insight_officeclaw_134023b3821c1f54f6159713"
    )
    aid = resolve_member_catalog_agent_id(
        _TEAM_CONFIG,
        member_name="market-intelligence-researcher",
        role="teammate",
        team_id=scoped,
    )
    assert aid == "expert-market-intelligence"


def test_resolve_catalog_agent_id_role_leader_without_member_name():
    aid = resolve_member_catalog_agent_id(
        _TEAM_CONFIG,
        member_name="leader",
        role="leader",
        team_id="oc_team_preset-research-insight_sess1",
    )
    assert aid == "expert-chief-researcher"


def test_resolve_catalog_agent_id_unknown_team_returns_none():
    aid = resolve_member_catalog_agent_id(
        _TEAM_CONFIG,
        member_name="chief-researcher",
        role="leader",
        team_id="oc_team_other_sess1",
    )
    assert aid is None


def test_enabled_skills_from_member_or_tip_prefers_yaml():
    from jiuwenclaw.agentserver.team.team_runtime_inheritance import (
        enabled_skills_from_member_or_tip,
    )

    text = enabled_skills_from_member_or_tip(
        enabled_skills=["a", "b"],
        catalog_agent_id=None,
    )
    assert text == "a,b"
