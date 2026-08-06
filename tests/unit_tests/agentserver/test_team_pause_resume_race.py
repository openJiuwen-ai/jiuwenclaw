# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Pause/continue multi-session race: paused bookmark + pool scan."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from jiuwenclaw.agentserver.team.team_manager import TeamManager
from openjiuwen.agent_teams.runtime.pool import RuntimeState


@pytest.mark.asyncio
async def test_clear_active_bookmarks_paused_for_restore() -> None:
    mgr = TeamManager.__new__(TeamManager)
    mgr._active_team_names = {"sess-a": "team-a"}
    mgr._pending_team_names = {}
    mgr._paused_team_names = {}

    mgr.clear_active_runtime("sess-a", bookmark_paused=True)

    assert "sess-a" not in mgr._active_team_names
    assert mgr._paused_team_names["sess-a"] == "team-a"
    assert mgr._lookup_session_team_name("sess-a") == "team-a"


@pytest.mark.asyncio
async def test_resolve_resumable_scans_pool_by_session(monkeypatch: pytest.MonkeyPatch) -> None:
    mgr = TeamManager.__new__(TeamManager)
    mgr._active_team_names = {"sess-b": "team-b"}  # another session active
    mgr._pending_team_names = {}
    mgr._paused_team_names = {}

    paused_entry = SimpleNamespace(
        team_name="team-a",
        current_session_id="sess-a",
        state=RuntimeState.PAUSED,
    )
    pool = SimpleNamespace(
        get=AsyncMock(return_value=None),
        teams_for_session=AsyncMock(return_value=[paused_entry]),
    )
    runtime_mgr = SimpleNamespace(pool=pool)

    monkeypatch.setattr(
        "jiuwenclaw.agentserver.team.team_manager._runner_team_runtime_manager",
        lambda _runner: runtime_mgr,
    )
    monkeypatch.setattr(
        "jiuwenclaw.agentserver.team.team_manager.get_session_metadata",
        lambda _sid: {},
    )

    resolved = await mgr._resolve_resumable_runner_entry("sess-a")
    assert resolved is not None
    assert resolved[0] == "team-a"

    ok = await mgr.restore_resumable_runtime("sess-a")
    assert ok is True
    assert mgr._active_team_names["sess-a"] == "team-a"
