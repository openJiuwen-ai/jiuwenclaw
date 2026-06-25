# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

# pylint: disable=protected-access

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openjiuwen.harness.tools.todo import TodoItem, TodoStatus
from openjiuwen.harness.tools.todo_resume import TODO_RESUME_SNAPSHOT_PENDING_KEY

from jiuwenclaw.agentserver.deep_agent import interrupt_resume_helpers as helpers


class _DeepAdapterInterruptHarness:
    """Expose DeepAdapter interrupt-clear helpers for unit tests."""

    @classmethod
    def for_clear_session_test(cls):
        from jiuwenclaw.agentserver.deep_agent.interface_deep import JiuWenClawDeepAdapter

        adapter = JiuWenClawDeepAdapter.__new__(JiuWenClawDeepAdapter)
        adapter._instance = SimpleNamespace(card=MagicMock())
        return adapter

    @staticmethod
    async def clear_session_persisted_interrupt_state(adapter, session_id: str, **kwargs):
        return await adapter._clear_session_persisted_interrupt_state(session_id, **kwargs)


@pytest.mark.asyncio
async def test_prepare_interrupt_resume_noop_on_non_resume_query() -> None:
    adapter = SimpleNamespace(_instance=SimpleNamespace(card=MagicMock()))
    request = SimpleNamespace(
        session_id="sess-1",
        params={"mode": "agent.plan", "query": "帮我写一个新脚本"},
    )

    with patch.object(helpers, "create_agent_session") as create_session:
        await helpers.prepare_interrupt_resume_for_request(adapter, request)

    create_session.assert_not_called()


@pytest.mark.asyncio
async def test_prepare_interrupt_resume_sets_snapshot_pending_on_resume_query() -> None:
    session = MagicMock()
    session.pre_run = AsyncMock()
    session.post_run = AsyncMock()
    active_todo = TodoItem.create(content="task-a", status=TodoStatus.IN_PROGRESS)
    modify_tool = MagicMock()
    modify_tool.load_todos = AsyncMock(return_value=[active_todo])
    adapter = SimpleNamespace(
        _instance=SimpleNamespace(card=MagicMock()),
        _get_todo_modify_tool=MagicMock(return_value=modify_tool),
        _resolve_runtime_language=MagicMock(return_value="cn"),
    )
    request = SimpleNamespace(
        session_id="sess-2",
        params={"mode": "agent.plan", "query": "继续"},
    )

    with (
        patch.object(helpers, "create_agent_session", return_value=session),
        patch.object(helpers, "post_agent_execute_for_session", new_callable=AsyncMock),
        patch.object(helpers, "read_plan_pause_from_session", return_value=(False, None)),
        patch.object(helpers, "is_interrupt_recovery_injected", return_value=False),
    ):
        await helpers.prepare_interrupt_resume_for_request(adapter, request)

    # update_state 被调用两次：set_todo_resume_snapshot_pending 和 mark_interrupt_recovery_injected
    from jiuwenclaw.agentserver.deep_agent.plan_pause_helpers import INTERRUPT_RECOVERY_INJECTED_KEY

    assert session.update_state.call_count == 2
    session.update_state.assert_any_call({TODO_RESUME_SNAPSHOT_PENDING_KEY: True})
    session.update_state.assert_any_call({INTERRUPT_RECOVERY_INJECTED_KEY: True})


@pytest.mark.asyncio
async def test_clear_session_persisted_interrupt_state_clears_snapshot_pending() -> None:
    session = MagicMock()
    session.pre_run = AsyncMock()
    session.post_run = AsyncMock()
    adapter = _DeepAdapterInterruptHarness.for_clear_session_test()

    with (
        patch(
            "jiuwenclaw.agentserver.deep_agent.interface_deep.create_agent_session",
            return_value=session,
        ),
        patch(
            "jiuwenclaw.agentserver.deep_agent.interface_deep.clear_session_interrupt_state",
        ) as clear_interrupt,
        patch(
            "jiuwenclaw.agentserver.deep_agent.interface_deep.clear_interrupt_recovery_injected",
        ) as clear_recovery_injected,
        patch(
            "jiuwenclaw.agentserver.deep_agent.interface_deep.set_todo_resume_snapshot_pending",
        ) as clear_pending,
        patch(
            "jiuwenclaw.agentserver.deep_agent.interface_deep.post_agent_execute_for_session",
            new_callable=AsyncMock,
        ) as post_execute,
    ):
        await _DeepAdapterInterruptHarness.clear_session_persisted_interrupt_state(
            adapter,
            "sess-cancel",
            reason="interrupt(cancel)",
            clear_todo_resume_snapshot_pending=True,
        )

    clear_interrupt.assert_called_once_with(session)
    clear_recovery_injected.assert_called_once_with(session)
    clear_pending.assert_called_once_with(session, pending=False)
    post_execute.assert_awaited_once_with(session)
    session.post_run.assert_awaited_once()
