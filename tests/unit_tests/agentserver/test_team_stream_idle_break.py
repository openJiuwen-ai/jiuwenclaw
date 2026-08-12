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


@pytest.mark.asyncio
async def test_idle_break_deferred_while_pending_user_decision(monkeypatch):
    """08-12 场景：审批/提问等待期间零 chunk 是等用户而非卡死，idle-break 不得拆流。

    pending 查询第一次 True（等用户）→ 继续等；第二次 False（用户已答/记录清）→
    无 busy 成员 → 正常拆流。断言 stalled 只在第二个窗口后才发，且带 rid。
    """
    import time

    import jiuwenclaw.agentserver.deep_agent.team_helpers as th

    monkeypatch.setattr(th, "_TEAM_STREAM_IDLE_BREAK_S", 0.15)
    pending = AsyncMock(side_effect=[True, False])
    active = AsyncMock(return_value=False)
    monkeypatch.setattr(th, "_team_has_pending_user_decision", pending)
    monkeypatch.setattr(th, "_team_stream_has_active_member", active)
    stream = _FakeStream(chunks=[_chunk()], block_after=True)
    captured = _patch_common(monkeypatch, _mock_tm(), stream, settled=False)
    started = time.monotonic()
    await th._consume_stream_with_query_impl(
        "officeclaw", "officeclaw_s1", MagicMock(), "q",
        round_id=1, envs={}, hide_dm=False,
    )
    elapsed = time.monotonic() - started
    types = [p.get("event_type") for p in captured]
    assert "team.stalled" in types, types
    assert pending.await_count == 2, "首个窗口应因 pending 推迟，第二窗口才拆流"
    stalled = next(p for p in captured if p.get("event_type") == "team.stalled")
    assert stalled.get("rid") == 1
    pings = [
        p for p in captured
        if p.get("event_type") == "chat.processing_status" and p.get("is_processing")
    ]
    assert len(pings) >= 2, "推迟窗口应向 relay 广播业务帧保活（防 relay 看门狗抢跑）"
    assert elapsed >= 0.25, f"应在第二个空闲窗口才拆流, got {elapsed:.3f}s"


@pytest.mark.asyncio
async def test_idle_break_deferred_while_member_active(monkeypatch):
    """08-11 场景：长工具执行期间成员快照 busy → 静默是合法工作，idle-break 不得拆流。"""
    import time

    import jiuwenclaw.agentserver.deep_agent.team_helpers as th

    monkeypatch.setattr(th, "_TEAM_STREAM_IDLE_BREAK_S", 0.15)
    pending = AsyncMock(return_value=False)
    active = AsyncMock(side_effect=[True, False])
    monkeypatch.setattr(th, "_team_has_pending_user_decision", pending)
    monkeypatch.setattr(th, "_team_stream_has_active_member", active)
    stream = _FakeStream(chunks=[_chunk()], block_after=True)
    captured = _patch_common(monkeypatch, _mock_tm(), stream, settled=False)
    started = time.monotonic()
    await th._consume_stream_with_query_impl(
        "officeclaw", "officeclaw_s1", MagicMock(), "q",
        round_id=1, envs={}, hide_dm=False,
    )
    elapsed = time.monotonic() - started
    types = [p.get("event_type") for p in captured]
    assert "team.stalled" in types, types
    assert active.await_count == 2, "首个窗口应因 busy 成员推迟，第二窗口才拆流"
    assert elapsed >= 0.25, f"应在第二个空闲窗口才拆流, got {elapsed:.3f}s"


@pytest.mark.asyncio
async def test_idle_break_busy_defer_capped(monkeypatch):
    """busy 推迟必须封顶：成员状态卡 busy（RC1 stuck-BUSY）与合法长工具快照不可区分，
    无上限会让 idle-break 对 RC1 场景失效。封顶后即使仍 busy 也按 stalled 拆流。
    """
    import time

    import jiuwenclaw.agentserver.deep_agent.team_helpers as th

    monkeypatch.setattr(th, "_TEAM_STREAM_IDLE_BREAK_S", 0.15)
    monkeypatch.setattr(th, "_TEAM_STREAM_IDLE_BREAK_BUSY_DEFER_CAP_S", 0.2)
    pending = AsyncMock(return_value=False)
    active = AsyncMock(return_value=True)  # 成员一直 busy（卡死）
    monkeypatch.setattr(th, "_team_has_pending_user_decision", pending)
    monkeypatch.setattr(th, "_team_stream_has_active_member", active)
    stream = _FakeStream(chunks=[_chunk()], block_after=True)
    captured = _patch_common(monkeypatch, _mock_tm(), stream, settled=False)
    started = time.monotonic()
    await th._consume_stream_with_query_impl(
        "officeclaw", "officeclaw_s1", MagicMock(), "q",
        round_id=1, envs={}, hide_dm=False,
    )
    elapsed = time.monotonic() - started
    types = [p.get("event_type") for p in captured]
    assert "team.stalled" in types, "busy 封顶后仍应拆流"
    assert active.await_count == 3, f"前两个窗口推迟（含保活），第三窗口封顶拆流: {active.await_count}"
    assert elapsed < 1.0, f"封顶后应尽早拆流, got {elapsed:.3f}s"


def test_idle_break_env_clamp_non_finite_and_out_of_range(monkeypatch):
    """nan/inf 穿过 <=0 / >=300 比较（IEEE 比较恒 False），必须按非有限数钳制。"""
    import importlib

    import jiuwenclaw.agentserver.deep_agent.team_helpers as th

    cases = [
        ("nan", 240.0),
        ("-nan", 240.0),
        ("inf", 240.0),
        ("abc", 240.0),
        ("301", 240.0),
        ("300", 240.0),
        ("0", 240.0),
        ("-1", 240.0),
        ("0.5", 0.5),
        ("120", 120.0),
    ]
    try:
        for raw, expected in cases:
            monkeypatch.setenv("JIUWEN_TEAM_STREAM_IDLE_BREAK_S", raw)
            importlib.reload(th)
            assert th._TEAM_STREAM_IDLE_BREAK_S == expected, raw
    finally:
        monkeypatch.delenv("JIUWEN_TEAM_STREAM_IDLE_BREAK_S", raising=False)
        importlib.reload(th)


@pytest.mark.asyncio
async def test_active_member_gate_includes_leader(monkeypatch):
    """get_team_snapshot 会把 leader 滤掉（前端展示语义），而阻塞在权限交互/长工具
    上的恰恰是 leader——门控必须走 monitor 未过滤的成员列表。"""
    from types import SimpleNamespace

    import jiuwenclaw.agentserver.deep_agent.team_helpers as th

    monitor = SimpleNamespace(
        get_members=AsyncMock(
            return_value=[SimpleNamespace(member_name="leader", status="busy")]
        )
    )
    handler = SimpleNamespace(_monitor=monitor)
    tm = SimpleNamespace(get_monitor_handler=lambda sid: handler)
    monkeypatch.setattr(th, "get_team_manager", lambda cid: tm)
    assert await th._team_stream_has_active_member("officeclaw", "s1") is True

    # 全部 settled/unstarted → False（放行拆流）
    monitor.get_members = AsyncMock(return_value=[
        SimpleNamespace(member_name="leader", status="ready"),
        SimpleNamespace(member_name="m1", status="unstarted"),
        SimpleNamespace(member_name="m2", status="paused"),
    ])
    assert await th._team_stream_has_active_member("officeclaw", "s1") is False

    # handler/monitor 缺失 → False（保守回退，维持原拆流行为）
    tm.get_monitor_handler = lambda sid: None
    assert await th._team_stream_has_active_member("officeclaw", "s1") is False
