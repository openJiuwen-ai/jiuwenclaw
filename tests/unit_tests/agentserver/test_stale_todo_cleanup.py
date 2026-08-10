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
async def test_prepare_stale_todo_cleanup_new_task_not_blocked_by_interrupt_sentinel() -> None:
    """BUG2026080900034: 新任务即使残留中断恢复哨兵，也必须清理旧任务的 active todo。

    之前 prepare_interrupt_artifacts_for_request 兜底注入会把 is_interrupt_recovery_injected
    置位，导致 prepare_stale_todo_cleanup 被一票否决、跳过清理，进而 before_invoke 把旧任务
    （Root）的 todo 广播成新任务（Hook/RTK）的工具步骤。修复后该哨兵不再阻止新任务的清理。
    """
    session = MagicMock()
    session.pre_run = AsyncMock()
    session.post_run = AsyncMock()
    modify_tool = MagicMock()
    modify_tool.load_todos = AsyncMock(return_value=_active_todos())
    agent_card = MagicMock()
    params: dict = {
        "mode": "agent.plan",
        "query": "安装Hook、RTK这类的节省token的插件",
    }
    request = SimpleNamespace(session_id="sess-bug34", request_id="req-bug34", params=params)

    with (
        patch.object(helpers, "create_agent_session", return_value=session),
        # 模拟"中断产物摘要兜底注入后哨兵被置位"的外部状态；修复后该状态不应阻止清理。
        patch.object(
            helpers,
            "post_agent_execute_for_session",
            new_callable=AsyncMock,
        ) as post_execute,
        patch.object(
            helpers,
            "cancel_pending_todos_on_tool",
            new_callable=AsyncMock,
            return_value=True,
        ) as cancel,
        patch.object(helpers, "set_todo_resume_snapshot_pending") as clear_pending,
        patch.object(helpers, "mark_skip_invoke_task_update_sync") as mark_skip,
    ):
        cancelled = await helpers.prepare_stale_todo_cleanup_for_request(
            request,
            agent_card=agent_card,
            get_todo_modify_tool=lambda _sid: modify_tool,
        )

    assert cancelled is True
    cancel.assert_awaited_once_with(modify_tool, "sess-bug34")
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
async def test_prepare_stale_todo_cleanup_noop_without_any_todos() -> None:
    session = MagicMock()
    session.pre_run = AsyncMock()
    session.post_run = AsyncMock()
    modify_tool = MagicMock()
    modify_tool.load_todos = AsyncMock(return_value=[])
    params = {"mode": "agent.plan", "query": "做一个新页面"}
    request = SimpleNamespace(session_id="sess-done", request_id="req-done", params=params)

    with (
        patch.object(helpers, "create_agent_session", return_value=session),
        patch.object(helpers, "clear_skip_invoke_task_update_sync"),
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


@pytest.mark.asyncio
async def test_prepare_stale_todo_cleanup_marks_skip_for_completed_only() -> None:
    """BUG2026080900061: 只有 completed 的旧 todo 也要 mark skip，
    让广播层过滤掉旧 request 的 completed todo，防止跨请求串台。
    """
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
        patch.object(helpers, "cancel_pending_todos_on_tool", new_callable=AsyncMock) as cancel,
        patch.object(helpers, "set_todo_resume_snapshot_pending") as clear_pending,
        patch.object(helpers, "mark_skip_invoke_task_update_sync") as mark_skip,
        patch.object(helpers, "post_agent_execute_for_session", new_callable=AsyncMock) as post_execute,
    ):
        cancelled = await helpers.prepare_stale_todo_cleanup_for_request(
            request,
            agent_card=MagicMock(),
            get_todo_modify_tool=lambda _sid: modify_tool,
        )

    assert cancelled is True
    cancel.assert_awaited_once_with(modify_tool, "sess-done")
    clear_pending.assert_called_once_with(session, pending=False)
    mark_skip.assert_called_once_with(session)
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


@pytest.mark.asyncio
async def test_before_invoke_records_stale_todo_ids_when_skip_flag_set() -> None:
    """skip 标志为 True 时，before_invoke 应记录旧 todo id 供广播层过滤。"""
    rail = TaskExecutionRail()
    session = MagicMock()
    session.get_session_id.return_value = "sess-rail"
    ctx = _invoke_ctx(session)

    async def _seed_active_todos(_session: object) -> None:
        rail._todo_map = {
            "old-1": {"status": "completed", "content": "旧任务"},
            "old-2": {"status": "in_progress", "content": "旧任务2"},
        }

    with (
        patch(_SKIP_SYNC_PATCH, return_value=True),
        patch.object(rail, "_init_task_tracking", side_effect=_seed_active_todos),
        patch.object(rail, "_emit_task_update_event", new_callable=AsyncMock),
    ):
        await rail.before_invoke(ctx)

    assert rail._stale_todo_ids == {"old-1", "old-2"}


@pytest.mark.asyncio
async def test_before_invoke_does_not_record_stale_ids_without_skip_flag() -> None:
    """续跑场景（skip 标志 False）不应记录 stale ids，保证旧 todo 正常广播。"""
    rail = TaskExecutionRail()
    session = MagicMock()
    session.get_session_id.return_value = "sess-rail"
    ctx = _invoke_ctx(session)

    async def _seed_active_todos(_session: object) -> None:
        rail._todo_map = {"1": {"status": "in_progress", "content": "active task"}}

    with (
        patch(_SKIP_SYNC_PATCH, return_value=False),
        patch.object(rail, "_init_task_tracking", side_effect=_seed_active_todos),
        patch.object(rail, "_emit_task_update_event", new_callable=AsyncMock),
    ):
        await rail.before_invoke(ctx)

    assert rail._stale_todo_ids == set()


@pytest.mark.asyncio
async def test_emit_task_update_event_filters_stale_todo_ids() -> None:
    """_emit_task_update_event 应过滤掉 _stale_todo_ids 中的旧 todo，
    只广播当前 request 的新 todo，防止跨请求串台。
    """
    rail = TaskExecutionRail()
    rail._stale_todo_ids = {"old-1", "old-2"}
    session = MagicMock()
    session.get_session_id.return_value = "sess-rail"

    # todo.json 含旧 todo + 新 todo
    all_todos = [
        {"id": "old-1", "content": "旧任务1", "status": "completed", "index": 0},
        {"id": "old-2", "content": "旧任务2", "status": "cancelled", "index": 1},
        {"id": "new-1", "content": "新任务1", "status": "in_progress", "index": 0},
    ]

    with (
        patch.object(rail, "_load_todo_from_json", return_value=all_todos),
        patch.object(rail, "_format_tasks_for_update", side_effect=lambda items, source: items) as fmt,
    ):
        session.write_stream = AsyncMock()
        await rail._emit_task_update_event(session, "req-new")

    # 只传入了过滤后的 todo（new-1），旧 todo 被排除
    filtered_items = fmt.call_args.args[0]
    filtered_ids = [t["id"] for t in filtered_items]
    assert filtered_ids == ["new-1"]
    assert "old-1" not in filtered_ids
    assert "old-2" not in filtered_ids


@pytest.mark.asyncio
async def test_after_invoke_clears_stale_todo_ids() -> None:
    """after_invoke 应清空 _stale_todo_ids，避免影响下一轮。"""
    rail = TaskExecutionRail()
    rail._stale_todo_ids = {"old-1", "old-2"}
    session = MagicMock()
    ctx = _invoke_ctx(session)

    with patch("jiuwenclaw.agentserver.deep_agent.rails.task_execution_rail.clear_skip_invoke_task_update_sync"):
        await rail.after_invoke(ctx)

    assert rail._stale_todo_ids == set()


# ---------------------------------------------------------------------------
# should_cancel_stale_active_todos 各跳过路径
# ---------------------------------------------------------------------------

def test_should_cancel_skips_heartbeat() -> None:
    request = SimpleNamespace(session_id="heartbeat-123")
    params = {"mode": "agent.plan", "query": "anything"}
    assert helpers.should_cancel_stale_active_todos(request, params) is False


def test_should_cancel_skips_non_plan_mode() -> None:
    request = SimpleNamespace(session_id="sess-1")
    params = {"mode": "agent.chat", "query": "做一个新页面"}
    assert helpers.should_cancel_stale_active_todos(request, params) is False


def test_should_cancel_skips_answers() -> None:
    request = SimpleNamespace(session_id="sess-1")
    params = {"mode": "agent.plan", "query": "新任务", "answers": {"q1": "a1"}}
    assert helpers.should_cancel_stale_active_todos(request, params) is False


def test_should_cancel_skips_long_resume_phrase() -> None:
    """长续跑语句（>32 字符）不匹配 is_resume_user_query，应当作新任务清理。

    这是已知的遗留局限：长续跑语句会被误判为新任务。
    此测试固化当前行为，后续升级续跑判定时需更新。
    """
    request = SimpleNamespace(session_id="sess-1")
    long_resume = "继续刚才那个清理电脑的任务，从第五步开始"  # > 32 chars
    params = {"mode": "agent.plan", "query": long_resume}
    assert helpers.should_cancel_stale_active_todos(request, params) is True


# ---------------------------------------------------------------------------
# _emit_task_update_event 边界场景
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_emit_task_update_event_no_filter_without_stale_ids() -> None:
    """续跑场景（_stale_todo_ids 为空）应全量广播，不过滤。"""
    rail = TaskExecutionRail()
    rail._stale_todo_ids = set()
    session = MagicMock()
    session.get_session_id.return_value = "sess-rail"

    all_todos = [
        {"id": "old-1", "content": "旧任务", "status": "in_progress", "index": 0},
        {"id": "new-1", "content": "新任务", "status": "pending", "index": 1},
    ]

    with (
        patch.object(rail, "_load_todo_from_json", return_value=all_todos),
        patch.object(rail, "_format_tasks_for_update", side_effect=lambda items, source: items) as fmt,
    ):
        session.write_stream = AsyncMock()
        await rail._emit_task_update_event(session, "req-resume")

    broadcasted_ids = [t["id"] for t in fmt.call_args.args[0]]
    assert broadcasted_ids == ["old-1", "new-1"]


@pytest.mark.asyncio
async def test_emit_task_update_event_empty_after_filter() -> None:
    """所有 todo 都是 stale 时，过滤后应广播空列表。"""
    rail = TaskExecutionRail()
    rail._stale_todo_ids = {"old-1", "old-2"}
    session = MagicMock()
    session.get_session_id.return_value = "sess-rail"

    all_todos = [
        {"id": "old-1", "content": "旧任务1", "status": "completed", "index": 0},
        {"id": "old-2", "content": "旧任务2", "status": "cancelled", "index": 1},
    ]

    with (
        patch.object(rail, "_load_todo_from_json", return_value=all_todos),
        patch.object(rail, "_format_tasks_for_update", side_effect=lambda items, source: items) as fmt,
    ):
        session.write_stream = AsyncMock()
        await rail._emit_task_update_event(session, "req-new")

    filtered_items = fmt.call_args.args[0]
    assert filtered_items == []


@pytest.mark.asyncio
async def test_emit_task_update_event_filters_todos_without_id_field() -> None:
    """todo 项缺少 id 字段时，_get_todo_key 使用 idx 或 index 作为 fallback，
    _build_map_from_todo_items 和过滤逻辑应保持一致，确保旧 todo 被正确过滤。
    """
    rail = TaskExecutionRail()
    session = MagicMock()
    session.get_session_id.return_value = "sess-rail"

    # 无 id 字段的 todo，_build_map_from_todo_items 会用 str(index) 作为 key
    # 模拟 before_invoke 已记录 stale ids = {"0", "1"}
    rail._stale_todo_ids = {"0", "1"}

    all_todos = [
        {"content": "旧任务1", "status": "completed", "index": 0},  # 无 id -> key "0"
        {"content": "旧任务2", "status": "cancelled", "index": 1},   # 无 id -> key "1"
        {"id": "new-1", "content": "新任务", "status": "in_progress", "index": 2},
    ]

    with (
        patch.object(rail, "_load_todo_from_json", return_value=all_todos),
        patch.object(rail, "_format_tasks_for_update", side_effect=lambda items, source: items) as fmt,
    ):
        session.write_stream = AsyncMock()
        await rail._emit_task_update_event(session, "req-new")

    filtered_items = fmt.call_args.args[0]
    assert len(filtered_items) == 1
    assert filtered_items[0]["id"] == "new-1"


# ---------------------------------------------------------------------------
# before_invoke 边界场景
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_before_invoke_skip_flag_but_empty_todo_map() -> None:
    """skip 标志 True 但 todo_map 为空时，stale_todo_ids 应为空集。"""
    rail = TaskExecutionRail()
    session = MagicMock()
    session.get_session_id.return_value = "sess-rail"
    ctx = _invoke_ctx(session)

    async def _seed_empty(_session: object) -> None:
        rail._todo_map = {}

    with (
        patch(_SKIP_SYNC_PATCH, return_value=True),
        patch.object(rail, "_init_task_tracking", side_effect=_seed_empty),
        patch.object(rail, "_emit_task_update_event", new_callable=AsyncMock),
    ):
        await rail.before_invoke(ctx)

    assert rail._stale_todo_ids == set()


@pytest.mark.asyncio
async def test_two_consecutive_turns_stale_ids_not_leaked() -> None:
    """连续两轮：第一轮 after_invoke 清空 stale_ids 后，第二轮不应误过滤。"""
    rail = TaskExecutionRail()
    session = MagicMock()
    session.get_session_id.return_value = "sess-rail"

    # 第一轮：skip 标志 True，记录 stale ids
    ctx1 = _invoke_ctx(session, "req-1")

    async def _seed_old_todos(_session: object) -> None:
        rail._todo_map = {"old-1": {"status": "completed", "content": "旧"}}

    with (
        patch(_SKIP_SYNC_PATCH, return_value=True),
        patch.object(rail, "_init_task_tracking", side_effect=_seed_old_todos),
        patch.object(rail, "_emit_task_update_event", new_callable=AsyncMock),
    ):
        await rail.before_invoke(ctx1)

    assert rail._stale_todo_ids == {"old-1"}

    # after_invoke 清空
    with patch("jiuwenclaw.agentserver.deep_agent.rails.task_execution_rail.clear_skip_invoke_task_update_sync"):
        await rail.after_invoke(ctx1)

    assert rail._stale_todo_ids == set()

    # 第二轮：skip 标志 False（正常续跑），不记录 stale ids
    ctx2 = _invoke_ctx(session, "req-2")

    async def _seed_new_todos(_session: object) -> None:
        rail._todo_map = {"new-1": {"status": "in_progress", "content": "新"}}

    with (
        patch(_SKIP_SYNC_PATCH, return_value=False),
        patch.object(rail, "_init_task_tracking", side_effect=_seed_new_todos),
        patch.object(rail, "_emit_task_update_event", new_callable=AsyncMock) as emit,
    ):
        await rail.before_invoke(ctx2)

    assert rail._stale_todo_ids == set()
    emit.assert_awaited_once()


# ---------------------------------------------------------------------------
# cancel_pending_todos_on_tool 数据层行为
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cancel_pending_todos_only_cancels_active_not_completed() -> None:
    """cancel_pending_todos_on_tool 只 cancel pending/in_progress，
    不碰 completed 和 cancelled。
    """
    from jiuwenclaw.agentserver.deep_agent.plan_pause_helpers import (
        cancel_pending_todos_on_tool,
    )

    modify_tool = MagicMock()
    modify_tool.file_path_for_session = MagicMock(return_value="/fake/todo.json")

    mixed_todos = [
        TodoItem.create(content="已完成步骤", status=TodoStatus.COMPLETED),
        TodoItem.create(content="进行中步骤", status=TodoStatus.IN_PROGRESS),
        TodoItem.create(content="待办步骤", status=TodoStatus.PENDING),
        TodoItem.create(content="已取消步骤", status=TodoStatus.CANCELLED),
    ]
    modify_tool.load_todos = AsyncMock(return_value=mixed_todos)

    cancel_mock = AsyncMock()
    with patch(
        "jiuwenclaw.agentserver.deep_agent.plan_pause_helpers.cancel_todos_via_modify_tool",
        new=cancel_mock,
    ):
        result = await cancel_pending_todos_on_tool(modify_tool, "sess-1")

    assert result is True
    # 只 cancel 了 in_progress 和 pending 的 id，completed/cancelled 不在列表中
    cancelled_ids = cancel_mock.call_args.args[1]
    assert len(cancelled_ids) == 2
    completed_id = mixed_todos[0].id
    cancelled_id = mixed_todos[3].id
    assert completed_id not in cancelled_ids
    assert cancelled_id not in cancelled_ids


@pytest.mark.asyncio
async def test_cancel_pending_todos_returns_false_for_completed_only() -> None:
    """只有 completed/cancelled 时，cancel_pending_todos_on_tool 返回 False（无 active 可 cancel）。"""
    from jiuwenclaw.agentserver.deep_agent.plan_pause_helpers import (
        cancel_pending_todos_on_tool,
    )

    modify_tool = MagicMock()
    modify_tool.file_path_for_session = MagicMock(return_value="/fake/todo.json")
    completed_only = [
        TodoItem.create(content="done1", status=TodoStatus.COMPLETED),
        TodoItem.create(content="done2", status=TodoStatus.CANCELLED),
    ]
    modify_tool.load_todos = AsyncMock(return_value=completed_only)

    cancel_mock = AsyncMock()
    with patch(
        "jiuwenclaw.agentserver.deep_agent.plan_pause_helpers.cancel_todos_via_modify_tool",
        new=cancel_mock,
    ):
        result = await cancel_pending_todos_on_tool(modify_tool, "sess-1")

    assert result is False
    cancel_mock.assert_not_called()


# ---------------------------------------------------------------------------
# 混合 todo 清理场景（active + completed + cancelled）
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_prepare_cleanup_mixed_todos_marks_skip_and_cancels_active() -> None:
    """混合 todo（active + completed + cancelled）时：
    - cancel 只处理 active（由 cancel_pending_todos_on_tool 保证）
    - mark skip 始终执行（让广播层过滤所有旧 todo）
    """
    session = MagicMock()
    session.pre_run = AsyncMock()
    session.post_run = AsyncMock()
    mixed = [
        TodoItem.create(content="completed-1", status=TodoStatus.COMPLETED),
        TodoItem.create(content="in-progress-1", status=TodoStatus.IN_PROGRESS),
        TodoItem.create(content="pending-1", status=TodoStatus.PENDING),
        TodoItem.create(content="cancelled-1", status=TodoStatus.CANCELLED),
    ]
    modify_tool = MagicMock()
    modify_tool.load_todos = AsyncMock(return_value=mixed)
    params = {"mode": "agent.plan", "query": "做一个全新任务"}
    request = SimpleNamespace(session_id="sess-mix", request_id="req-mix", params=params)

    with (
        patch.object(helpers, "create_agent_session", return_value=session),
        patch.object(helpers, "clear_skip_invoke_task_update_sync"),
        patch.object(helpers, "cancel_pending_todos_on_tool", new_callable=AsyncMock) as cancel,
        patch.object(helpers, "set_todo_resume_snapshot_pending") as clear_pending,
        patch.object(helpers, "mark_skip_invoke_task_update_sync") as mark_skip,
        patch.object(helpers, "post_agent_execute_for_session", new_callable=AsyncMock),
    ):
        result = await helpers.prepare_stale_todo_cleanup_for_request(
            request,
            agent_card=MagicMock(),
            get_todo_modify_tool=lambda _sid: modify_tool,
        )

    assert result is True
    cancel.assert_awaited_once_with(modify_tool, "sess-mix")
    mark_skip.assert_called_once_with(session)
    clear_pending.assert_called_once_with(session, pending=False)
