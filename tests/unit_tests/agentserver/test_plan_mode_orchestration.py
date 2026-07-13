# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""End-to-end orchestration tests for plan mode — aligned with Claude Code.

Plan approval is now handled by ``PlanApprovalInterruptRail`` which intercepts
``exit_plan_mode`` with an immediate approval dialog.  Mode restoration happens
inside ``ExitPlanModeTool.invoke()`` via ``restore_mode_after_plan_exit()``.
The server-side pending-approval gate has been removed.
"""

# pylint: disable=protected-access

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jiuwenswarm.common.schema.agent import AgentRequest
from jiuwenswarm.common.schema.message import ReqMethod
from jiuwenswarm.server import agent_ws_server as agent_ws_server_module
from jiuwenswarm.server.agent_ws_server import AgentWebSocketServer


def _chat_request(
    session_id: str,
    query: str = "hello",
    *,
    mode: str = "code.plan",
    extra_params: dict | None = None,
) -> AgentRequest:
    params: dict = {"query": query, "mode": mode}
    if extra_params:
        params.update(extra_params)
    return AgentRequest(
        request_id="req_flow",
        channel_id="tui",
        session_id=session_id,
        req_method=ReqMethod.CHAT_SEND,
        params=params,
    )


@pytest.mark.asyncio
async def test_prepare_code_mode_chat_turn_resolves_mode_and_agent() -> None:
    """_prepare_code_mode_chat_turn resolves mode/sub_mode and gets the agent
    without plan-approval side effects.
    """
    session_id = "sess_basic"

    agent = MagicMock()
    manager = MagicMock()
    manager.get_agent = AsyncMock(return_value=agent)

    server = AgentWebSocketServer.__new__(AgentWebSocketServer)
    server._agent_manager = manager
    server._resolve_code_language = MagicMock(return_value="cn")

    request = _chat_request(session_id, "hello", mode="code.plan")

    mode, sub_mode, resolved_agent = await server._prepare_code_mode_chat_turn(
        request, "tui"
    )

    assert mode == "code"
    assert sub_mode == "plan"
    assert resolved_agent is agent
    manager.get_agent.assert_awaited_once()


@pytest.mark.asyncio
async def test_ensure_code_mode_state_syncs_plan_to_normal() -> None:
    """_ensure_code_mode_state syncs plan→normal when modes differ."""
    session_id = "sess_sync"

    plan_agent = MagicMock()
    plan_instance = MagicMock()
    plan_agent.get_instance.return_value = plan_instance
    plan_instance.card = SimpleNamespace(id="code-agent")
    plan_state = SimpleNamespace(mode="plan", plan_slug="test")
    plan_instance.load_state.return_value = SimpleNamespace(plan_mode=plan_state)

    session = MagicMock()
    create_session = MagicMock(return_value=session)
    pre_run = AsyncMock()
    post_run = AsyncMock()

    server = AgentWebSocketServer.__new__(AgentWebSocketServer)
    request = _chat_request(session_id, "hello", mode="code.normal")

    with patch(
        "openjiuwen.core.single_agent.create_agent_session",
        create_session,
    ):
        session.pre_run = pre_run
        session.post_run = post_run
        restored = await server._ensure_code_mode_state(
            request, "code", "normal", plan_agent
        )

    assert restored is True
    plan_instance.switch_mode.assert_called_once_with(session=session, mode="normal")


@pytest.mark.asyncio
async def test_ensure_code_mode_state_skips_if_mode_already_matches() -> None:
    """_ensure_code_mode_state does nothing when plan_mode already matches."""
    session_id = "sess_skip"

    plan_agent = MagicMock()
    plan_instance = MagicMock()
    plan_agent.get_instance.return_value = plan_instance
    plan_instance.card = SimpleNamespace(id="code-agent")
    plan_state = SimpleNamespace(mode="plan", plan_slug="test")
    plan_instance.load_state.return_value = SimpleNamespace(plan_mode=plan_state)

    session = MagicMock()
    create_session = MagicMock(return_value=session)

    server = AgentWebSocketServer.__new__(AgentWebSocketServer)
    request = _chat_request(session_id, "hello", mode="code.plan")

    with patch(
        "openjiuwen.core.single_agent.create_agent_session",
        create_session,
    ):
        session.pre_run = AsyncMock()
        session.post_run = AsyncMock()
        restored = await server._ensure_code_mode_state(
            request, "code", "plan", plan_agent
        )

    assert restored is False
    plan_instance.switch_mode.assert_not_called()


@pytest.mark.asyncio
async def test_ensure_code_mode_state_allows_explicit_plan_reentry_after_exit() -> None:
    """A user-triggered /plan re-entry must not be blocked by stale exit guards."""
    session_id = "sess_explicit_reentry"

    plan_agent = MagicMock()
    plan_instance = MagicMock()
    plan_agent.get_instance.return_value = plan_instance
    plan_instance.card = SimpleNamespace(id="code-agent")
    plan_state = SimpleNamespace(mode="normal", plan_slug="old-plan")
    plan_instance.load_state.return_value = SimpleNamespace(plan_mode=plan_state)

    session = MagicMock()
    create_session = MagicMock(return_value=session)

    server = AgentWebSocketServer.__new__(AgentWebSocketServer)
    server._push_plan_mode_exited = AsyncMock()
    request = _chat_request(
        session_id,
        "implement this in plan mode",
        mode="code.plan",
        extra_params={"plan_entry_source": "slash_command"},
    )

    agent_ws_server_module._plan_exited_sessions.add(session_id)
    try:
        with patch(
            "openjiuwen.core.single_agent.create_agent_session",
            create_session,
        ):
            session.pre_run = AsyncMock()
            session.post_run = AsyncMock()
            restored = await server._ensure_code_mode_state(
                request, "code", "plan", plan_agent
            )
    finally:
        agent_ws_server_module._plan_exited_sessions.discard(session_id)

    assert restored is False
    plan_instance.switch_mode.assert_called_once_with(session=session, mode="plan")
    server._push_plan_mode_exited.assert_not_awaited()
    assert request.params["mode"] == "code.plan"


@pytest.mark.asyncio
async def test_ensure_skips_for_team_sub_mode() -> None:
    """_ensure_code_mode_state returns False for team sub_mode."""
    agent = MagicMock()
    server = AgentWebSocketServer.__new__(AgentWebSocketServer)
    request = _chat_request("sess_team", mode="code.team")

    restored = await server._ensure_code_mode_state(request, "code", "team", agent)
    assert restored is False


@pytest.mark.asyncio
async def test_prepare_chat_turn_skips_approval_for_interrupt_resume() -> None:
    """_prepare_code_mode_chat_turn works correctly for interrupt resume requests."""
    session_id = "sess_interrupt"

    agent = MagicMock()
    manager = MagicMock()
    manager.get_agent = AsyncMock(return_value=agent)

    server = AgentWebSocketServer.__new__(AgentWebSocketServer)
    server._agent_manager = manager
    server._resolve_code_language = MagicMock(return_value="cn")

    request = _chat_request(
        session_id,
        "",
        mode="code.plan",
        extra_params={
            "request_id": "tool_req_1",
            "answers": {"approved": True},
            "source": "confirm_interrupt",
        },
    )

    mode, sub_mode, _agent = await server._prepare_code_mode_chat_turn(request, "tui")

    assert mode == "code"
    assert sub_mode == "plan"
    manager.get_agent.assert_awaited_once()


@pytest.mark.asyncio
async def test_ensure_skips_for_agent_mode() -> None:
    """_ensure_code_mode_state returns False for non-code modes."""
    agent = MagicMock()
    server = AgentWebSocketServer.__new__(AgentWebSocketServer)
    request = _chat_request("sess_agent", mode="agent.fast")

    restored = await server._ensure_code_mode_state(request, "agent", "fast", agent)
    assert restored is False
