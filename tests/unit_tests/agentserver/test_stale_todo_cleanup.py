# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.


from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openjiuwen.core.single_agent.rail.base import InvokeInputs
from openjiuwen.harness.tools.todo import TodoItem, TodoStatus

from jiuwenclaw.agentserver.deep_agent import stale_todo_cleanup_helpers as helpers
from jiuwenclaw.agentserver.deep_agent.rails.task_execution_rail import TaskExecutionRail

_SKIP_SYNC_PATCH = (
    "jiuwenclaw.agentserver.deep_agent.rails.task_execution_rail"
    ".is_skip_invoke_task_update_sync"
)


def _active_todos() -> list[TodoItem]:
    return [
        TodoItem.create(content="删除表头统计栏", status=TodoStatus.COMPLETED),
        TodoItem.create(content="重新生成PPT文件", status=TodoStatus.IN_PROGRESS),
    ]


def test_should_cancel_stale_active_todos_plain_new_query() -> None:
    request = SimpleNamespace(session_id="sess-1")
    params = {"mode": "agent.plan", "query": "做一个新页面"}
    assert helpers.should_cancel_stale_active_todos(request, params) is True


def test_should_cancel_stale_active_todos_skips_resume_and_supplement() -> None:
    resume_request = SimpleNamespace(session_id="sess-2")
    resume_params = {"mode": "agent.plan", "query": "继续"}
    assert helpers.should_cancel_stale_active_todos(resume_request, resume_params) is False

    supplement_params = {
        "mode": "agent.plan",
        "query": "新任务",
        "is_supplement": True,
    }
    assert helpers.should_cancel_stale_active_todos(resume_request, supplement_params) is False


@pytest.mark.asyncio
async def test_prepare_stale_todo_cleanup_cancels_and_sets_skip_flag() -> None:
    session = MagicMock()
    session.pre_run = AsyncMock()
    session.post_run = AsyncMock()
    modify_tool = MagicMock()
    modify_tool.load_todos = AsyncMock(return_value=_active_todos())
    agent_card = MagicMock()
    params: dict = {
        "mode": "agent.plan",
        "query": "新增场景在局点列表中使用浅绿色表示",
    }
    request = SimpleNamespace(session_id="sess-officeace", request_id="req-new", params=params)

    with (
        patch.object(helpers, "create_agent_session", return_value=session),
        patch.object(helpers, "is_interrupt_recovery_injected", return_value=False),
        patch.object(
            helpers,
            "cancel_pending_todos_on_tool",
            new_callable=AsyncMock,
            return_value=True,
        ) as cancel,
        patch.object(helpers, "set_todo_resume_snapshot_pending") as clear_pending,
        patch.object(helpers, "clear_skip_invoke_task_update_sync") as clear_skip,
        patch.object(helpers, "mark_skip_invoke_task_update_sync") as mark_skip,
        patch.object(helpers, "post_agent_execute_for_session", new_callable=AsyncMock) as post_execute,
    ):
        cancelled = await helpers.prepare_stale_todo_cleanup_for_request(
            request,
            agent_card=agent_card,
            get_todo_modify_tool=lambda _sid: modify_tool,
        )

    assert cancelled is True
    clear_skip.assert_called_once_with(session)
    cancel.assert_awaited_once_with(modify_tool, "sess-officeace")
    clear_pending.assert_called_once_with(session, pending=False)
    mark_skip.assert_called_once_with(session)
    post_execute.assert_awaited_once_with(session)


@pytest.mark.asyncio
async def test_prepare_stale_todo_cleanup_clears_stale_skip_flag_on_resume() -> None:
    """Resume turns must not inherit skip flag from a crashed prior cleanup turn."""
    session = MagicMock()
    session.pre_run = AsyncMock()
    session.post_run = AsyncMock()
    request = SimpleNamespace(
        session_id="sess-resume",
        request_id="req-resume",
        params={"mode": "agent.plan", "query": "继续"},
    )

    with (
        patch.object(helpers, "create_agent_session", return_value=session),
        patch.object(helpers, "clear_skip_invoke_task_update_sync") as clear_skip,
        patch.object(helpers, "post_agent_execute_for_session", new_callable=AsyncMock) as post_execute,
    ):
        cancelled = await helpers.prepare_stale_todo_cleanup_for_request(
            request,
            agent_card=MagicMock(),
            get_todo_modify_tool=MagicMock(),
        )

    assert cancelled is False
    clear_skip.assert_called_once_with(session)
    post_execute.assert_awaited_once_with(session)


@pytest.mark.asyncio
async def test_prepare_stale_todo_cleanup_skips_supplement() -> None:
    request = SimpleNamespace(
        session_id="sess-supplement",
        request_id="req-supplement",
        params={
            "mode": "agent.plan",
            "query": "换成另一个任务",
            "is_supplement": True,
        },
    )

    with (
        patch.object(helpers, "create_agent_session", return_value=MagicMock(
            pre_run=AsyncMock(),
            post_run=AsyncMock(),
        )),
        patch.object(helpers, "clear_skip_invoke_task_update_sync") as clear_skip,
        patch.object(helpers, "post_agent_execute_for_session", new_callable=AsyncMock) as post_execute,
    ):
        cancelled = await helpers.prepare_stale_todo_cleanup_for_request(
            request,
            agent_card=MagicMock(),
            get_todo_modify_tool=MagicMock(),
        )

    assert cancelled is False
    clear_skip.assert_called_once()
    post_execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_prepare_stale_todo_cleanup_noop_without_active_todos() -> None:
    session = MagicMock()
    session.pre_run = AsyncMock()
    session.post_run = AsyncMock()
    completed_only = [TodoItem.create(content="done", status=TodoStatus.COMPLETED)]
    modify_tool = MagicMock()
    modify_tool.load_todos = AsyncMock(return_value=completed_only)
    params = {"mode": "agent.plan", "query": "做一个新页面"}
    request = SimpleNamespace(session_id="sess-done", request_id="req-done", params=params)

    with (
        patch.object(helpers, "create_agent_session", return_value=session),
        patch.object(helpers, "clear_skip_invoke_task_update_sync"),
        patch.object(helpers, "is_interrupt_recovery_injected", return_value=False),
        patch.object(helpers, "cancel_pending_todos_on_tool", new_callable=AsyncMock) as cancel,
        patch.object(helpers, "post_agent_execute_for_session", new_callable=AsyncMock) as post_execute,
    ):
        cancelled = await helpers.prepare_stale_todo_cleanup_for_request(
            request,
            agent_card=MagicMock(),
            get_todo_modify_tool=lambda _sid: modify_tool,
        )

    assert cancelled is False
    cancel.assert_not_called()
    post_execute.assert_awaited_once_with(session)


def test_skip_invoke_session_state_helpers() -> None:
    from jiuwenclaw.agentserver.deep_agent.plan_pause_helpers import (
        SKIP_INVOKE_TASK_UPDATE_SYNC_KEY,
        clear_skip_invoke_task_update_sync,
        is_skip_invoke_task_update_sync,
        mark_skip_invoke_task_update_sync,
    )

    session = MagicMock()
    session.get_state.return_value = None

    mark_skip_invoke_task_update_sync(session)
    session.update_state.assert_called_with({SKIP_INVOKE_TASK_UPDATE_SYNC_KEY: True})

    session.get_state.return_value = True
    assert is_skip_invoke_task_update_sync(session) is True

    clear_skip_invoke_task_update_sync(session)
    session.update_state.assert_called_with({SKIP_INVOKE_TASK_UPDATE_SYNC_KEY: None})


def _invoke_ctx(session: MagicMock, request_id: str = "req-rail") -> SimpleNamespace:
    inputs = InvokeInputs(query="rail test")
    inputs.request_id = request_id
    return SimpleNamespace(session=session, inputs=inputs)


@pytest.mark.asyncio
async def test_before_invoke_skips_task_update_when_skip_flag_set() -> None:
    rail = TaskExecutionRail()
    session = MagicMock()
    session.get_session_id.return_value = "sess-rail"
    ctx = _invoke_ctx(session)

    async def _seed_active_todos(_session: object) -> None:
        rail._todo_map = {"1": {"status": "in_progress", "content": "old task"}}

    with (
        patch(_SKIP_SYNC_PATCH, return_value=True),
        patch.object(rail, "_init_task_tracking", side_effect=_seed_active_todos),
        patch.object(rail, "_emit_task_update_event", new_callable=AsyncMock) as emit_update,
    ):
        await rail.before_invoke(ctx)

    emit_update.assert_not_called()


@pytest.mark.asyncio
async def test_before_invoke_emits_task_update_without_skip_flag() -> None:
    rail = TaskExecutionRail()
    session = MagicMock()
    session.get_session_id.return_value = "sess-rail"
    ctx = _invoke_ctx(session)

    async def _seed_active_todos(_session: object) -> None:
        rail._todo_map = {"1": {"status": "in_progress", "content": "active task"}}

    with (
        patch(_SKIP_SYNC_PATCH, return_value=False),
        patch.object(rail, "_init_task_tracking", side_effect=_seed_active_todos),
        patch.object(rail, "_emit_task_update_event", new_callable=AsyncMock) as emit_update,
    ):
        await rail.before_invoke(ctx)

    emit_update.assert_awaited_once_with(session, "req-rail")
