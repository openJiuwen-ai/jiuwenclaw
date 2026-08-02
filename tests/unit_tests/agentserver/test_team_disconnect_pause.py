# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests: gateway disconnect parks team runtimes (resumable), not stop."""

from __future__ import annotations

import pytest

from jiuwenclaw.agentserver.team.team_manager import (
    TeamManager,
    pause_all_team_session_runtimes_across_managers,
    reset_team_manager,
)


@pytest.fixture(autouse=True)
def _reset_team_mgr():
    reset_team_manager()
    yield
    reset_team_manager()


@pytest.mark.asyncio
async def test_pause_all_session_runtimes_covers_active_and_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mgr = TeamManager()
    paused: list[tuple[str, str]] = []

    async def _fake_pause(session_id: str, reason: str = "") -> bool:
        paused.append((session_id, reason))
        return True

    monkeypatch.setattr(mgr, "pause_session_runtime", _fake_pause)
    mgr._active_team_names["sess-active"] = "team-a"
    mgr._pending_team_names["sess-pending"] = "team-b"
    mgr._stream_tasks["sess-stream"] = object()  # type: ignore[assignment]

    await mgr.pause_all_session_runtimes(reason="[gateway ws closed] ")

    assert sorted(sid for sid, _ in paused) == [
        "sess-active",
        "sess-pending",
        "sess-stream",
    ]
    assert all(reason == "[gateway ws closed] " for _, reason in paused)


@pytest.mark.asyncio
async def test_pause_all_across_managers_uses_singleton(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mgr = TeamManager()
    paused: list[str] = []

    async def _fake_pause(session_id: str, reason: str = "") -> bool:
        paused.append(session_id)
        return True

    monkeypatch.setattr(mgr, "pause_session_runtime", _fake_pause)
    mgr._runner_team_agents["sess-runner"] = object()  # type: ignore[assignment]

    monkeypatch.setattr(
        "jiuwenclaw.agentserver.team.team_manager.get_team_manager",
        lambda channel_id=None: mgr,
    )

    await pause_all_team_session_runtimes_across_managers(reason="disconnect: ")

    assert paused == ["sess-runner"]


@pytest.mark.asyncio
async def test_pause_all_noop_when_no_sessions() -> None:
    mgr = TeamManager()
    await mgr.pause_all_session_runtimes(reason="disconnect: ")
