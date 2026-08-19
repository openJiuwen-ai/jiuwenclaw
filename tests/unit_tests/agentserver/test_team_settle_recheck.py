# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""leader-final 后 settle 复评（settle recheck）。

2026-08-13 事故（对话1/对话2 同根因）：轮次收尾只在 leader chat.final 的
瞬间做一次 `_team_round_settled` 点判定；彼时成员尚未落定（对话1 差 ~60ms，
对话2 leader 口头派活零任务、成员更晚才落定）则判定失败，之后不再有任何
复评触发——流挂死直至 idle-stall 拆流报错（team_stalled）。

修复：见过 leader final 后，把 wait_for 超时缩短为复评 tick，每个 tick 复评
settle——settled 即正常收尾（team.completed、无 team.stalled）；超 idle 预算
仍不 settled 才落入既有 pending/busy/teardown 决策树。
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest


def _FakeStream(chunks=None, block_after=False):
    """真实 async generator（不是 mock）：yield chunks 后阻塞或自然结束。

    2026-08-13 事故教训：旧的 _FakeStream 是普通对象，__anext__ 被 wait_for
    取消后对象不关闭，与真实 async generator 语义不同（后者被取消即运行
    finally/finalize）——因此盖不住"复评 tick 取消 __anext__ 误杀团队"的回归。
    这里必须用真生成器复现线上一致的行为。
    """

    async def _gen():
        for c in list(chunks or []):
            yield c
        if block_after:
            await asyncio.sleep(100)  # blocks -> tick 超时

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


def _patch_common(monkeypatch, tm, stream, settled, *, pending=None, active=None):
    import jiuwenclaw.agentserver.deep_agent.team_helpers as th

    monkeypatch.setattr(th, "get_team_manager", lambda cid: tm)
    monkeypatch.setattr(th.Runner, "run_agent_team_streaming", lambda **kw: stream)
    captured: list = []
    monkeypatch.setattr(th, "_broadcast_event", lambda cid, sid, p: captured.append(p))
    monkeypatch.setattr(th, "_team_round_settled", settled)
    monkeypatch.setattr(th, "_emit_team_chat_file_events", lambda *a, **k: None)
    monkeypatch.setattr(th, "_broadcast_team_state_snapshot", AsyncMock())
    monkeypatch.setattr(th, "_team_workspace_root", lambda spec: "/tmp/ws")
    monkeypatch.setattr(th, "TeamStreamLogger", MagicMock(return_value=None))
    # 第一个 chunk 当普通帧（None 丢弃），第二个当 leader chat.final
    final_event = {"event_type": "chat.final", "content": "总结", "role": "leader"}
    parse_results = [None, final_event]
    monkeypatch.setattr(
        th, "parse_stream_chunk", lambda c: parse_results.pop(0) if parse_results else None
    )
    monkeypatch.setattr(
        th,
        "_team_has_pending_user_decision",
        pending if pending is not None else AsyncMock(return_value=False),
    )
    monkeypatch.setattr(
        th,
        "_team_stream_has_active_member",
        active if active is not None else AsyncMock(return_value=False),
    )
    return captured


def _patch_timeouts(monkeypatch, idle_s=5.0, recheck_s=0.05):
    import jiuwenclaw.agentserver.deep_agent.team_helpers as th

    monkeypatch.setattr(th, "_TEAM_STREAM_IDLE_BREAK_S", idle_s)
    monkeypatch.setattr(th, "_TEAM_STREAM_SETTLE_RECHECK_S", recheck_s)


@pytest.mark.asyncio
async def test_leader_final_settle_race_finishes_cleanly(monkeypatch):
    """对话1 竞态复现：leader final 点判定时成员未落定（False），
    下一个复评 tick 落定（True）→ 必须干净收尾：team.completed、无 team.stalled、
    且远早于 idle 预算耗尽。"""
    import time

    import jiuwenclaw.agentserver.deep_agent.team_helpers as th

    _patch_timeouts(monkeypatch, idle_s=5.0, recheck_s=0.05)
    settled = AsyncMock(side_effect=[False, True, True])  # final 点判定 / 复评 / finally
    stream = _FakeStream(chunks=[_chunk(), _chunk()], block_after=True)
    captured = _patch_common(monkeypatch, _mock_tm(), stream, settled)
    started = time.monotonic()
    await th._consume_stream_with_query_impl(
        "officeclaw", "officeclaw_s1", MagicMock(), "q",
        round_id=1, envs={}, hide_dm=False,
    )
    elapsed = time.monotonic() - started
    types = [p.get("event_type") for p in captured]
    assert "team.stalled" not in types, types
    assert "team.completed" in types, types
    assert elapsed < 2.0, f"复评应快速收尾而不是等满 idle 预算, got {elapsed:.3f}s"


@pytest.mark.asyncio
async def test_leader_final_settle_recheck_waits_multiple_ticks(monkeypatch):
    """对话2 同构：final 后成员多个 tick 才落定 → 复评持续等到 settled 再收尾。"""
    import time

    import jiuwenclaw.agentserver.deep_agent.team_helpers as th

    _patch_timeouts(monkeypatch, idle_s=5.0, recheck_s=0.05)
    # final 点判定 False；复评第 1、2 tick 未落定；第 3 tick 落定；finally 复判 True
    settled = AsyncMock(side_effect=[False, False, False, True, True])
    stream = _FakeStream(chunks=[_chunk(), _chunk()], block_after=True)
    captured = _patch_common(monkeypatch, _mock_tm(), stream, settled)
    started = time.monotonic()
    await th._consume_stream_with_query_impl(
        "officeclaw", "officeclaw_s1", MagicMock(), "q",
        round_id=1, envs={}, hide_dm=False,
    )
    elapsed = time.monotonic() - started
    types = [p.get("event_type") for p in captured]
    assert "team.stalled" not in types, types
    assert "team.completed" in types, types
    assert elapsed >= 0.1, f"应等够多个复评 tick, got {elapsed:.3f}s"
    assert elapsed < 2.0, f"不应等满 idle 预算, got {elapsed:.3f}s"


@pytest.mark.asyncio
async def test_leader_final_never_settled_still_tears_down(monkeypatch):
    """回归：final 后永不 settled → 超 idle 预算仍按 stalled 拆流（行为不变）。"""
    import jiuwenclaw.agentserver.deep_agent.team_helpers as th

    _patch_timeouts(monkeypatch, idle_s=0.15, recheck_s=0.05)
    settled = AsyncMock(return_value=False)
    stream = _FakeStream(chunks=[_chunk(), _chunk()], block_after=True)
    captured = _patch_common(monkeypatch, _mock_tm(), stream, settled)
    await th._consume_stream_with_query_impl(
        "officeclaw", "officeclaw_s1", MagicMock(), "q",
        round_id=1, envs={}, hide_dm=False,
    )
    types = [p.get("event_type") for p in captured]
    assert "team.stalled" in types, types


@pytest.mark.asyncio
async def test_leader_final_pending_user_decision_defers_recheck(monkeypatch):
    """final 后挂在 ask_user 等用户：pending 期间不收尾不拆流也不复评；
    用户应答（pending 清除）且 settled 后干净收尾。"""
    import jiuwenclaw.agentserver.deep_agent.team_helpers as th

    _patch_timeouts(monkeypatch, idle_s=5.0, recheck_s=0.05)
    pending = AsyncMock(side_effect=[True, True, False])
    settled = AsyncMock(side_effect=[False, True, True])  # final 点判定 / 复评 / finally
    stream = _FakeStream(chunks=[_chunk(), _chunk()], block_after=True)
    captured = _patch_common(monkeypatch, _mock_tm(), stream, settled, pending=pending)
    await th._consume_stream_with_query_impl(
        "officeclaw", "officeclaw_s1", MagicMock(), "q",
        round_id=1, envs={}, hide_dm=False,
    )
    types = [p.get("event_type") for p in captured]
    assert "team.stalled" not in types, types
    assert "team.completed" in types, types
    assert pending.await_count == 3, pending.await_count
    # pending 期间不得触发 settle 复评：final 点判定 1 次 + 复评 1 次 + finally 1 次
    assert settled.await_count == 3, settled.await_count
    # pending 等待期间必须向 relay 广播业务帧保活（防 relay 300s 看门狗抢跑）：
    # 流启动 1 次 + 2 个 pending tick 各 1 次
    pings = [
        p for p in captured
        if p.get("event_type") == "chat.processing_status"
        and p.get("is_processing")
        and not p.get("is_complete")
    ]
    assert len(pings) >= 3, f"pending 期间缺少保活帧: {len(pings)}"


@pytest.mark.asyncio
async def test_leader_final_recheck_skipped_while_leader_active(monkeypatch):
    """final 后 leader 被迟到的成员消息唤醒开新 turn（快照滤掉 leader，
    settle 判定看不出来）——active 门控必须挡住复评收尾，否则误杀新 turn；
    leader 闲下来且 settled 后才收尾。"""
    import jiuwenclaw.agentserver.deep_agent.team_helpers as th

    _patch_timeouts(monkeypatch, idle_s=5.0, recheck_s=0.05)
    # 第 1 个 tick leader 还在忙（新 turn 静默期），第 2 个 tick 闲下来
    active = AsyncMock(side_effect=[True, False])
    # final 点判定 False；active 为真的 tick 不得触发复评；active 转闲后复评 True；finally True
    settled = AsyncMock(side_effect=[False, True, True])
    stream = _FakeStream(chunks=[_chunk(), _chunk()], block_after=True)
    captured = _patch_common(monkeypatch, _mock_tm(), stream, settled, active=active)
    await th._consume_stream_with_query_impl(
        "officeclaw", "officeclaw_s1", MagicMock(), "q",
        round_id=1, envs={}, hide_dm=False,
    )
    types = [p.get("event_type") for p in captured]
    assert "team.stalled" not in types, types
    assert "team.completed" in types, types
    assert active.await_count == 2, active.await_count
    assert settled.await_count == 3, settled.await_count


@pytest.mark.asyncio
async def test_recheck_tick_keeps_stream_alive_for_late_chunks(monkeypatch):
    """2026-08-13 复评 tick 误杀事故回归：leader final 之后只要出现超过 recheck
    tick（2s）的 chunk 间隙（成员 LLM 思考间隙再正常不过），老实现
    wait_for(__anext__, timeout=recheck) 会在首个 tick 取消挂起的 __anext__，
    CancelledError 抛进 Runner 生成器，finally 链直接 finalize/pause 整个团队
    ——下一个 __anext__ 立刻 StopAsyncIteration，流"自然"结束，迟到 chunk 全丢。

    修复后 tick 用 asyncio.wait 不取消：迟到 chunk 必须照常到达，且最终干净收尾。
    """
    import time

    import jiuwenclaw.agentserver.deep_agent.team_helpers as th

    _patch_timeouts(monkeypatch, idle_s=5.0, recheck_s=0.05)

    async def _gen():
        yield _chunk()  # 普通帧（parse 丢弃）
        yield _chunk()  # leader chat.final
        await asyncio.sleep(0.2)  # 超过 recheck tick 的 LLM 思考间隙
        yield _chunk()  # 迟到 chunk——老代码在首个 tick 已杀死生成器，永远收不到
        await asyncio.sleep(100)

    # final 点判定 False；3 个复评 tick 未落定；迟到 chunk 后下一 tick 落定；finally True
    settled = AsyncMock(side_effect=[False, False, False, False, True, True])
    captured = _patch_common(monkeypatch, _mock_tm(), _gen(), settled)
    parse_results = [
        None,
        {"event_type": "chat.final", "content": "总结", "role": "leader"},
        {"event_type": "chat.late_after_tick"},
    ]
    monkeypatch.setattr(
        th,
        "parse_stream_chunk",
        lambda c: parse_results.pop(0) if parse_results else None,
    )
    started = time.monotonic()
    await th._consume_stream_with_query_impl(
        "officeclaw", "officeclaw_s1", MagicMock(), "q",
        round_id=1, envs={}, hide_dm=False,
    )
    elapsed = time.monotonic() - started
    types = [p.get("event_type") for p in captured]
    assert "chat.late_after_tick" in types, f"tick 后流被误杀，迟到 chunk 丢失: {types}"
    assert "team.stalled" not in types, types
    assert "team.completed" in types, types
    assert elapsed < 2.0, f"复评应快速收尾而不是等满 idle 预算, got {elapsed:.3f}s"
