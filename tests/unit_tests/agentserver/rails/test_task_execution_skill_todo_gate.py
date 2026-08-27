# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackContext,
    ToolCallInputs,
)

from jiuwenswarm.agents.harness.common.rails.task_execution_rail import (
    TaskExecutionRail,
)


class _FakeSession:
    def __init__(self, session_id: str = "sess-1") -> None:
        self._session_id = session_id
        self.events: list[object] = []

    def get_session_id(self) -> str:
        return self._session_id

    async def write_stream(self, schema: object) -> None:
        self.events.append(schema)


def _ctx(
    tool_name: str,
    *,
    tool_id: str = "c1",
    extra: dict | None = None,
    messages: list | None = None,
    tool_msg: object | None = None,
    tool_result: object | None = None,
    session_id: str = "sess-1",
) -> AgentCallbackContext:
    session = _FakeSession(session_id)
    ctx = AgentCallbackContext(
        agent=MagicMock(),
        session=session,
        extra={},
        inputs=ToolCallInputs(
            tool_call=SimpleNamespace(id=tool_id, name=tool_name),
            tool_name=tool_name,
            tool_args={},
            tool_result=tool_result,
            tool_msg=tool_msg,
        ),
    )
    if messages is not None:
        ctx.context = SimpleNamespace(get_messages=lambda: messages)
    if extra:
        ctx.extra.update(extra)
    return ctx


@pytest.mark.asyncio
async def test_skill_tool_arms_gate_and_blocks_work_without_todo() -> None:
    rail = TaskExecutionRail()
    load_ctx = _ctx(
        "skill_tool",
        tool_msg=SimpleNamespace(metadata={"skill_name": "pptx-craft"}),
        tool_result="# PPT 全流程",
    )
    await rail.after_tool_call(load_ctx)
    assert rail._skill_todo_required is True

    work_ctx = _ctx("bash", messages=[])
    await rail.before_tool_call(work_ctx)
    assert work_ctx.extra.get("_skip_tool") is True
    assert "SKILL_TODO_REQUIRED" in str(work_ctx.inputs.tool_result)


@pytest.mark.asyncio
async def test_parallel_todo_create_does_not_block_work_tool() -> None:
    rail = TaskExecutionRail()
    rail._skill_todo_required = True
    messages = [
        SimpleNamespace(
            tool_calls=[
                SimpleNamespace(id="c-todo", name="todo_create"),
                SimpleNamespace(id="c-bash", name="bash"),
            ]
        )
    ]
    work_ctx = _ctx("bash", tool_id="c-bash", messages=messages)
    await rail.before_tool_call(work_ctx)
    assert work_ctx.extra.get("_skip_tool") is not True


@pytest.mark.asyncio
async def test_existing_incomplete_todo_does_not_block() -> None:
    rail = TaskExecutionRail()
    rail._skill_todo_required = True
    rail._todo_map = {
        "stage1": {
            "content": "阶段1",
            "status": "in_progress",
            "index": 0,
            "total": 1,
        }
    }
    work_ctx = _ctx("bash", messages=[])
    await rail.before_tool_call(work_ctx)
    assert work_ctx.extra.get("_skip_tool") is not True


@pytest.mark.asyncio
async def test_skill_acceleration_exec_is_exempt_and_disarms_gate() -> None:
    rail = TaskExecutionRail()
    rail._skill_todo_required = True
    accel_ctx = _ctx("skill_acceleration_exec", messages=[])
    await rail.before_tool_call(accel_ctx)
    assert accel_ctx.extra.get("_skip_tool") is not True
    assert rail._skill_todo_required is False


@pytest.mark.asyncio
async def test_directory_listing_does_not_arm_gate() -> None:
    rail = TaskExecutionRail()
    load_ctx = _ctx(
        "skill_tool",
        tool_msg=SimpleNamespace(
            metadata={"is_directory_listing": True, "skill_name": "pptx-craft"}
        ),
        tool_result="files: ...",
    )
    await rail.after_tool_call(load_ctx)
    assert rail._skill_todo_required is False


@pytest.mark.asyncio
async def test_before_invoke_clears_gate() -> None:
    rail = TaskExecutionRail()
    rail._skill_todo_required = True
    ctx = AgentCallbackContext(
        agent=MagicMock(),
        session=SimpleNamespace(get_session_id=lambda: "sess-1"),
        extra={},
        inputs=SimpleNamespace(),
    )
    await rail.before_invoke(ctx)
    assert rail._skill_todo_required is False
    assert rail._skill_stages_owned is False


PPTX_BODY = {
    "success": True,
    "data": {
        "skill_content": (
            "## 阶段 1：需求澄清 & 环境检测\n"
            "## 阶段 2：内容设计\n"
            "## 阶段 3：视觉设计\n"
            "## 阶段 4：HTML 生成、修复与导出\n"
        )
    },
}


@pytest.mark.asyncio
async def test_skill_tool_seeds_todos_from_skill_headings(
    tmp_path, monkeypatch
) -> None:
    rail = TaskExecutionRail()
    todo_path = tmp_path / "sess-1" / "todo.json"
    monkeypatch.setattr(
        rail, "_get_todo_workspace_path", lambda _sid: todo_path
    )
    load_ctx = _ctx(
        "skill_tool",
        tool_msg=SimpleNamespace(
            metadata={"skill_name": "pptx-craft"},
            content="loaded",
        ),
        tool_result=PPTX_BODY,
    )
    await rail.after_tool_call(load_ctx)
    assert rail._skill_stages_owned is True
    assert rail._skill_todo_required is False
    assert list(rail._todo_map) == [
        "skill_stage_1",
        "skill_stage_2",
        "skill_stage_3",
        "skill_stage_4",
    ]
    assert rail._todo_map["skill_stage_1"]["content"] == (
        "阶段 1：需求澄清 & 环境检测"
    )
    types = [getattr(ev, "type", None) for ev in load_ctx.session.events]
    assert "task.update" in types
    assert "task.start" not in types
    assert "SKILL.md 的阶段标题" in str(load_ctx.inputs.tool_msg.content)

    create_ctx = _ctx("todo_create")
    await rail.before_tool_call(create_ctx)
    assert create_ctx.extra.get("_skip_tool") is True
    assert "SKILL_STAGES_LOCKED" in str(create_ctx.inputs.tool_result)


@pytest.mark.asyncio
async def test_nested_skill_body_does_not_seed() -> None:
    rail = TaskExecutionRail()
    load_ctx = _ctx(
        "skill_tool",
        tool_msg=SimpleNamespace(metadata={"skill_name": "pptx-craft"}),
        tool_result=PPTX_BODY,
    )
    load_ctx.inputs.tool_args = {"relative_file_path": "designer/SKILL.md"}
    await rail.after_tool_call(load_ctx)
    assert rail._skill_stages_owned is False
    assert rail._skill_todo_required is True


@pytest.mark.asyncio
async def test_acceleration_releases_seeded_stages(tmp_path, monkeypatch) -> None:
    rail = TaskExecutionRail()
    todo_path = tmp_path / "sess-1" / "todo.json"
    monkeypatch.setattr(
        rail, "_get_todo_workspace_path", lambda _sid: todo_path
    )
    load_ctx = _ctx(
        "skill_tool",
        tool_msg=SimpleNamespace(
            metadata={"skill_name": "pptx-craft"}, content="loaded"
        ),
        tool_result=PPTX_BODY,
    )
    await rail.after_tool_call(load_ctx)
    assert todo_path.exists()

    accel_ctx = _ctx("skill_acceleration_exec", messages=[])
    await rail.before_tool_call(accel_ctx)
    assert accel_ctx.extra.get("_skip_tool") is not True
    assert rail._skill_stages_owned is False
    assert rail._todo_map == {}
    assert not todo_path.exists()

