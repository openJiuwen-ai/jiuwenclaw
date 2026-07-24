# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for AgentDropoutRail and provider factory."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from jiuwenswarm.agents.dropout import AgentDropoutConfig, AgentDropoutService, ContributionAction
from jiuwenswarm.agents.dropout.prompts import DROP_SIGNAL_PREFIX
from jiuwenswarm.agents.harness.team.rails.agent_dropout_rail import (
    AgentDropoutRail,
    build_agent_dropout_rail,
)


def _flawed_json() -> str:
    return json.dumps(
        {
            "evidence_quote": "lie",
            "analysis": "Incorrect information",
            "suggestion": "Correct the claim",
            "impact_assessment": "YES",
            "is_flawed": True,
        }
    )


def _correct_json() -> str:
    return json.dumps(
        {
            "evidence_quote": "N/A",
            "analysis": "N/A",
            "suggestion": "N/A",
            "impact_assessment": "NO",
            "is_flawed": False,
        }
    )


def _make_ctx(*, tool_name: str = "send_message", content: str = "bad content", tool_id: str = "team.send_message"):
    tool_call = SimpleNamespace(
        id=tool_id,
        name=tool_name,
        arguments={"content": content},
    )
    inputs = SimpleNamespace(
        tool_name=tool_name,
        tool_call=tool_call,
        tool_result=None,
        tool_msg=None,
        query="Solve the shared task",
    )
    return SimpleNamespace(inputs=inputs, extra={})


@pytest.mark.asyncio
async def test_build_agent_dropout_rail_returns_none_when_disabled():
    rail = build_agent_dropout_rail(config={"enabled": False})
    assert rail is None


@pytest.mark.asyncio
async def test_rail_rectify_rejects_tool_with_feedback():
    async def llm(_prompt: str) -> str:
        return _flawed_json()

    cfg = AgentDropoutConfig(
        enabled=True,
        max_rectify_attempts=2,
        pass_rate=1.0,
        drop_after_failures=5,
        min_active_members=1,
    )
    service = AgentDropoutService(config=cfg, llm=llm)
    rail = AgentDropoutRail(
        service=service,
        member_name="teammate-1",
        role="teammate",
        active_members=3,
    )
    ctx = _make_ctx(content="The answer is fabricated.")
    await rail.before_tool_call(ctx)
    assert ctx.extra.get("_skip_tool") is True
    assert DROP_SIGNAL_PREFIX in str(ctx.inputs.tool_result)
    assert "pending correction" in str(ctx.inputs.tool_result).lower() or "Attempt" in str(
        ctx.inputs.tool_result
    )
    assert service.get_pending_feedback("teammate-1") is not None


@pytest.mark.asyncio
async def test_rail_reject_after_max_rectify_attempts():
    async def llm(_prompt: str) -> str:
        return _flawed_json()

    cfg = AgentDropoutConfig(
        enabled=True,
        max_rectify_attempts=1,
        pass_rate=1.0,
        drop_after_failures=5,
        min_active_members=1,
    )
    service = AgentDropoutService(config=cfg, llm=llm)
    rail = AgentDropoutRail(
        service=service,
        member_name="teammate-1",
        active_members=3,
    )
    ctx = _make_ctx()
    await rail.before_tool_call(ctx)
    assert ctx.extra.get("_skip_tool") is True
    assert "rejected" in str(ctx.inputs.tool_result).lower()
    assert service.scoreboard.is_pruned(
        list(service.scoreboard.dump().keys())[0]
    )


@pytest.mark.asyncio
async def test_rail_drop_signal_when_threshold_met():
    async def llm(_prompt: str) -> str:
        return _flawed_json()

    cfg = AgentDropoutConfig(
        enabled=True,
        max_rectify_attempts=1,
        pass_rate=1.0,
        drop_after_failures=1,
        min_active_members=1,
    )
    service = AgentDropoutService(config=cfg, llm=llm)
    rail = AgentDropoutRail(
        service=service,
        member_name="bad-actor",
        active_members=3,
    )
    ctx = _make_ctx()
    await rail.before_tool_call(ctx)
    result_text = str(ctx.inputs.tool_result)
    assert DROP_SIGNAL_PREFIX in result_text
    assert "shutdown_member" in result_text
    assert service.tracker.is_dropped("bad-actor") is True


@pytest.mark.asyncio
async def test_rail_no_drop_on_team_collapse_fallback():
    async def llm(_prompt: str) -> str:
        return _flawed_json()

    cfg = AgentDropoutConfig(
        enabled=True,
        max_rectify_attempts=1,
        pass_rate=1.0,
        drop_after_failures=1,
        min_active_members=2,
    )
    service = AgentDropoutService(config=cfg, llm=llm)
    rail = AgentDropoutRail(
        service=service,
        member_name="lonely",
        active_members=2,  # remaining would be 1 < 2
    )
    ctx = _make_ctx()
    await rail.before_tool_call(ctx)
    result_text = str(ctx.inputs.tool_result)
    assert "shutdown_member" not in result_text
    assert "rejected" in result_text.lower()
    assert service.tracker.is_dropped("lonely") is False


@pytest.mark.asyncio
async def test_rail_passes_clean_contribution():
    async def llm(_prompt: str) -> str:
        return _correct_json()

    cfg = AgentDropoutConfig(enabled=True, max_rectify_attempts=2)
    service = AgentDropoutService(config=cfg, llm=llm)
    rail = AgentDropoutRail(service=service, member_name="good", active_members=3)
    ctx = _make_ctx(content="Here is a verified progress update.")
    await rail.before_tool_call(ctx)
    assert ctx.extra.get("_skip_tool") is not True
    assert ctx.inputs.tool_result is None


@pytest.mark.asyncio
async def test_service_evaluate_contribution_actions():
    async def llm(_prompt: str) -> str:
        return _flawed_json()

    cfg = AgentDropoutConfig(
        enabled=True,
        max_rectify_attempts=2,
        drop_after_failures=2,
        min_active_members=1,
    )
    service = AgentDropoutService(config=cfg, llm=llm)
    first = await service.evaluate_contribution(
        task="t",
        content="bad",
        member_name="m1",
        active_members=3,
    )
    assert first.action == ContributionAction.RECTIFY
    second = await service.evaluate_contribution(
        task="t",
        content="still bad",
        member_name="m1",
        active_members=3,
    )
    assert second.action == ContributionAction.REJECT
