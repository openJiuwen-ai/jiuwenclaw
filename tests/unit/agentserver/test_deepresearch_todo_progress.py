# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for projecting DeepResearch stage snapshots to the harness todo file."""
# pylint: disable=protected-access

from __future__ import annotations

import json
from types import SimpleNamespace

from openjiuwen.harness.tools.todo import TodoItem

from jiuwenclaw.agentserver.deep_agent.rails.task_execution_rail import (
    TaskExecutionRail,
)
from jiuwenclaw.agentserver.tools.deepresearch.stream_router import (
    DEEPRESEARCH_STAGES,
)
from jiuwenclaw.agentserver.tools.deepresearch.todo_progress import (
    deepresearch_todo_path,
    persist_deepresearch_task_update,
)


def _stage_snapshot(active_stage: int | None) -> dict:
    tasks = []
    for index, title in enumerate(DEEPRESEARCH_STAGES, start=1):
        if active_stage is None or index < active_stage:
            status = "completed"
        elif index == active_stage:
            status = "in_progress"
        else:
            status = "pending"
        tasks.append({
            "task_id": f"deepresearch_stage_{index}",
            "task_content": title,
            "status": status,
        })
    return {"event_type": "task.update", "tasks": tasks}


def test_deepresearch_todo_path_uses_tenant_workspace(tmp_path, monkeypatch):
    calls = []

    def _resolve(service_id, agent_id):
        calls.append((service_id, agent_id))
        return tmp_path

    monkeypatch.setattr(
        "jiuwenclaw.agentserver.tools.deepresearch.todo_progress."
        "resolve_tenant_agent_workspace_dir",
        _resolve,
    )

    path = deepresearch_todo_path(
        session_id="session-1",
        service_id="service-1",
        agent_id="agent-1",
    )

    assert path == tmp_path / "todo" / "session-1" / "todo.json"
    assert calls == [("service-1", "agent-1")]


def test_persist_deepresearch_task_update_ignores_incomplete_snapshots(tmp_path):
    todo_path = tmp_path / "session-1" / "todo.json"

    for active_stage in range(1, len(DEEPRESEARCH_STAGES) + 1):
        assert not persist_deepresearch_task_update(
            _stage_snapshot(active_stage=active_stage),
            todo_path=todo_path,
        )
        assert not todo_path.exists()


def test_persist_deepresearch_task_update_retains_completed_four_stage_plan(tmp_path):
    todo_path = tmp_path / "todo" / "session-1" / "todo.json"

    assert persist_deepresearch_task_update(
        _stage_snapshot(active_stage=None),
        todo_path=todo_path,
    )

    items = json.loads(todo_path.read_text(encoding="utf-8"))
    assert len(items) == 4
    assert [item["id"] for item in items] == [
        f"deepresearch_stage_{index}" for index in range(1, 5)
    ]
    assert [item["content"] for item in items] == list(DEEPRESEARCH_STAGES)
    assert [item["activeForm"] for item in items] == list(DEEPRESEARCH_STAGES)
    assert all(item["status"] == "completed" for item in items)
    for item in items:
        TodoItem.from_dict(item)

    rail = TaskExecutionRail()
    rail.workspace = SimpleNamespace(
        get_node_path=lambda _node: tmp_path / "todo",
    )
    retained = rail._load_todo_from_json("session-1")
    formatted = rail._format_tasks_for_update(retained, source="todo")
    assert [item["task_id"] for item in formatted] == [
        f"deepresearch_stage_{index}" for index in range(1, 5)
    ]
    assert all(item["status"] == "completed" for item in formatted)


def test_incomplete_snapshot_does_not_replace_retained_completed_plan(tmp_path):
    todo_path = tmp_path / "todo" / "session-1" / "todo.json"

    assert persist_deepresearch_task_update(
        _stage_snapshot(active_stage=None),
        todo_path=todo_path,
    )
    retained = todo_path.read_text(encoding="utf-8")

    assert not persist_deepresearch_task_update(
        _stage_snapshot(active_stage=1),
        todo_path=todo_path,
    )
    assert todo_path.read_text(encoding="utf-8") == retained


def test_persist_deepresearch_task_update_ignores_other_snapshots(tmp_path):
    todo_path = tmp_path / "session-1" / "todo.json"
    payload = {
        "event_type": "task.update",
        "tasks": [{
            "task_id": "another_task",
            "task_content": "not DeepResearch",
            "status": "completed",
        }],
    }

    assert not persist_deepresearch_task_update(payload, todo_path=todo_path)
    assert not todo_path.exists()
