# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for PlanApprovalRail deferred plan-mode exit."""

# pylint: disable=protected-access

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from jiuwenswarm.agents.harness.code.rails.code_agent_mode_rail import CodeAgentModeRail
from jiuwenswarm.agents.harness.code.rails.code_plan_approval_rail import PlanApprovalRail


@pytest.mark.asyncio
async def test_exit_plan_mode_appends_pending_marker_without_switching_mode() -> None:
    rail = PlanApprovalRail()
    agent = MagicMock()
    plan_state = SimpleNamespace(mode="plan", plan_slug="test-plan")
    agent.load_state.return_value = SimpleNamespace(plan_mode=plan_state)
    agent.get_plan_file_path.return_value = None

    ctx = SimpleNamespace(
        session=SimpleNamespace(),
        inputs=SimpleNamespace(
            tool_name="exit_plan_mode",
            tool_result={"ok": True},
            tool_msg=SimpleNamespace(
                content="Plan submitted for your review.\n\n## Plan:\nstep 1",
            ),
        ),
        extra={},
    )

    rail._agent = agent
    await rail.after_tool_call(ctx)

    agent.switch_mode.assert_not_called()
    assert getattr(agent, "_plan_approval_state").pending is True
    assert "仍在规划模式" in ctx.inputs.tool_msg.content


@pytest.mark.asyncio
async def test_code_agent_mode_rail_skips_exit_after_tool_call() -> None:
    rail = CodeAgentModeRail(allowed_tools=["exit_plan_mode"])
    rail._agent = MagicMock()
    parent = AsyncMock()
    rail.__class__.__bases__[0].after_tool_call = parent

    ctx = SimpleNamespace(
        inputs=SimpleNamespace(
            tool_name="exit_plan_mode",
            tool_result={"ok": True},
        ),
        extra={},
    )

    await rail.after_tool_call(ctx)
    parent.assert_not_called()
