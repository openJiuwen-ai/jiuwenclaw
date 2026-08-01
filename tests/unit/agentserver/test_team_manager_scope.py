# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jiuwenclaw.agentserver.team.team_manager import TeamManager, reset_team_manager


@pytest.fixture(autouse=True)
def _reset_team_mgr():
    reset_team_manager()
    yield
    reset_team_manager()


@pytest.mark.asyncio
async def test_new_session_destroys_previous_team() -> None:
    mgr = TeamManager()
    session_a = "sess-a"
    session_b = "sess-b"

    fake_team = MagicMock()
    fake_team.destroy_team = AsyncMock(return_value=True)

    with (
        patch.object(TeamManager, "_load_team_spec", return_value=MagicMock(team_name="t")),
        patch(
            "jiuwenclaw.agentserver.team.team_manager.set_session_id",
            return_value="tok",
        ),
        patch("jiuwenclaw.agentserver.team.team_manager.reset_session_id"),
    ):
        mgr._team_agents[session_a] = fake_team

        async def _fake_create(session_id, *args, **kwargs):
            team = MagicMock(name=f"team-{session_id}")
            team.destroy_team = AsyncMock(return_value=True)
            mgr._team_agents[session_id] = team
            return team

        with patch.object(mgr, "create_team", side_effect=_fake_create):
            await mgr.get_or_create_team(session_b, deep_agent=MagicMock())

        assert session_a not in mgr._team_agents
        assert session_b in mgr._team_agents
        fake_team.destroy_team.assert_awaited()


@pytest.mark.asyncio
async def test_cached_session_does_not_destroy_other_sessions() -> None:
    mgr = TeamManager()
    session_a = "sess-a"
    session_b = "sess-b"

    team_a = MagicMock(name="a")
    team_b = MagicMock(name="b")
    team_b.destroy_team = AsyncMock(return_value=True)
    mgr._team_agents[session_a] = team_a
    mgr._team_agents[session_b] = team_b

    result = await mgr.get_or_create_team(session_b, deep_agent=MagicMock())

    assert result is team_b
    assert session_a in mgr._team_agents
    assert session_b in mgr._team_agents


def test_has_stream_task() -> None:
    mgr = TeamManager()
    assert mgr.has_stream_task("sess") is False
    mgr._stream_tasks["sess"] = MagicMock()
    assert mgr.has_stream_task("sess") is True
