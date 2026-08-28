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
    assert not load_ctx.session.events

    work_ctx = _ctx("bash", messages=[])
    await rail.before_tool_call(work_ctx)
    assert work_ctx.extra.get("_skip_tool") is True
    assert "SKILL_TODO_REQUIRED" in str(work_ctx.inputs.tool_result)


@pytest.mark.asyncio
async def test_skill_tool_does_not_seed_todos_from_headings() -> None:
    rail = TaskExecutionRail()
    load_ctx = _ctx(
        "skill_tool",
        tool_msg=SimpleNamespace(
            metadata={"skill_name": "pptx-craft"},
            content="loaded",
        ),
        tool_result={
            "success": True,
            "data": {
                "skill_content": (
                    "## 阶段 1：需求澄清 & 环境检测\n"
                    "## 阶段 2：内容设计\n"
                )
            },
        },
    )
    await rail.after_tool_call(load_ctx)
    assert rail._skill_todo_required is True
    assert rail._todo_map == {}
    assert not load_ctx.session.events

    create_ctx = _ctx("todo_create")
    await rail.before_tool_call(create_ctx)
    assert create_ctx.extra.get("_skip_tool") is not True


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
