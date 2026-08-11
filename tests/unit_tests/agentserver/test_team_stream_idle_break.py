# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Task 5: team_stream per-chunk idle-break + team.completed gate.

RC1: the openjiuwen generator never finalizes when a teammate is stuck BUSY
(is_team_completed() returns None forever). The sidecar consumer blocked on
``async for chunk in team_stream`` until relay's 300s watchdog cancel, which
routed through pause-skip and suppressed chat.file. Fix: per-chunk idle
timeout (< 300s) breaks the loop and walks completion teardown (soft-fallback
chat.file), NOT pause. team.completed is gated on _team_round_settled to
avoid archiving a half-done team on idle-break.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest


class _FakeStream:
    """Async iterator mock: yields given chunks, then blocks or ends."""

    def __init__(self, chunks=None, block_after=False):
        self._chunks = list(chunks or [])
        self._block_after = block_after

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._chunks:
            return self._chunks.pop(0)
        if self._block_after:
            await asyncio.sleep(100)  # blocks -> wait_for times out -> idle-break
        raise StopAsyncIteration

    async def aclose(self):
        pass


def _chunk():
    c = MagicMock()
    c.role = "leader"
    return c


def _mock_tm(is_pause=False):
    tm = MagicMock()
    tm.reset_seen_team_events = MagicMock()
    tm.reset_workflow_completed = MagicMock()
    tm.has_seen_team_events = MagicMock(return_value=True)
    tm.is_workflow_completed = MagicMock(return_value=True)
    tm.is_pause_in_progress = MagicMock(return_value=is_pause)
    tm.clear_pending_runtime = MagicMock()
    return tm


def _patch_common(monkeypatch, tm, stream, settled):
    import jiuwenclaw.agentserver.deep_agent.team_helpers as th

    monkeypatch.setattr(th, "get_team_manager", lambda cid: tm)
    monkeypatch.setattr(th.Runner, "run_agent_team_streaming", lambda **kw: stream)
    captured: list = []
    monkeypatch.setattr(th, "_broadcast_event", lambda cid, sid, p: captured.append(p))
    monkeypatch.setattr(th, "_team_round_settled", AsyncMock(return_value=settled))
    monkeypatch.setattr(th, "_emit_team_chat_file_events", lambda *a, **k: None)
    monkeypatch.setattr(th, "_broadcast_team_state_snapshot", AsyncMock())
    monkeypatch.setattr(th, "_team_workspace_root", lambda spec: "/tmp/ws")
    monkeypatch.setattr(th, "TeamStreamLogger", MagicMock(return_value=None))
    monkeypatch.setattr(th, "parse_stream_chunk", lambda c: None)  # skip per-chunk body
    return captured


@pytest.mark.asyncio
async def test_idle_break_fires_within_threshold_on_no_chunk(monkeypatch):
    monkeypatch.setenv("JIUWEN_TEAM_STREAM_IDLE_BREAK_S", "0.2")
    import jiuwenclaw.agentserver.deep_agent.team_helpers as th

    monkeypatch.setattr(th, "_TEAM_STREAM_IDLE_BREAK_S", 0.2)
    stream = _FakeStream(chunks=[_chunk()], block_after=True)
    captured = _patch_common(monkeypatch, _mock_tm(), stream, settled=False)
    await th._consume_stream_with_query_impl(
        "officeclaw", "officeclaw_s1", MagicMock(), "q",
        round_id=1, envs={}, hide_dm=False,
    )
    types = [p.get("event_type") for p in captured]
    assert "team.stalled" in types, types  # idle-break fired


@pytest.mark.asyncio
async def test_idle_break_does_not_fire_on_steady_chunks(monkeypatch):
    import jiuwenclaw.agentserver.deep_agent.team_helpers as th

    monkeypatch.setattr(th, "_TEAM_STREAM_IDLE_BREAK_S", 0.3)
    stream = _FakeStream(chunks=[_chunk() for _ in range(5)], block_after=False)
    captured = _patch_common(monkeypatch, _mock_tm(), stream, settled=True)
    await th._consume_stream_with_query_impl(
        "officeclaw", "officeclaw_s1", MagicMock(), "q",
        round_id=1, envs={}, hide_dm=False,
    )
    types = [p.get("event_type") for p in captured]
    assert "team.stalled" not in types, types
    assert "team.completed" in types, types  # settled -> completed emitted


@pytest.mark.asyncio
async def test_team_completed_skipped_when_not_settled(monkeypatch):
    import jiuwenclaw.agentserver.deep_agent.team_helpers as th

    monkeypatch.setattr(th, "_TEAM_STREAM_IDLE_BREAK_S", 10)  # disable idle-break
    stream = _FakeStream(chunks=[_chunk()], block_after=False)  # ends naturally
    captured = _patch_common(monkeypatch, _mock_tm(), stream, settled=False)
    await th._consume_stream_with_query_impl(
        "officeclaw", "officeclaw_s1", MagicMock(), "q",
        round_id=1, envs={}, hide_dm=False,
    )
    types = [p.get("event_type") for p in captured]
    assert "team.completed" not in types, "unsettled finalization must not emit team.completed"


@pytest.mark.asyncio
async def test_pause_skip_branch_unchanged_on_cancel(monkeypatch):
    import jiuwenclaw.agentserver.deep_agent.team_helpers as th

    monkeypatch.setattr(th, "_TEAM_STREAM_IDLE_BREAK_S", 10)
    stream = _FakeStream(chunks=[_chunk()], block_after=False)
    captured = _patch_common(monkeypatch, _mock_tm(is_pause=True), stream, settled=False)
    await th._consume_stream_with_query_impl(
        "officeclaw", "officeclaw_s1", MagicMock(), "q",
        round_id=1, envs={}, hide_dm=False,
    )
    types = [p.get("event_type") for p in captured]
    assert "team.completed" not in types
    assert "team.stalled" not in types  # pause path: no stalled either
