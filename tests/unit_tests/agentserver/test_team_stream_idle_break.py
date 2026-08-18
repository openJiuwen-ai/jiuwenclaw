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


def _FakeStream(chunks=None, block_after=False):
    """真实 async generator（不是 mock）：yield chunks 后阻塞或自然结束。

    与 test_team_settle_recheck.py 同名工厂保持一致：普通对象的 __anext__ 被取消
    后对象不关闭，盖不住"tick 取消 __anext__ 误杀流"类回归（2026-08-13 事故），
    必须用真生成器。
    """

    async def _gen():
        for c in list(chunks or []):
            yield c
        if block_after:
            await asyncio.sleep(100)  # blocks -> tick 超时 -> idle-break

    return _gen()


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


def test_permission_pending_index_lifecycle():
    """PermissionInterruptRail 待答索引：store/pop/clear 三态转换 + session 隔离。"""
    from jiuwenclaw.agentserver.deep_agent.rails import permission_rail as pr

    session = MagicMock()
    session.session_id = "s_perm_1"
    other = MagicMock()
    other.session_id = "s_perm_2"
    try:
        assert not pr.has_pending_permission_for_session("s_perm_1")
        pr._track_pending_permission(session, "call_1")
        pr._track_pending_permission(session, "call_2")
        assert pr.has_pending_permission_for_session("s_perm_1")
        assert not pr.has_pending_permission_for_session("s_perm_2")
        pr._untrack_pending_permission(session, "call_1")
        assert pr.has_pending_permission_for_session("s_perm_1")  # call_2 仍待答
        pr._track_pending_permission(other, "call_9")
        pr.clear_session_interrupt_state(session)
        assert not pr.has_pending_permission_for_session("s_perm_1")
        assert pr.has_pending_permission_for_session("s_perm_2")  # 其他 session 不受影响
    finally:
        pr._PENDING_PERMISSION_BY_SESSION.pop("s_perm_1", None)
        pr._PENDING_PERMISSION_BY_SESSION.pop("s_perm_2", None)


@pytest.mark.asyncio
async def test_idle_break_defers_while_permission_ask_pending(monkeypatch):
    """2026-08-13 事故回归：leader 收尾调 send_file_to_user 命中 defaults.guard
    → 权限审批卡已发给用户、leader run 以 success 结束（成员快照不 busy，
    AskUserQuestionRegistry 也没有记录）——流静默是在等用户点卡，idle-break
    240s 后把团队拆了；用户随后作答撞 interact not_active，只能 hard RESUME。

    修复后 pending 判定覆盖 PermissionInterruptRail 待答索引：审批卡未答期间
    不得拆流（持续保活）；用户作答（索引清除）后超 idle 预算才允许拆。
    """
    import time

    import jiuwenclaw.agentserver.deep_agent.team_helpers as th
    from jiuwenclaw.agentserver.deep_agent.rails import permission_rail as pr

    monkeypatch.setattr(th, "_TEAM_STREAM_IDLE_BREAK_S", 0.15)
    monkeypatch.setattr(th, "_team_stream_has_active_member", AsyncMock(return_value=False))
    # 本文件 _patch_common 不替换 _team_has_pending_user_decision——走真实实现，
    # 正好验证 rail 待答索引 → pending 判定的集成链路。
    session = MagicMock()
    session.session_id = "officeclaw_s1"
    pr._track_pending_permission(session, "call_send_file_1")
    stream = _FakeStream(chunks=[_chunk()], block_after=True)
    captured = _patch_common(monkeypatch, _mock_tm(), stream, settled=False)

    pings: list = []

    def _broadcast(cid, sid, p):
        captured.append(p)
        if (
            p.get("event_type") == "chat.processing_status"
            and p.get("is_processing")
            and not p.get("is_complete")
        ):
            pings.append(p)
            if len(pings) == 3:
                # 流启动 ping + 2 个推迟 tick 后，模拟用户点了审批卡。
                pr._untrack_pending_permission(session, "call_send_file_1")

    monkeypatch.setattr(th, "_broadcast_event", _broadcast)

    started = time.monotonic()
    try:
        await th._consume_stream_with_query_impl(
            "officeclaw", "officeclaw_s1", MagicMock(), "q",
            round_id=1, envs={}, hide_dm=False,
        )
    finally:
        pr._PENDING_PERMISSION_BY_SESSION.pop("officeclaw_s1", None)
    elapsed = time.monotonic() - started
    types = [p.get("event_type") for p in captured]
    # 用户作答后允许拆流（不死等）；且拆流只能发生在多个推迟 tick 之后——
    # 若权限 pending 不可见，首个 0.15s 窗口就会误拆（elapsed < 0.2s）。
    assert "team.stalled" in types, types
    assert len(pings) >= 3, f"待答期间缺少保活帧: {len(pings)}"
    assert elapsed >= 0.4, f"权限审批待答期间不得拆流, got {elapsed:.3f}s"
