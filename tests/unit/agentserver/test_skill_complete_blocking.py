# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for skill_complete blocking when todos are incomplete."""
# pylint: disable=protected-access
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from openjiuwen.core.single_agent.rail.base import ToolCallInputs

from jiuwenclaw.agentserver.deep_agent.rails.task_execution_rail import TaskExecutionRail
from jiuwenclaw.agentserver.deep_agent.rails.skill_compliance_rail import (
    SkillComplianceRail,
    _sessions,
)


@pytest.fixture(autouse=True)
def _clean_skill_state():
    _sessions.clear()
    yield
    _sessions.clear()


def _mk_tool_call(name, tool_id="test_id_123"):
    tc = MagicMock()
    tc.name = name
    tc.id = tool_id
    tc.arguments = {}
    return tc


def _mk_ctx_with_tool_call(tool_name, tool_id="test_id_123"):
    tc = _mk_tool_call(tool_name, tool_id)
    inputs = ToolCallInputs(
        tool_call=tc,
        tool_name=tool_name,
        tool_args={},
        tool_result=None,
        tool_msg=None,
    )
    ctx = SimpleNamespace(
        inputs=inputs,
        session=None,
        extra={},
    )
    return ctx


class TestHasIncompleteTodos:
    """Tests for _has_incomplete_todos static method."""

    @staticmethod
    def test_empty_todo_map_returns_false():
        """空 todo_map 应该返回 False（没有未完成任务）"""
        assert TaskExecutionRail._has_incomplete_todos({}) is False

    @staticmethod
    def test_all_completed_returns_false():
        """所有任务都是 completed 状态应该返回 False"""
        todo_map = {
            "task1": {"status": "completed"},
            "task2": {"status": "completed"},
        }
        assert TaskExecutionRail._has_incomplete_todos(todo_map) is False

    @staticmethod
    def test_all_cancelled_returns_false():
        """所有任务都是 cancelled 状态应该返回 False"""
        todo_map = {
            "task1": {"status": "cancelled"},
            "task2": {"status": "cancelled"},
        }
        assert TaskExecutionRail._has_incomplete_todos(todo_map) is False

    @staticmethod
    def test_mixed_completed_cancelled_returns_false():
        """completed 和 cancelled 混合应该返回 False"""
        todo_map = {
            "task1": {"status": "completed"},
            "task2": {"status": "cancelled"},
        }
        assert TaskExecutionRail._has_incomplete_todos(todo_map) is False

    @staticmethod
    def test_pending_status_returns_true():
        """pending 状态应该返回 True（有未完成任务）"""
        todo_map = {"task1": {"status": "pending"}}
        assert TaskExecutionRail._has_incomplete_todos(todo_map) is True

    @staticmethod
    def test_in_progress_status_returns_true():
        """in_progress 状态应该返回 True"""
        todo_map = {"task1": {"status": "in_progress"}}
        assert TaskExecutionRail._has_incomplete_todos(todo_map) is True

    @staticmethod
    def test_waiting_status_returns_true():
        """waiting 状态应该返回 True"""
        todo_map = {"task1": {"status": "waiting"}}
        assert TaskExecutionRail._has_incomplete_todos(todo_map) is True

    @staticmethod
    def test_mixed_incomplete_returns_true():
        """多个未完成状态混合应该返回 True"""
        todo_map = {
            "task1": {"status": "pending"},
            "task2": {"status": "in_progress"},
            "task3": {"status": "completed"},
        }
        assert TaskExecutionRail._has_incomplete_todos(todo_map) is True

    @staticmethod
    def test_no_status_field_returns_true():
        """没有 status 字段默认视为 pending 应该返回 True"""
        todo_map = {"task1": {}}
        assert TaskExecutionRail._has_incomplete_todos(todo_map) is True

    @staticmethod
    def test_case_insensitive_status_check():
        """状态检查应该大小写不敏感"""
        todo_map = {"task1": {"status": "COMPLETED"}}
        assert TaskExecutionRail._has_incomplete_todos(todo_map) is False

        todo_map = {"task1": {"status": "PENDING"}}
        assert TaskExecutionRail._has_incomplete_todos(todo_map) is True


class TestSkillCompleteBlocking:
    """Tests for skill_complete blocking logic in before_tool_call."""

    @pytest.mark.asyncio
    @staticmethod
    async def test_blocks_skill_complete_with_incomplete_todos():
        """有未完成任务时调用 skill_complete 应该被阻止"""
        rail = TaskExecutionRail()
        rail._todo_map = {"task1": {"status": "pending"}}

        ctx = _mk_ctx_with_tool_call("skill_complete")
        await rail.before_tool_call(ctx)

        assert ctx.extra.get("_skip_tool") is True
        assert "SKILL_COMPLETE_BLOCKED" in str(ctx.inputs.tool_result)
        assert ctx.inputs.tool_msg is not None
        assert "SKILL_COMPLETE_BLOCKED" in ctx.inputs.tool_msg.content

    @pytest.mark.asyncio
    @staticmethod
    async def test_allows_skill_complete_with_all_completed():
        """所有任务完成时调用 skill_complete 应该允许执行"""
        rail = TaskExecutionRail()
        rail._todo_map = {
            "task1": {"status": "completed"},
            "task2": {"status": "cancelled"},
        }

        ctx = _mk_ctx_with_tool_call("skill_complete")
        await rail.before_tool_call(ctx)

        assert "_skip_tool" not in ctx.extra
        assert ctx.inputs.tool_result is None

    @pytest.mark.asyncio
    @staticmethod
    async def test_allows_skill_complete_with_empty_todo_map():
        """todo_map 为空时调用 skill_complete 应该允许执行"""
        rail = TaskExecutionRail()
        rail._todo_map = {}

        ctx = _mk_ctx_with_tool_call("skill_complete")
        await rail.before_tool_call(ctx)

        assert "_skip_tool" not in ctx.extra
        assert ctx.inputs.tool_result is None

    @pytest.mark.asyncio
    @staticmethod
    async def test_does_not_affect_other_tools():
        """阻止逻辑不应该影响其他工具"""
        rail = TaskExecutionRail()
        rail._todo_map = {"task1": {"status": "pending"}}

        ctx = _mk_ctx_with_tool_call("todo_modify")
        await rail.before_tool_call(ctx)

        assert "_skip_tool" not in ctx.extra
        assert ctx.inputs.tool_result is None

    @pytest.mark.asyncio
    @staticmethod
    async def test_tool_call_id_propagated_to_tool_msg():
        """tool_call_id 应该正确传递到 ToolMessage"""
        rail = TaskExecutionRail()
        rail._todo_map = {"task1": {"status": "pending"}}

        expected_id = "test_tool_call_id_456"
        ctx = _mk_ctx_with_tool_call("skill_complete", expected_id)
        await rail.before_tool_call(ctx)

        assert ctx.inputs.tool_msg is not None
        assert ctx.inputs.tool_msg.tool_call_id == expected_id


class TestSkillComplianceRailEarlyReturn:
    """Tests for early return on SKILL_COMPLETE_BLOCKED in SkillComplianceRail."""

    @pytest.mark.asyncio
    @staticmethod
    async def test_early_return_on_skill_complete_blocked():
        """当 skill_complete 返回 SKILL_COMPLETE_BLOCKED 时应该提前返回"""
        rail = SkillComplianceRail()

        ctx = _mk_ctx_with_tool_call("skill_complete")
        ctx.inputs.tool_msg = SimpleNamespace(
            content="[SKILL_COMPLETE_BLOCKED] todo.json 中仍有未完成任务",
        )

        original_handle_tool_event = rail._handle_tool_event
        called = False

        def mock_handle(*args, **kwargs):
            nonlocal called
            called = True

        rail._handle_tool_event = mock_handle

        await rail.before_tool_call(ctx)

        assert called is False

        rail._handle_tool_event = original_handle_tool_event

    @pytest.mark.asyncio
    @staticmethod
    async def test_normal_execution_without_blocked_message():
        """没有 SKILL_COMPLETE_BLOCKED 消息时应该正常执行"""
        rail = SkillComplianceRail()

        ctx = _mk_ctx_with_tool_call("skill_complete")
        ctx.inputs.tool_msg = SimpleNamespace(content="normal completion message")

        original_handle_tool_event = rail._handle_tool_event
        called = False

        def mock_handle(*args, **kwargs):
            nonlocal called
            called = True

        rail._handle_tool_event = mock_handle

        await rail.before_tool_call(ctx)

        rail._handle_tool_event = original_handle_tool_event

    @pytest.mark.asyncio
    @staticmethod
    async def test_other_tools_not_affected():
        """其他工具不应该受到提前返回逻辑的影响"""
        rail = SkillComplianceRail()

        ctx = _mk_ctx_with_tool_call("todo_modify")
        ctx.inputs.tool_msg = SimpleNamespace(
            content="[SKILL_COMPLETE_BLOCKED] should not affect"
        )

        original_handle_tool_event = rail._handle_tool_event
        called = False

        def mock_handle(*args, **kwargs):
            nonlocal called
            called = True

        rail._handle_tool_event = mock_handle

        await rail.before_tool_call(ctx)

        rail._handle_tool_event = original_handle_tool_event
