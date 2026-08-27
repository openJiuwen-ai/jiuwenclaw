# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from types import SimpleNamespace

import pytest

from jiuwenswarm.agents.harness.common.rails.task_execution_rail import (
    TaskExecutionRail,
)


class _FakeSession:
    def __init__(self) -> None:
        self.events: list[object] = []

    def get_session_id(self) -> str:
        return "sess-1"

    async def write_stream(self, schema: object) -> None:
        self.events.append(schema)


def _task(content: str, status: str, index: int, total: int = 3) -> dict:
    return {
        "content": content,
        "status": status,
        "index": index,
        "total": total,
    }


@pytest.mark.asyncio
async def test_pending_to_completed_emits_start_then_complete(monkeypatch) -> None:
    rail = TaskExecutionRail()
    session = _FakeSession()
    rail._todo_map_before_tool = {
        "stage2_outline": _task("Stage 2-3", "in_progress", 1),
        "stage4_research": _task("Stage 4: 深度研究", "pending", 2),
        "stage5_style": _task("Stage 5: 风格", "pending", 3),
        "stage6_generate": _task("Stage 6: 生成", "pending", 4),
    }
    after_items = [
        {"id": "stage2_outline", "content": "Stage 2-3", "status": "completed"},
        {"id": "stage4_research", "content": "Stage 4: 深度研究", "status": "completed"},
        {"id": "stage5_style", "content": "Stage 5: 风格", "status": "completed"},
        {"id": "stage6_generate", "content": "Stage 6: 生成", "status": "in_progress"},
    ]
    monkeypatch.setattr(rail, "_load_todo_from_json", lambda _sid: after_items)
    rail._todo_started.add("stage2_outline")
    rail._active_tasks["todo:stage2_outline"] = SimpleNamespace(
        task_id="todo:stage2_outline",
        task_content="Stage 2-3",
        source="todo",
        start_time=0.0,
    )

    ctx = SimpleNamespace(
        session=session,
        inputs=SimpleNamespace(request_id="req-1"),
    )
    await rail._sync_todo_and_emit_transitions(ctx)

    types = [getattr(ev, "type", None) for ev in session.events]
    assert types[:6] == [
        "task.complete",
        "task.start",
        "task.complete",
        "task.start",
        "task.complete",
        "task.start",
    ]
    assert types[-1] == "task.update"

    payloads = [getattr(ev, "payload", {}) for ev in session.events]
    assert payloads[0]["task_id"] == "todo:stage2_outline"
    assert payloads[1]["task_id"] == "todo:stage4_research"
    assert payloads[2]["task_id"] == "todo:stage4_research"
    assert payloads[3]["task_id"] == "todo:stage5_style"
    assert payloads[4]["task_id"] == "todo:stage5_style"
    assert payloads[5]["task_id"] == "todo:stage6_generate"
    assert "stage4_research" in rail._todo_started
    assert "stage5_style" in rail._todo_started
    assert "stage6_generate" in rail._todo_started


@pytest.mark.asyncio
async def test_replaced_todo_list_completes_started_ids(monkeypatch) -> None:
    """todo_create with new ids must close the previous in-progress start."""
    rail = TaskExecutionRail()
    session = _FakeSession()
    rail._todo_map_before_tool = {
        "pptx_stage_1": _task("Stage 1: 需求澄清与环境检测", "in_progress", 0, 4),
        "pptx_stage_2": _task("Stage 2: 内容设计", "pending", 1, 4),
    }
    rail._todo_started.add("pptx_stage_1")
    rail._active_tasks["todo:pptx_stage_1"] = SimpleNamespace(
        task_id="todo:pptx_stage_1",
        task_content="Stage 1: 需求澄清与环境检测",
        source="todo",
        start_time=0.0,
    )
    monkeypatch.setattr(
        rail,
        "_load_todo_from_json",
        lambda _sid: [
            {"id": "stage1", "content": "阶段1：需求澄清 & 环境检测", "status": "in_progress"},
            {"id": "stage2", "content": "阶段2：内容设计", "status": "pending"},
        ],
    )
    ctx = SimpleNamespace(
        session=session,
        inputs=SimpleNamespace(request_id="req-replace"),
    )
    await rail._sync_todo_and_emit_transitions(ctx)

    types = [getattr(ev, "type", None) for ev in session.events]
    assert types[0] == "task.start"
    assert types[1] == "task.complete"
    assert types[-1] == "task.update"
    start = getattr(session.events[0], "payload", {})
    complete = getattr(session.events[1], "payload", {})
    assert start["task_id"] == "todo:stage1"
    assert complete["task_id"] == "todo:pptx_stage_1"
    assert complete["status"] == "skipped"
    assert "todo:pptx_stage_1" not in rail._active_tasks

