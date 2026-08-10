# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Team progress ping: relay stall watchdog liveness during E2A-suppressed segments."""

from __future__ import annotations

from jiuwenclaw.agentserver.deep_agent import team_helpers


def test_is_e2a_suppressed_event_matches_suppressed_types() -> None:
    assert team_helpers._is_e2a_suppressed_event("chat.tool_calls.delta") is True


def test_is_e2a_suppressed_event_rejects_business_types() -> None:
    for et in ("chat.delta", "chat.reasoning", "chat.final", "chat.error",
               "chat.usage_metadata", "chat.processing_status", "team.task",
               "team.member", "chat.tool_call", "chat.tool_result"):
        assert team_helpers._is_e2a_suppressed_event(et) is False, et


def test_is_e2a_suppressed_event_handles_none_and_blank() -> None:
    assert team_helpers._is_e2a_suppressed_event(None) is False
    assert team_helpers._is_e2a_suppressed_event("") is False


# ── 循环级测试 ────────────────────────────────────────────────

import asyncio
import time as _real_time
from types import SimpleNamespace

import pytest

from openjiuwen.agent_teams.schema.team import TeamRole


class _FakeMonitorHandler:
    def __init__(self, snapshot) -> None:
        self._snapshot = snapshot

    async def get_team_snapshot(self):
        return self._snapshot


class _FakeTeamManager:
    """Minimal TeamManager stand-in (pattern from test_team_direct_answer_finish)."""

    def __init__(self) -> None:
        self.events: list[dict] = []
        self._seen = False
        self._handler = _FakeMonitorHandler(None)

    def reset_seen_events(self, session_id: str) -> None:
        self._seen = False

    def reset_seen_team_events(self, session_id: str) -> None:
        self._seen = False

    def has_seen_team_events(self, session_id: str) -> bool:
        return self._seen

    def mark_seen_team_events(self, session_id: str) -> None:
        self._seen = True

    def reset_workflow_completed(self, session_id: str) -> None:
        pass

    def is_workflow_completed(self, session_id: str) -> bool:
        return False

    def broadcast_event(self, session_id: str, event: dict) -> None:
        self.events.append(dict(event))

    def pop_stream_task(self, session_id: str):
        return None

    def clear_pending_runtime(self, session_id: str) -> None:
        pass

    def clear_active_runtime(self, session_id: str, bookmark_paused: bool = False) -> None:
        pass

    def is_pause_in_progress(self, session_id: str) -> bool:
        return False

    def get_monitor_handler(self, session_id: str):
        return self._handler

    def get_workflow_handler(self, session_id: str):
        return None


class _FakeClock:
    """team_helpers.time 替身：monotonic 由测试控制，time/sleep 透传真实实现。"""

    def __init__(self) -> None:
        self.now = 1_000.0

    def monotonic(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds

    time = staticmethod(_real_time.time)
    sleep = staticmethod(_real_time.sleep)


_FAKE_CLOCK = _FakeClock()


def _delta_chunk(call_id: str = "call_1") -> SimpleNamespace:
    return SimpleNamespace(
        type="tool_calls.delta",
        payload={"tool_calls": [{"id": call_id, "name": "write_file",
                                 "arguments_delta": "{\"content\": \""}]},
        role=TeamRole.TEAMMATE,
        source_member="creative-experience-producer",
    )


def _reasoning_chunk() -> SimpleNamespace:
    return SimpleNamespace(
        type="llm_reasoning",
        payload={"content": "思考中"},
        role=TeamRole.TEAMMATE,
        source_member="creative-experience-producer",
    )


def _usage_chunk() -> SimpleNamespace:
    return SimpleNamespace(
        type="llm_usage",
        payload={"metadata": {"usage_metadata": {"total_tokens": 10}}},
        role=TeamRole.TEAMMATE,
        source_member="creative-experience-producer",
    )


def _make_scripted_stream(script):
    """script: list of (chunk_factory, advance_seconds)。逐条推进假时钟后 yield。"""

    async def _gen(**kwargs):
        for factory, advance in script:
            _FAKE_CLOCK.advance(advance)
            yield factory()

    return _gen


def _pings(tm: _FakeTeamManager) -> list[dict]:
    """补发的 processing_status（流开始那帧在任何 delta 之前，不计入）。"""
    result: list[dict] = []
    seen_delta = False
    for e in tm.events:
        et = e.get("event_type")
        if et == "chat.tool_calls.delta":
            seen_delta = True
        elif et == "chat.processing_status" and e.get("is_processing") is True and seen_delta:
            result.append(e)
    return result


async def _run_consume(monkeypatch: pytest.MonkeyPatch, tm: _FakeTeamManager,
                       script) -> None:
    _FAKE_CLOCK.now = 1_000.0
    monkeypatch.setattr(team_helpers, "get_team_manager", lambda channel_id=None: tm)
    monkeypatch.setattr(team_helpers, "time", _FAKE_CLOCK)
    monkeypatch.delenv(team_helpers._STREAM_TRACE_ENV_KEY, raising=False)
    monkeypatch.delenv(team_helpers._HIDE_TEAMMATE_ENV_KEY, raising=False)
    monkeypatch.setattr(
        team_helpers.Runner, "run_agent_team_streaming",
        _make_scripted_stream(script),
    )
    team_spec = SimpleNamespace(team_name="oc_team_test", workspace=None)
    await asyncio.wait_for(
        team_helpers._consume_stream_with_query_impl(
            "officeclaw", "sess-ping", team_spec, "写文件",
            round_id=1, envs={}, hide_dm=True,
        ),
        timeout=10,
    )


@pytest.mark.asyncio
async def test_ping_during_long_suppressed_delta_segment(monkeypatch: pytest.MonkeyPatch) -> None:
    """100s 纯 tool_calls.delta 段（每 chunk 推进 10s）→ 恰好在 30/60/90s 补发 3 次。"""
    tm = _FakeTeamManager()
    script = [(_delta_chunk, 10.0)] * 10
    await _run_consume(monkeypatch, tm, script)

    pings = _pings(tm)
    assert len(pings) == 3
    for ping in pings:
        assert ping["session_id"] == "sess-ping"
        assert ping["rid"] == 1
        assert ping["is_processing"] is True
        assert ping["is_complete"] is False
    # 原 delta 广播不减少（只加不减）
    deltas = [e for e in tm.events if e.get("event_type") == "chat.tool_calls.delta"]
    assert len(deltas) == 10


@pytest.mark.asyncio
async def test_no_ping_when_business_frames_flow(monkeypatch: pytest.MonkeyPatch) -> None:
    """delta 段中穿插 llm_reasoning（业务帧，刷新计时器）→ 永不补发。"""
    tm = _FakeTeamManager()
    script = [(_delta_chunk, 20.0), (_reasoning_chunk, 20.0)] * 4
    await _run_consume(monkeypatch, tm, script)

    assert _pings(tm) == []
    reasoning = [e for e in tm.events if e.get("event_type") == "chat.reasoning"]
    assert len(reasoning) == 4


@pytest.mark.asyncio
async def test_usage_metadata_does_not_reset_ping_timer(monkeypatch: pytest.MonkeyPatch) -> None:
    """llm_usage 拦截分支上 wire 但 relay 不算业务帧 → 不得刷新计时器。"""
    tm = _FakeTeamManager()
    # 第 2 个 delta 时距上次业务帧（循环入口）60s ≥ 30 → ping 恰好 1 次
    script = [(_delta_chunk, 20.0), (_usage_chunk, 20.0), (_delta_chunk, 20.0)]
    await _run_consume(monkeypatch, tm, script)

    assert len(_pings(tm)) == 1
    usage = [e for e in tm.events if e.get("event_type") == "chat.usage_metadata"]
    assert len(usage) == 1
