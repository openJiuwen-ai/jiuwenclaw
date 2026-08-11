# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for skill-evolution trajectory directory resolution and FileTrajectoryStore wiring."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openjiuwen.agent_evolving.trajectory import FileTrajectoryStore

from jiuwenswarm.common.utils import get_agent_evolution_trajectories_dir, get_agent_root_dir
from jiuwenswarm.server.runtime.agent_adapter.interface_deep import JiuWenSwarmDeepAdapter


def test_get_agent_evolution_trajectories_dir_under_agent_root():
    assert get_agent_evolution_trajectories_dir() == get_agent_root_dir() / "evolution_trajectories"


def test_resolve_evolution_trajectory_dir_prefers_env(tmp_path, monkeypatch):
    env_dir = tmp_path / "from-env"
    monkeypatch.setenv("EVOLUTION_TRAJECTORY_DIR", str(env_dir))
    resolved = JiuWenSwarmDeepAdapter._resolve_evolution_trajectory_dir(
        {"react": {"evolution": {"trajectory_dir": str(tmp_path / "from-config")}}}
    )
    assert resolved == env_dir.resolve()


def test_resolve_evolution_trajectory_dir_absolute_config(tmp_path, monkeypatch):
    monkeypatch.delenv("EVOLUTION_TRAJECTORY_DIR", raising=False)
    abs_dir = tmp_path / "abs-traj"
    resolved = JiuWenSwarmDeepAdapter._resolve_evolution_trajectory_dir(
        {"react": {"evolution": {"trajectory_dir": str(abs_dir)}}}
    )
    assert resolved == abs_dir


def test_resolve_evolution_trajectory_dir_relative_config(tmp_path, monkeypatch):
    monkeypatch.delenv("EVOLUTION_TRAJECTORY_DIR", raising=False)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    with patch(
        "jiuwenswarm.server.runtime.agent_adapter.interface_deep.get_multi_tenant_user_workspace_dir",
        return_value=workspace,
    ):
        resolved = JiuWenSwarmDeepAdapter._resolve_evolution_trajectory_dir(
            {"react": {"evolution": {"trajectory_dir": "agent/evolution_trajectories"}}}
        )
    assert resolved == (workspace / "agent" / "evolution_trajectories").resolve()


def test_resolve_evolution_trajectory_dir_default(monkeypatch):
    monkeypatch.delenv("EVOLUTION_TRAJECTORY_DIR", raising=False)
    resolved = JiuWenSwarmDeepAdapter._resolve_evolution_trajectory_dir({"react": {"evolution": {}}})
    assert resolved == get_agent_evolution_trajectories_dir()


def test_build_skill_evolution_rail_passes_file_trajectory_store(tmp_path, monkeypatch):
    monkeypatch.delenv("EVOLUTION_TRAJECTORY_DIR", raising=False)
    traj_dir = tmp_path / "traj"
    captured: dict = {}

    class _FakeSkillEvolutionRail:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    adapter = JiuWenSwarmDeepAdapter()
    adapter._model = object()
    adapter._default_model_name = "gpt-test"
    adapter._skill_manager = SimpleNamespace(list_execution_disabled_skills=lambda: [])
    with (
        patch.object(
            JiuWenSwarmDeepAdapter,
            "_resolve_evolution_trajectory_dir",
            return_value=traj_dir,
        ),
        patch.object(adapter, "_resolve_skill_dirs", return_value=[str(tmp_path / "skills")]),
        patch(
            "jiuwenswarm.server.runtime.agent_adapter.interface_deep.SkillEvolutionRail",
            _FakeSkillEvolutionRail,
        ),
        patch(
            "jiuwenswarm.server.runtime.agent_adapter.interface_deep.EvolutionReviewRuntime",
            return_value=object(),
        ),
    ):
        rail = adapter._build_skill_evolution_rail(
            {"react": {"evolution": {"trajectory_dir": str(traj_dir)}}}
        )

    assert rail is not None
    store = captured.get("trajectory_store")
    assert isinstance(store, FileTrajectoryStore)
    assert store._base_dir == traj_dir


@pytest.mark.asyncio
async def test_ensure_active_evolution_rails_passes_file_trajectory_store(tmp_path, monkeypatch):
    monkeypatch.delenv("EVOLUTION_TRAJECTORY_DIR", raising=False)
    traj_dir = tmp_path / "configured-traj"
    configure = AsyncMock()

    adapter = JiuWenSwarmDeepAdapter()
    adapter._instance = object()
    adapter._model = object()
    adapter._default_model_name = "gpt-test"
    adapter._config_cache = {"react": {"evolution": {"trajectory_dir": str(traj_dir)}}}
    adapter._skill_manager = SimpleNamespace(list_execution_disabled_skills=lambda: [])
    adapter._skill_evolution_rail = SimpleNamespace(_language="cn")

    with (
        patch.object(adapter, "_resolve_runtime_language", return_value="cn"),
        patch.object(adapter, "_resolve_skill_dirs", return_value=[str(tmp_path / "skills")]),
        patch.object(
            JiuWenSwarmDeepAdapter,
            "_resolve_evolution_trajectory_dir",
            return_value=traj_dir,
        ),
        patch(
            "jiuwenswarm.server.runtime.agent_adapter.interface_deep.configure_skill_evolution_runtime",
            configure,
        ),
        patch.object(adapter, "_refresh_active_evolution_rail_refs", MagicMock()),
        patch(
            "jiuwenswarm.server.runtime.agent_adapter.interface_deep._set_skill_evolution_triggers",
            MagicMock(),
        ),
    ):
        await adapter._ensure_active_evolution_rails_registered()

    configure.assert_awaited_once()
    kwargs = configure.await_args.kwargs
    store = kwargs.get("trajectory_store")
    assert isinstance(store, FileTrajectoryStore)
    assert store._base_dir == traj_dir
