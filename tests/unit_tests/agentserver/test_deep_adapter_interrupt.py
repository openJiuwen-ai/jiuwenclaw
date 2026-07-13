# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for JiuWenSwarmDeepAdapter interrupt when stream consumer already unwound."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from jiuwenswarm.common.schema.agent import AgentRequest
from jiuwenswarm.common.schema.message import ReqMethod
from jiuwenswarm.server.runtime.agent_adapter.interface_deep import JiuWenSwarmDeepAdapter


def _build_cancel_request(session_id: str = "tui_sess_1") -> AgentRequest:
    return AgentRequest(
        request_id="req-cancel",
        channel_id="tui",
        session_id=session_id,
        req_method=ReqMethod.CHAT_CANCEL,
        params={"intent": "cancel", "mode": "agent.plan"},
    )


def _make_adapter(**state: object) -> JiuWenSwarmDeepAdapter:
    """Create a bare adapter with internal state set via setattr."""
    adapter = object.__new__(JiuWenSwarmDeepAdapter)
    adapter._is_session_scoped_adapter = True  # pylint: disable=protected-access
    adapter._parent_session_id = None  # pylint: disable=protected-access
    for name, value in state.items():
        setattr(adapter, name, value)
    return adapter


@pytest.mark.asyncio
async def test_cancel_runs_teardown_when_session_not_in_active_counter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When session is not active, per-session teardown runs but global abort is skipped.

    Global abort (instance.abort) is unsafe when the session is inactive — a
    just-starting session on the same adapter could be killed as collateral.
    Per-session teardown (rail abort, shell kill) is sufficient for the target.
    """
    rail = MagicMock()
    rail.get_cancelled_tool_results.return_value = []
    instance = MagicMock()
    instance.abort = AsyncMock()
    adapter = _make_adapter(
        _active_session_ids={},
        _session_agent_tasks={},
        _stream_event_rail=rail,
        _instance=instance,
    )

    kill_mock = MagicMock(return_value=2)
    monkeypatch.setattr(
        "openjiuwen.core.sys_operation.shell_process_registry.kill_shell_processes_for_session_tree",
        kill_mock,
    )
    monkeypatch.setattr(adapter, "_cancel_pending_todos", AsyncMock(return_value=[]))
    monkeypatch.setattr(adapter, "_cancel_scheduler_running_tasks", MagicMock())

    response = await adapter.process_interrupt(_build_cancel_request())

    # Per-session teardown must still run
    rail.abort.assert_called_once_with("tui_sess_1")
    rail.collect_cancelled_tool_updates.assert_called_once_with("tui_sess_1")
    rail.reset_for_new_task.assert_called_once_with("tui_sess_1")
    kill_mock.assert_called_once_with("tui_sess_1")
    # Global abort must NOT fire — session is inactive, could kill a just-starting session
    instance.abort.assert_not_awaited()
    assert response.payload["event_type"] == "chat.interrupt_result"
    assert response.payload["intent"] == "cancel"
    assert response.payload["success"] is True


@pytest.mark.asyncio
async def test_unmark_skips_rail_cleanup_when_stream_consumer_cancelled() -> None:
    rail = MagicMock()
    adapter = _make_adapter(
        _active_session_ids={"sess_a": 1},
        _stream_event_rail=rail,
    )

    getattr(adapter, "_unmark_session_active")("sess_a", cleanup_rail=False)

    rail.cleanup_session.assert_not_called()
    assert "sess_a" not in getattr(adapter, "_active_session_ids")


@pytest.mark.asyncio
async def test_unmark_cleans_rail_on_normal_completion() -> None:
    rail = MagicMock()
    adapter = _make_adapter(
        _active_session_ids={"sess_a": 1},
        _stream_event_rail=rail,
    )

    getattr(adapter, "_unmark_session_active")("sess_a")

    rail.cleanup_session.assert_called_once_with("sess_a")
    assert "sess_a" not in getattr(adapter, "_active_session_ids")


@pytest.mark.asyncio
async def test_abort_skipped_when_other_sessions_active_even_if_target_executing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """instance.abort() is global on the shared DeepAgent — when other sessions are
    active, it must NEVER be called, even if the target session is executing.
    Per-session teardown (rail abort, task cancel, shell kill) is sufficient.
    """
    rail = MagicMock()
    rail.get_cancelled_tool_results.return_value = []
    instance = MagicMock()
    setattr(instance, "abort", AsyncMock())
    setattr(instance, "_invoke_active", True)
    stream_task = MagicMock()
    stream_task.done.return_value = False
    setattr(instance, "_stream_process_task", stream_task)
    loop_session = MagicMock()
    loop_session.get_session_id.return_value = "tui_target"
    setattr(instance, "_loop_session", loop_session)
    adapter = _make_adapter(
        _active_session_ids={"tui_other": 1},
        _session_agent_tasks={},
        _stream_event_rail=rail,
        _instance=instance,
    )

    monkeypatch.setattr(
        "openjiuwen.core.sys_operation.shell_process_registry.kill_shell_processes_for_session_tree",
        MagicMock(return_value=0),
    )
    monkeypatch.setattr(adapter, "_cancel_pending_todos", AsyncMock(return_value=[]))
    monkeypatch.setattr(adapter, "_cancel_scheduler_running_tasks", MagicMock())

    await adapter.process_interrupt(_build_cancel_request(session_id="tui_target"))

    # instance.abort must NOT be called — it would kill tui_other's work too
    instance.abort.assert_not_awaited()
    # But per-session teardown must still run
    rail.abort.assert_called_once_with("tui_target")


def test_reset_runtime_cron_context_resets_shell_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openjiuwen.core.sys_operation.shell_process_registry import (
        set_shell_session_id,
    )

    reset_shell_mock = MagicMock()
    monkeypatch.setattr(
        "openjiuwen.core.sys_operation.shell_process_registry.reset_shell_session_id",
        reset_shell_mock,
    )
    for var_name in (
        "_CRON_TOOL_MODE",
        "_CRON_TOOL_METADATA",
        "_CRON_TOOL_SESSION_ID",
        "_CRON_TOOL_CHANNEL_ID",
    ):
        monkeypatch.setattr(
            f"jiuwenswarm.server.runtime.agent_adapter.interface_deep.{var_name}",
            MagicMock(),
        )

    shell_token = set_shell_session_id("sess_reset")
    getattr(JiuWenSwarmDeepAdapter, "_reset_runtime_cron_context")(
        (
            MagicMock(),
            MagicMock(),
            MagicMock(),
            MagicMock(),
            shell_token,
        )
    )
    reset_shell_mock.assert_called_once_with(shell_token)