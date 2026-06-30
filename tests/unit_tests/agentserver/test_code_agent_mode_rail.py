# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for CodeAgentModeRail plan-mode enforcement."""

# pylint: disable=protected-access

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jiuwenswarm.agents.harness.code.rails.code_agent_mode_rail import CodeAgentModeRail


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
