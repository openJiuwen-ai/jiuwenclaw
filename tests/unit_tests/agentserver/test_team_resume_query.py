# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for team pause→continue boundary (not plan todo_resume)."""

from __future__ import annotations

import pytest

from jiuwenclaw.agentserver.deep_agent.team_helpers import (
    _deliver_followup_interact_across_boundary,
    _detect_resume_from_pause,
    _interact_reason_requires_new_stream,
    _wrap_team_resume_protocol,
)


@pytest.mark.asyncio
async def test_gate_closed_with_paused_pool_falls_back_to_stream() -> None:
    """After pause, InteractGate is closed; continue must open RESUME_FROM_PAUSE stream."""

    class _TM:
        async def has_resumable_runtime(self, session_id: str) -> bool:
            assert session_id == "sess-paused"
            return True

        async def interact(self, session_id: str, query):
            raise AssertionError("should not poll interact when paused pool is ready")

    result = await _deliver_followup_interact_across_boundary(
        _TM(),
        "sess-paused",
        "请继续",
        initial_reason="gate_closed",
        timeout_sec=0.2,
    )
    assert result.success is False
    assert result.reason == "gate_closed"
    assert result.first_request_ready is True
    assert result.resume_from_pause is True


@pytest.mark.asyncio
async def test_not_active_with_paused_pool_is_hard_resume() -> None:
    """not_active + resumable pool must not soft-fall through as cold first."""

    class _TM:
        async def has_resumable_runtime(self, session_id: str) -> bool:
            return True

        async def interact(self, session_id: str, query):
            raise AssertionError("must not poll interact on hard resume path")

    result = await _deliver_followup_interact_across_boundary(
        _TM(),
        "sess-not-active",
        "请继续",
        initial_reason="not_active",
        timeout_sec=0.2,
    )
    assert result.success is False
    assert result.reason == "not_active"
    assert result.first_request_ready is True
    assert result.resume_from_pause is True


@pytest.mark.asyncio
async def test_native_harness_stopped_falls_back_to_stream() -> None:
    """Dead harness after pause must not surface Failed to send message."""

    class _TM:
        async def has_resumable_runtime(self, session_id: str) -> bool:
            return True

        def is_runtime_active(self, session_id: str) -> bool:
            return True

        async def interact(self, session_id: str, query):
            raise AssertionError("must not poll after NativeHarness already stopped")

    reason = (
        "deliver_to_leader_failed:[123023] deepagent runtime error, "
        "reason: NativeHarness already stopped."
    )
    assert _interact_reason_requires_new_stream(reason) is True
    result = await _deliver_followup_interact_across_boundary(
        _TM(),
        "sess-dead-harness",
        "刚才停止了，请继续",
        initial_reason=reason,
        timeout_sec=0.2,
    )
    assert result.success is False
    assert result.first_request_ready is True
    assert result.resume_from_pause is True


@pytest.mark.asyncio
async def test_gate_closed_without_pool_does_not_force_stream() -> None:
    class _TM:
        async def has_resumable_runtime(self, session_id: str) -> bool:
            return False

        def is_runtime_active(self, session_id: str) -> bool:
            return False

        def is_runtime_pending(self, session_id: str) -> bool:
            return False

        def has_stream_task(self, session_id: str) -> bool:
            return False

        async def interact(self, session_id: str, query):
            return False, "gate_closed"

    # No claw-local runtime → boundary returns first_request_ready via empty-runtime path.
    result = await _deliver_followup_interact_across_boundary(
        _TM(),
        "sess-gone",
        "请继续",
        initial_reason="gate_closed",
        timeout_sec=0.2,
    )
    assert result.success is False
    assert result.first_request_ready is True
    assert result.resume_from_pause is False


def test_wrap_team_resume_protocol_cn() -> None:
    wrapped = _wrap_team_resume_protocol("请继续", "zh", original_query="研究AI办公并写PPT")
    assert "【团队暂停续跑协议】" in wrapped
    assert "不要无故整图重开" in wrapped
    assert "请继续" in wrapped
    assert "研究AI办公并写PPT" in wrapped
    assert "禁止再向用户索要已给出的目标" in wrapped
    # Idempotent
    assert _wrap_team_resume_protocol(wrapped, "zh") == wrapped


def test_wrap_team_resume_protocol_falls_back_to_user_query() -> None:
    wrapped = _wrap_team_resume_protocol("请继续执行研究任务", "zh")
    assert "原任务目标" in wrapped
    assert "请继续执行研究任务" in wrapped


@pytest.mark.asyncio
async def test_detect_resume_from_pause_via_paused_bookmark() -> None:
    """Clear-init first path still needs protocol wrap when paused bookmark exists."""

    class _TM:
        def get_paused_team_name(self, session_id: str) -> str | None:
            assert session_id == "sess-paused"
            return "team-research"

        async def has_resumable_runtime(self, session_id: str) -> bool:
            raise AssertionError("paused bookmark should short-circuit")

    assert await _detect_resume_from_pause(_TM(), "sess-paused") is True


@pytest.mark.asyncio
async def test_detect_resume_from_pause_via_runner_pool() -> None:
    class _TM:
        def get_paused_team_name(self, session_id: str) -> str | None:
            return None

        async def has_resumable_runtime(self, session_id: str) -> bool:
            return True

    assert await _detect_resume_from_pause(_TM(), "sess-pool") is True


@pytest.mark.asyncio
async def test_detect_resume_from_pause_false_when_idle() -> None:
    class _TM:
        def get_paused_team_name(self, session_id: str) -> str | None:
            return None

        async def has_resumable_runtime(self, session_id: str) -> bool:
            return False

    assert await _detect_resume_from_pause(_TM(), "sess-idle") is False
    assert await _detect_resume_from_pause(_TM(), "sess-force", force_resume_stream=True) is True
