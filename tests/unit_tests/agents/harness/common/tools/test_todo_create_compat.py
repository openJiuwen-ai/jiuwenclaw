# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from types import SimpleNamespace

import pytest

from jiuwenswarm.agents.harness.common.rails.task_execution_rail import TaskExecutionRail
from jiuwenswarm.agents.harness.common.tools.todo_compat import CompatibleTodoCreateTool
from openjiuwen.harness.schema.task import TodoItem, TodoStatus


def test_format_create_result_does_not_prompt_immediate_execution() -> None:
    tool = CompatibleTodoCreateTool.__new__(CompatibleTodoCreateTool)
    todos = [
        TodoItem(
            id="submit_report",
            content="Submit report tomorrow",
            activeForm="Preparing report",
            description="Due tomorrow",
            status=TodoStatus.PENDING,
        )
    ]
    message = tool._format_create_result(todos)
    assert "Immediately execute" not in message
    assert "pending" in message.lower()


@pytest.mark.asyncio
async def test_create_from_list_demotes_in_progress_to_pending() -> None:
    tool = CompatibleTodoCreateTool.__new__(CompatibleTodoCreateTool)
    saved: list[list[TodoItem]] = []

    async def _save(_session_id: str, todos: list[TodoItem]) -> None:
        saved.append(list(todos))

    async def _load(_session_id: str) -> list[TodoItem]:
        return list(saved[-1]) if saved else []

    tool.save_todos = _save  # type: ignore[method-assign]
    tool.load_todos = _load  # type: ignore[method-assign]

    tasks_data = [
        {
            "id": "submit_report",
            "content": "Submit report tomorrow",
            "activeForm": "Preparing report",
            "description": "Due tomorrow",
        }
    ]
    message = await tool._create_from_list("sess-1", tasks_data)

    assert saved, "todo_create should persist tasks"
    assert all(todo.status == TodoStatus.PENDING for todo in saved[-1])
    assert "Immediately execute" not in message


@pytest.mark.asyncio
async def test_pending_create_does_not_emit_task_start(monkeypatch) -> None:
    rail = TaskExecutionRail()
    session = _FakeSession()
    rail._todo_map_before_tool = {}
    after_items = [
        {"id": "submit_report", "content": "Submit report", "status": "pending"},
    ]
    monkeypatch.setattr(rail, "_load_todo_from_json", lambda _sid: after_items)

    ctx = SimpleNamespace(
        session=session,
        inputs=SimpleNamespace(request_id="req-1"),
    )
    await rail._sync_todo_and_emit_transitions(ctx)

    types = [getattr(ev, "type", None) for ev in session.events]
    assert "task.start" not in types
    assert types[-1] == "task.update"


class _FakeSession:
    def __init__(self) -> None:
        self.events: list[object] = []

    def get_session_id(self) -> str:
        return "sess-1"

    async def write_stream(self, schema: object) -> None:
        self.events.append(schema)
