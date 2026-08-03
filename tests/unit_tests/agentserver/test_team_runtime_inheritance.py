# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for team runtime inheritance helpers."""

from jiuwenclaw.agentserver.deep_agent.rails.runtime_prompt_rail import RuntimePromptRail


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
