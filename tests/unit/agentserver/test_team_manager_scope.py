# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jiuwenclaw.agentserver.runtime_scope import RuntimeScopeKey
from jiuwenclaw.agentserver.team.team_manager import TeamManager, reset_team_manager


@pytest.fixture(autouse=True)
def _reset_team_mgr():
    reset_team_manager()
    yield
    reset_team_manager()


@pytest.mark.asyncio
async def test_same_tenant_new_session_destroys_previous_team() -> None:
    mgr = TeamManager()
    scope_a = RuntimeScopeKey.from_ids("svc", "aid", "sess-a")
    scope_b = RuntimeScopeKey.from_ids("svc", "aid", "sess-b")

    fake_team = MagicMock()
    fake_team.destroy_team = AsyncMock(return_value=True)

    with (
        patch.object(TeamManager, "_load_team_spec", return_value=MagicMock(team_name="t")),
        patch.object(TeamManager, "_cleanup_team_runtime_state", new=AsyncMock(return_value=([], []))),
        patch.object(TeamManager, "build_agent_customizer", return_value=lambda *a, **k: None),
        patch.object(TeamManager, "_copy_global_skills_to_team_shared_dir"),
        patch(
            "jiuwenclaw.agentserver.team.team_manager.set_session_id",
            return_value="tok",
        ),
        patch("jiuwenclaw.agentserver.team.team_manager.reset_session_id"),
    ):
        # Inject fake create via patching spec.build
        with patch(
            "jiuwenclaw.agentserver.team.team_manager.TeamAgentSpec.model_validate",
        ):
            # Bypass create_team internals: put A directly then get_or_create B
            mgr._team_agents[scope_a.session_key()] = fake_team

            async def _fake_create(scope, *args, **kwargs):
                team = MagicMock(name=f"team-{scope.session_id}")
                team.destroy_team = AsyncMock(return_value=True)
                mgr._team_agents[scope.session_key()] = team
                return team

            with patch.object(mgr, "create_team", side_effect=_fake_create):
                await mgr.get_or_create_team(scope_b, deep_agent=MagicMock())

            assert scope_a.session_key() not in mgr._team_agents
            assert scope_b.session_key() in mgr._team_agents
            fake_team.destroy_team.assert_awaited()


@pytest.mark.asyncio
async def test_different_tenants_teams_coexist() -> None:
    mgr = TeamManager()
    scope_a = RuntimeScopeKey.from_ids("svc1", "aid1", "sess")
    scope_b = RuntimeScopeKey.from_ids("svc2", "aid2", "sess")  # same session string

    team_a = MagicMock(name="a")
    team_a.destroy_team = AsyncMock(return_value=True)
    mgr._team_agents[scope_a.session_key()] = team_a

    async def _fake_create(scope, *args, **kwargs):
        team = MagicMock(name=f"team-{scope.tenant()}")
        team.destroy_team = AsyncMock(return_value=True)
        mgr._team_agents[scope.session_key()] = team
        return team

    with patch.object(mgr, "create_team", side_effect=_fake_create):
        await mgr.get_or_create_team(scope_b, deep_agent=MagicMock())

    assert scope_a.session_key() in mgr._team_agents
    assert scope_b.session_key() in mgr._team_agents
    team_a.destroy_team.assert_not_awaited()

def test_has_stream_task_requires_session() -> None:
    mgr = TeamManager()
    with pytest.raises(ValueError):
        mgr.has_stream_task(RuntimeScopeKey.from_ids("s", "a"))


def test_build_agent_customizer_requires_tenant_scope() -> None:
    from openjiuwen.agent_teams.schema.blueprint import TeamAgentSpec

    spec = TeamAgentSpec.model_validate(
        {"team_name": "t", "agents": {"leader": {}}}
    )
    deep_agent = type("_DeepAgent", (), {})()  # no tenant ids
    with pytest.raises(ValueError, match="requires runtime_scope"):
        TeamManager.build_agent_customizer(
            spec=spec,
            deep_agent=deep_agent,
            session_id="sess",
            runtime_scope=None,
        )
