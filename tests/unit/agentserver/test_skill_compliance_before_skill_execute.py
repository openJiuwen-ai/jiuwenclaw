"""Unit tests for SkillComplianceRail.before_tool_call BEFORE_SKILL_EXECUTE trigger."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jiuwenclaw.agentserver.deep_agent.rails.skill_compliance_rail import (
    SkillComplianceRail,
    _sessions,
)
from jiuwenclaw.schema.hook_event import AgentServerHookEvents
from jiuwenclaw.schema.hooks_context import BeforeSkillExecuteHookContext


@pytest.fixture(autouse=True)
def _clean_state():
    _sessions.clear()
    yield
    _sessions.clear()


def _mk_skill(name, directory="/tmp/skills/test-skill"):
    """Create a Skill-like object (matches openjiuwen Skill attributes)."""
    return SimpleNamespace(name=name, directory=directory)


def _mk_tool_call_inputs(tool_name, tool_args=None):
    """Create ToolCallInputs-shaped object."""
    return SimpleNamespace(
        tool_call=MagicMock(),
        tool_name=tool_name,
        tool_args=tool_args or {},
        tool_result=None,
        tool_msg=None,
    )


def _mk_ctx(agent=None, session=None, inputs=None):
    """Create AgentCallbackContext-shaped object."""
    return SimpleNamespace(
        agent=agent or SimpleNamespace(),
        session=session,
        context=SimpleNamespace(),
        inputs=inputs,
        extra={},
    )


@pytest.mark.asyncio
async def test_before_tool_call_triggers_event_for_skill_tool():
    """When tool_name is 'skill_tool', trigger BEFORE_SKILL_EXECUTE with skill context."""
    def resolve_demo_skill():
        return [_mk_skill("demo", directory="/tmp/skills/demo")]

    rail = SkillComplianceRail(skill_dir_resolver=resolve_demo_skill)

    inputs = _mk_tool_call_inputs("skill_tool", {"skill_name": "demo"})
    ctx = _mk_ctx(inputs=inputs, session=MagicMock())

    hook_ctx = None

    async def capture_handler(event, received_ctx):
        nonlocal hook_ctx
        hook_ctx = received_ctx

    with patch("jiuwenclaw.extensions.registry.ExtensionRegistry") as mock_registry:
        mock_instance = AsyncMock()
        mock_registry.get_instance.return_value = mock_instance
        mock_instance.trigger = AsyncMock(side_effect=capture_handler)

        await rail.before_tool_call(ctx)

    assert hook_ctx is not None
    assert hook_ctx.skill_name == "demo"
    assert hook_ctx.skill_dir == "/tmp/skills/demo"
    mock_instance.trigger.assert_called_once_with(
        AgentServerHookEvents.BEFORE_SKILL_EXECUTE, hook_ctx
    )


@pytest.mark.asyncio
async def test_before_tool_call_skips_non_skill_tool():
    """When tool_name is NOT 'skill_tool', no hook event is triggered."""
    rail = SkillComplianceRail()

    inputs = _mk_tool_call_inputs("bash", {"command": "ls"})
    ctx = _mk_ctx(inputs=inputs)

    with patch("jiuwenclaw.extensions.registry.ExtensionRegistry") as mock_registry:
        mock_instance = AsyncMock()
        mock_registry.get_instance.return_value = mock_instance

        await rail.before_tool_call(ctx)

    mock_instance.trigger.assert_not_called()


@pytest.mark.asyncio
async def test_before_tool_call_skips_when_skill_name_missing():
    """When skill_tool has no skill_name in args, no hook event is triggered."""
    rail = SkillComplianceRail()

    inputs = _mk_tool_call_inputs("skill_tool", {})
    ctx = _mk_ctx(inputs=inputs)

    with patch("jiuwenclaw.extensions.registry.ExtensionRegistry") as mock_registry:
        mock_instance = AsyncMock()
        mock_registry.get_instance.return_value = mock_instance

        await rail.before_tool_call(ctx)

    mock_instance.trigger.assert_not_called()


@pytest.mark.asyncio
async def test_before_tool_call_skips_when_resolver_returns_none():
    """When skill_dir_resolver cannot find the skill, no hook event is triggered."""
    def resolve_none():
        return None

    rail = SkillComplianceRail(skill_dir_resolver=resolve_none)

    inputs = _mk_tool_call_inputs("skill_tool", {"skill_name": "unknown"})
    ctx = _mk_ctx(inputs=inputs)

    with patch("jiuwenclaw.extensions.registry.ExtensionRegistry") as mock_registry:
        mock_instance = AsyncMock()
        mock_registry.get_instance.return_value = mock_instance

        await rail.before_tool_call(ctx)

    mock_instance.trigger.assert_not_called()


@pytest.mark.asyncio
async def test_before_tool_call_logs_handler_exception_but_does_not_propagate():
    """Handler exception is caught and logged; skill_tool execution is not blocked."""
    def resolve_demo_skill():
        return [_mk_skill("demo", "/tmp/skills/demo")]

    rail = SkillComplianceRail(skill_dir_resolver=resolve_demo_skill)

    inputs = _mk_tool_call_inputs("skill_tool", {"skill_name": "demo"})
    ctx = _mk_ctx(inputs=inputs)

    with patch("jiuwenclaw.extensions.registry.ExtensionRegistry") as mock_registry:
        mock_instance = AsyncMock()
        mock_registry.get_instance.return_value = mock_instance
        mock_instance.trigger = AsyncMock(side_effect=RuntimeError("download failed"))

        with patch("jiuwenclaw.agentserver.deep_agent.rails.skill_compliance_rail.logger") as mock_logger:
            await rail.before_tool_call(ctx)
            mock_logger.warning.assert_called()

    # No exception propagated — the method completed normally


@pytest.mark.asyncio
async def test_before_tool_call_skips_when_resolver_not_set():
    """When no skill_dir_resolver is configured, no hook event is triggered."""
    rail = SkillComplianceRail()
    # _skill_dir_resolver is None by default

    inputs = _mk_tool_call_inputs("skill_tool", {"skill_name": "demo"})
    ctx = _mk_ctx(inputs=inputs)

    with patch("jiuwenclaw.extensions.registry.ExtensionRegistry") as mock_registry:
        mock_instance = AsyncMock()
        mock_registry.get_instance.return_value = mock_instance

        await rail.before_tool_call(ctx)

    mock_instance.trigger.assert_not_called()
