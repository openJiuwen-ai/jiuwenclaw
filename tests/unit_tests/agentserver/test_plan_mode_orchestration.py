# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""End-to-end orchestration tests for plan mode approval and exit flows."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jiuwenswarm.agents.harness.code.prompt.plan_approval import PLAN_USER_APPROVED_FLAG
from jiuwenswarm.common.schema.agent import AgentRequest
from jiuwenswarm.common.schema.message import ReqMethod
from jiuwenswarm.server.agent_ws_server import (
    AgentWebSocketServer,
    _check_and_handle_pending_approval,
    _pending_plan_approvals,
    _plan_approved_sessions,
    _try_handle_direct_plan_implement,
)


def _chat_request(
    session_id: str,
    query: str,
    *,
    mode: str = "code.plan",
    req_method: ReqMethod = ReqMethod.CHAT_SEND,
    extra_params: dict | None = None,
) -> AgentRequest:
    params: dict = {"query": query, "mode": mode}
    if extra_params:
        params.update(extra_params)
    return AgentRequest(
        request_id="req_flow",
        channel_id="tui",
        session_id=session_id,
        req_method=req_method,
        params=params,
    )


def setup_function() -> None:
    _pending_plan_approvals.clear()
    _plan_approved_sessions.clear()


@pytest.mark.asyncio
async def test_direct_implement_refetches_normal_agent_instance(tmp_path: Path) -> None:
    """TUI still sends code.plan; server must switch to code:normal agent after approval."""
    session_id = "sess_refetch"
    plan_file = tmp_path / "sliding-window.md"
    plan_file.write_text("# Plan\n\nImplement sliding window.", encoding="utf-8")

    plan_agent = MagicMock()
    plan_instance = MagicMock()
    normal_agent = MagicMock()
    normal_instance = MagicMock()
    plan_agent.get_instance.return_value = plan_instance
    normal_agent.get_instance.return_value = normal_instance

    plan_state = SimpleNamespace(mode="plan", plan_slug="sliding-window")
    plan_instance.card = SimpleNamespace(id="code-agent")
    normal_instance.card = SimpleNamespace(id="code-agent")
    plan_instance.load_state.return_value = SimpleNamespace(plan_mode=plan_state)
    plan_instance.get_plan_file_path.return_value = plan_file
    plan_instance.restore_mode_after_plan_exit = MagicMock()

    manager = MagicMock()
    manager.get_agent = AsyncMock(side_effect=[plan_agent, normal_agent])

    server = AgentWebSocketServer.__new__(AgentWebSocketServer)
    server._agent_manager = manager
    server._resolve_code_language = MagicMock(return_value="cn")

    request = _chat_request(session_id, "好，那按计划实现吧", mode="code.plan")

    mock_session = MagicMock()
    mock_session.pre_run = AsyncMock()
    with (
        patch(
            "openjiuwen.core.single_agent.create_agent_session",
            MagicMock(return_value=mock_session),
        ),
        patch.object(
            AgentWebSocketServer,
            "_ensure_code_mode_state",
            new_callable=AsyncMock,
            return_value=True,
        ) as ensure_mock,
    ):
        mode, sub_mode, agent = await server._prepare_code_mode_chat_turn(
            request, "tui"
        )

    assert manager.get_agent.await_count == 2
    second_call = manager.get_agent.await_args_list[1].kwargs
    assert second_call["sub_mode"] == "normal"
    assert agent is normal_agent
    assert sub_mode == "normal"
    assert request.params["mode"] == "code.normal"
    assert session_id in _plan_approved_sessions
    assert "用户已批准" in request.params["query"]
    ensure_mock.assert_not_called()


@pytest.mark.asyncio
async def test_pending_approval_uses_normal_agent_without_extra_refetch() -> None:
    session_id = "sess_pending"
    _pending_plan_approvals[session_id] = {
        "pending": True,
        "plan_content": "# Plan\nstep 1",
        "plan_slug": "test",
        "plan_path": "/tmp/plan.md",
    }

    normal_agent = MagicMock()
    manager = MagicMock()
    manager.get_agent = AsyncMock(return_value=normal_agent)

    server = AgentWebSocketServer.__new__(AgentWebSocketServer)
    server._agent_manager = manager
    server._resolve_code_language = MagicMock(return_value="cn")

    request = _chat_request(session_id, "按计划实现", mode="code.plan")

    mode, sub_mode, agent = await server._prepare_code_mode_chat_turn(request, "tui")

    assert manager.get_agent.await_count == 1
    assert sub_mode == "normal"
    assert agent is normal_agent
    assert session_id not in _pending_plan_approvals


@pytest.mark.asyncio
async def test_confirm_interrupt_resume_does_not_consume_pending_approval() -> None:
    session_id = "sess_interrupt"
    _pending_plan_approvals[session_id] = {
        "pending": True,
        "plan_content": "# Plan",
        "plan_slug": "test",
        "plan_path": "/tmp/plan.md",
    }

    assert (
        _check_and_handle_pending_approval(
            _chat_request(
                session_id,
                "",
                extra_params={
                    "request_id": "tool_req_1",
                    "answers": {"approved": False},
                    "source": "confirm_interrupt",
                },
            ),
            language="cn",
        )
        is False
    )
    assert session_id in _pending_plan_approvals


@pytest.mark.asyncio
async def test_blocked_plan_to_normal_without_approval(tmp_path: Path) -> None:
    session_id = "sess_block"
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

    assert restored is False
    assert request.params["mode"] == "code.plan"
    plan_instance.restore_mode_after_plan_exit.assert_not_called()


@pytest.mark.asyncio
async def test_ensure_restores_plan_to_normal_after_approval(tmp_path: Path) -> None:
    session_id = "sess_restore"
    _plan_approved_sessions.add(session_id)

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
    request = _chat_request(session_id, "injected approval", mode="code.normal")
    request.params[PLAN_USER_APPROVED_FLAG] = True

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
    plan_instance.restore_mode_after_plan_exit.assert_called_once_with(session)
    assert session_id not in _plan_approved_sessions


@pytest.mark.asyncio
async def test_direct_implement_reads_plan_file(tmp_path: Path) -> None:
    session_id = "sess_direct"
    plan_file = tmp_path / "plan.md"
    plan_file.write_text("# Direct plan body", encoding="utf-8")

    agent = MagicMock()
    instance = MagicMock()
    agent.get_instance.return_value = instance
    instance.card = SimpleNamespace(id="code-agent")
    instance.load_state.return_value = SimpleNamespace(
        plan_mode=SimpleNamespace(mode="plan", plan_slug="plan")
    )
    instance.get_plan_file_path.return_value = plan_file

    request = _chat_request(session_id, "按计划实现")
    session = MagicMock()
    with patch(
        "openjiuwen.core.single_agent.create_agent_session",
        MagicMock(return_value=session),
    ):
        session.pre_run = AsyncMock()
        handled = await _try_handle_direct_plan_implement(
            request,
            agent,
            language="cn",
        )

    assert handled is True
    assert "Direct plan body" in request.params["query"]


@pytest.mark.asyncio
async def test_direct_implement_ignores_structured_a2ui_event() -> None:
    event = {
        "type": "a2ui.client_event",
        "event": {
            "userAction": {
                "name": "submitForm",
                "context": {"dietary": ["vegetarian"]},
            }
        },
    }
    request = AgentRequest(
        request_id="req_a2ui_event",
        channel_id="web",
        session_id="sess_a2ui_event",
        params={"query": event, "content": event, "mode": "agent.fast"},
    )

    handled = await _try_handle_direct_plan_implement(
        request,
        MagicMock(),
        language="cn",
    )

    assert handled is False
    assert request.params["query"] is event


@pytest.mark.asyncio
async def test_reject_switch_mode_then_direct_implement_exits_plan(
    tmp_path: Path,
) -> None:
    """Reproduce stuck scenario: reject switch_mode confirm, then chat approve."""
    session_id = "sess_stuck"
    plan_file = tmp_path / "plan.md"
    plan_file.write_text("# Sliding window plan\nstep 1", encoding="utf-8")

    plan_agent = MagicMock()
    normal_agent = MagicMock()
    plan_instance = MagicMock()
    normal_instance = MagicMock()
    plan_agent.get_instance.return_value = plan_instance
    normal_agent.get_instance.return_value = normal_instance
    plan_instance.card = SimpleNamespace(id="code-agent")
    normal_instance.card = SimpleNamespace(id="code-agent")
    plan_instance.load_state.return_value = SimpleNamespace(
        plan_mode=SimpleNamespace(mode="plan", plan_slug="plan")
    )
    plan_instance.get_plan_file_path.return_value = plan_file

    manager = MagicMock()
    manager.get_agent = AsyncMock(side_effect=[plan_agent, normal_agent])

    server = AgentWebSocketServer.__new__(AgentWebSocketServer)
    server._agent_manager = manager
    server._resolve_code_language = MagicMock(return_value="cn")

    # Step 1: user rejects switch_mode — pending must survive (none here).
    reject_req = _chat_request(
        session_id,
        "",
        extra_params={
            "request_id": "sw_1",
            "answers": {"approved": False},
            "source": "confirm_interrupt",
        },
    )
    assert _check_and_handle_pending_approval(reject_req, language="cn") is False

    # Step 2: user approves implementation in chat while TUI still shows plan.
    mock_session = MagicMock()
    mock_session.pre_run = AsyncMock()
    implement_req = _chat_request(session_id, "好，那按计划实现吧", mode="code.plan")

    with patch(
        "openjiuwen.core.single_agent.create_agent_session",
        MagicMock(return_value=mock_session),
    ):
        mode, sub_mode, agent = await server._prepare_code_mode_chat_turn(
            implement_req, "tui"
        )

    assert sub_mode == "normal"
    assert agent is normal_agent
    assert implement_req.params["mode"] == "code.normal"
    assert session_id in _plan_approved_sessions


@pytest.mark.asyncio
async def test_skills_list_does_not_restore_plan_mode() -> None:
    session_id = "sess_skills"
    _plan_approved_sessions.add(session_id)

    plan_agent = MagicMock()
    plan_instance = MagicMock()
    plan_agent.get_instance.return_value = plan_instance

    server = AgentWebSocketServer.__new__(AgentWebSocketServer)
    request = AgentRequest(
        request_id="req_skills",
        channel_id="tui",
        session_id=session_id,
        req_method=ReqMethod.SKILLS_LIST,
        params={"mode": "code.normal"},
    )

    restored = await server._ensure_code_mode_state(
        request, "code", "normal", plan_agent
    )

    assert restored is False
    plan_instance.restore_mode_after_plan_exit.assert_not_called()
