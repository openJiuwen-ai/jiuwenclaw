# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for evolution trajectory dir tenant resolution (方案 B)."""

from __future__ import annotations

from types import SimpleNamespace

from jiuwenclaw.agentserver.deep_agent.interface_deep import JiuWenClawDeepAdapter
from jiuwenclaw.utils import get_agent_evolution_trajectories_dir, resolve_tenant_agent_root_dir


def test_get_agent_evolution_trajectories_dir_explicit(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "jiuwenclaw.utils.get_multi_tenant_user_workspace_dir",
        lambda sid, aid: tmp_path / f"service_{sid}" / f"agent_{aid}",
    )
    path = get_agent_evolution_trajectories_dir("default", "office")
    assert path == (
        resolve_tenant_agent_root_dir("default", "office") / "evolution_trajectories"
    )
    assert "agent_office" in str(path)


def test_resolve_evolution_trajectory_dir_uses_tenant_disk_ids(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "jiuwenclaw.utils.get_multi_tenant_user_workspace_dir",
        lambda sid, aid: tmp_path / f"service_{sid}" / f"agent_{aid}",
    )
    adapter = SimpleNamespace(
        _env_service_id="default",
        _env_agent_id="office",
        _service_id="ignored",
        _agent_id="ignored",
        _tenant_disk_ids=lambda: ("default", "office"),
    )
    path = JiuWenClawDeepAdapter._resolve_evolution_trajectory_dir(adapter)
    assert "agent_office" in str(path)
    assert path.name == "evolution_trajectories"
