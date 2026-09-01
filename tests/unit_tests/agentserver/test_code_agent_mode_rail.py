# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for CodeAgentModeRail plan-mode enforcement."""

# pylint: disable=protected-access

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from openjiuwen.core.runner.callback import AbortError
from openjiuwen.core.single_agent.interrupt.exception import ToolInterruptException
from openjiuwen.core.single_agent.interrupt.response import InterruptRequest

from jiuwenswarm.agents.harness.code.rails.code_agent_mode_rail import CodeAgentModeRail


def _exit_plan_mode_ctx(*, exception: object = None, tool_result: object = "done"):
    """Build an after_tool_call context for an ``exit_plan_mode`` call."""
    return SimpleNamespace(
        session=SimpleNamespace(),
        inputs=SimpleNamespace(
            tool_name="exit_plan_mode",
            tool_call=SimpleNamespace(id="call_1"),
            tool_args={},
            tool_result=tool_result,
        ),
        extra={},
        exception=exception,
    )


def _plan_mode_agent() -> MagicMock:
    agent = MagicMock()
    agent.load_state.return_value = SimpleNamespace(
        plan_mode=SimpleNamespace(mode="plan", plan_slug="test-plan")
    )
    return agent


@pytest.mark.asyncio
async def test_before_tool_call_blocks_switch_mode_exit_in_plan_mode() -> None:
    rail = CodeAgentModeRail(allowed_tools=["switch_mode"])
    agent = MagicMock()
    plan_state = SimpleNamespace(mode="plan", plan_slug="test-plan")
    agent.load_state.return_value = SimpleNamespace(plan_mode=plan_state)
    rail._agent = agent

    parent = AsyncMock()
    with patch.object(CodeAgentModeRail.__bases__[0], "before_tool_call", parent):
        ctx = SimpleNamespace(
            session=SimpleNamespace(),
            inputs=SimpleNamespace(
                tool_name="switch_mode",
                tool_call=SimpleNamespace(
                    id="call_1",
                    arguments='{"mode": "normal"}',
                ),
                tool_args={"mode": "normal"},
            ),
            extra={},
        )
        await rail.before_tool_call(ctx)

    parent.assert_not_awaited()
    assert ctx.extra.get("_skip_tool") is True


@pytest.mark.asyncio
async def test_before_tool_call_blocks_non_git_write_in_plan_mode() -> None:
    rail = CodeAgentModeRail(allowed_tools=["bash"])
    agent = MagicMock()
    plan_state = SimpleNamespace(mode="plan", plan_slug="test-plan")
    agent.load_state.return_value = SimpleNamespace(plan_mode=plan_state)
    rail._agent = agent

    parent = AsyncMock()
    with patch.object(CodeAgentModeRail.__bases__[0], "before_tool_call", parent):
        ctx = SimpleNamespace(
            session=SimpleNamespace(),
            inputs=SimpleNamespace(
                tool_name="bash",
                tool_call=SimpleNamespace(id="call_1"),
                tool_args={"command": "mkdir -p src/foo"},
            ),
            extra={},
        )
        await rail.before_tool_call(ctx)

    parent.assert_awaited_once()
    assert ctx.extra.get("_skip_tool") is True


@pytest.mark.asyncio
async def test_after_tool_call_keeps_plan_mode_while_approval_is_pending() -> None:
    """A suspended ``exit_plan_mode`` must not exit plan mode.

    ``ToolCallResilienceRail`` writes a failure placeholder into
    ``tool_result`` when the approval interrupt is raised, so the rail has to
    recognise the pending interrupt on ``ctx.exception`` instead.
    """
    rail = CodeAgentModeRail(allowed_tools=["exit_plan_mode"])
    agent = _plan_mode_agent()
    rail._agent = agent
    rail._unregister_task_tool = MagicMock()

    interrupt = ToolInterruptException(request=InterruptRequest(message="计划审批"))
    ctx = _exit_plan_mode_ctx(
        exception=AbortError("Tool execution interrupted", cause=interrupt),
        tool_result=SimpleNamespace(success=False, data=None, error=""),
    )
    await rail.after_tool_call(ctx)

    agent.restore_mode_after_plan_exit.assert_not_called()
    rail._unregister_task_tool.assert_not_called()


@pytest.mark.asyncio
async def test_after_tool_call_restores_mode_when_tool_executed() -> None:
    """An executed ``exit_plan_mode`` that left plan mode on still restores."""
    rail = CodeAgentModeRail(allowed_tools=["exit_plan_mode"])
    agent = _plan_mode_agent()
    rail._agent = agent
    rail._unregister_task_tool = MagicMock()

    ctx = _exit_plan_mode_ctx()
    await rail.after_tool_call(ctx)

    agent.restore_mode_after_plan_exit.assert_called_once_with(ctx.session)
    rail._unregister_task_tool.assert_called_once()


@pytest.mark.asyncio
async def test_after_tool_call_keeps_plan_mode_when_user_rejected() -> None:
    rail = CodeAgentModeRail(allowed_tools=["exit_plan_mode"])
    agent = _plan_mode_agent()
    rail._agent = agent
    rail._unregister_task_tool = MagicMock()

    ctx = _exit_plan_mode_ctx()
    ctx.extra["_plan_rejected"] = True
    await rail.after_tool_call(ctx)

    agent.restore_mode_after_plan_exit.assert_not_called()
    rail._unregister_task_tool.assert_not_called()


def test_init_no_longer_patches_exit_plan_mode_invoke() -> None:
    """After removing the pending-approval pattern, CodeAgentModeRail.init()
    should NOT patch exit_plan_mode.invoke. The parent AgentModeRail's
    ExitPlanModeTool handles mode restoration directly inside invoke().
    """
    rail = CodeAgentModeRail(allowed_tools=["exit_plan_mode"])
    tool = MagicMock()
    original_invoke = object()
    tool.invoke = original_invoke
    tool.card.name = "exit_plan_mode"
    tool._language = "cn"
    rail._tools = [tool]

    agent = MagicMock()
    rail.init(agent)

    # Verify the tool's invoke was NOT replaced
    assert tool.invoke is original_invoke
