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

    def _request_force_finish(result):
        ctx._force_finish_request = result

    ctx = SimpleNamespace(
        inputs=inputs,
        extra={},
        session=None,
        _force_finish_request=None,
        request_force_finish=_request_force_finish,
    )
    return ctx


@pytest.mark.asyncio
async def test_build_agent_dropout_rail_returns_none_when_disabled():
    rail = build_agent_dropout_rail(config={"enabled": False})
    assert rail is None


def test_provider_wires_auditor_llm(monkeypatch):
    """Factory must pass a live auditor LLM (not None) when model config is present."""
    from jiuwenswarm.agents.swarm.providers import member_rails as mr

    class _FakeModel:
        async def invoke(self, prompt: str):
            return SimpleNamespace(content=_correct_json())

    monkeypatch.setattr(
        "jiuwenswarm.agents.swarm.providers.evolution_rails._build_evolution_llm_from",
        lambda _cfg: (_FakeModel(), "test-model"),
    )

    ctx = SimpleNamespace(
        config={
            "team_pruning": {
                "enabled": True,
                "strategy": "agent_dropout",
                "strategies": {"agent_dropout": {"enabled": True}},
            },
            "agent_dropout": {"enabled": True},
        },
        member_name="auditor-test",
        role="teammate",
    )
    rail = mr._build_agent_dropout_rail(
        {
            "agent_dropout_config": {"enabled": True, "max_rectify_attempts": 1},
            "auditor_model_config": {
                "model_client_config": {
                    "client_provider": "OpenAI",
                    "api_key": "sk-test",
                    "api_base": "https://example.invalid/v1",
                    "model_name": "test-model",
                },
                "model_config_obj": {},
                "model_name": "test-model",
            },
            "active_members": 3,
        },
        ctx,
    )
    assert rail is not None
    assert rail._service.auditor._llm is not None


@pytest.mark.asyncio
async def test_provider_auditor_llm_invokes_model(monkeypatch):
    from jiuwenswarm.agents.swarm.providers import member_rails as mr

    calls: list[str] = []

    class _FakeModel:
        async def invoke(self, prompt: str):
            calls.append(prompt)
            return SimpleNamespace(content="ok")

    llm = mr._build_auditor_llm(
        {
            "model_client_config": {"model_name": "m"},
            "model_config_obj": {},
            "model_name": "m",
        }
    )
    # Without monkeypatch the real builder may fail; patch then rebuild.
    monkeypatch.setattr(
        "jiuwenswarm.agents.swarm.providers.evolution_rails._build_evolution_llm_from",
        lambda _cfg: (_FakeModel(), "m"),
    )
    llm = mr._build_auditor_llm(
        {
            "model_client_config": {"model_name": "m"},
            "model_config_obj": {},
            "model_name": "m",
        }
    )
    assert llm is not None
    assert await llm("audit me") == "ok"
    assert calls == ["audit me"]


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
    assert "shutdown_member" in result_text or "dropped" in result_text.lower()
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
async def test_rail_audits_json_string_arguments_and_emits_check_notices():
    """SDK ToolCall.arguments is a JSON string — must still audit and show checks."""

    async def llm(_prompt: str) -> str:
        return _correct_json()

    cfg = AgentDropoutConfig(enabled=True, max_rectify_attempts=2)
    service = AgentDropoutService(config=cfg, llm=llm)
    rail = AgentDropoutRail(service=service, member_name="writer", active_members=3)

    class _Session:
        def __init__(self) -> None:
            self.written = []

        async def write_stream(self, schema):
            self.written.append(schema)

    ctx = _make_ctx(content="Water boils at 100°C at sea level.")
    ctx.inputs.tool_call.arguments = json.dumps(
        {"content": "Water boils at 100°C at sea level."}
    )
    ctx.session = _Session()
    await rail.before_tool_call(ctx)

    assert ctx.extra.get("_skip_tool") is not True
    notice_types = [
        (getattr(s, "payload", {}) or {}).get("notice_type")
        for s in ctx.session.written
        if getattr(s, "type", None) == "notice"
    ]
    assert "agent_dropout_check" in notice_types
    assert "agent_dropout_pass" in notice_types
    assert any(getattr(s, "type", None) == "llm_reasoning" for s in ctx.session.written)
    assert any(getattr(s, "type", None) == "tool_call" for s in ctx.session.written)
    assert any(getattr(s, "type", None) == "tool_result" for s in ctx.session.written)


@pytest.mark.asyncio
async def test_extract_share_content_from_json_arguments():
    from jiuwenswarm.agents.harness.team.rails.agent_dropout_rail import (
        AgentDropoutRail,
    )

    tool_call = SimpleNamespace(
        id="team.send_message",
        name="send_message",
        arguments='{"content":"hello team","to":"all"}',
    )
    assert AgentDropoutRail._extract_share_content(tool_call) == "hello team"


@pytest.mark.asyncio
async def test_rail_drop_emits_notice_and_shutdown_and_force_finish():
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

    class _Session:
        def __init__(self) -> None:
            self.written = []

        async def write_stream(self, schema):
            self.written.append(schema)

    session = _Session()
    ctx = _make_ctx()
    ctx.session = session
    await rail.before_tool_call(ctx)

    assert any(getattr(s, "type", None) == "notice" for s in session.written)
    assert any(
        getattr(s, "type", None) == "team.member"
        and isinstance(getattr(s, "payload", None), dict)
        and s.payload.get("type") == "team.member.shutdown"
        for s in session.written
    )
    assert ctx._force_finish_request is not None
    assert service.tracker.is_dropped("bad-actor") is True


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
