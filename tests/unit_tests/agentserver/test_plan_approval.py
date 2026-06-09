# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for text-only plan approval gate."""

# pylint: disable=protected-access

from jiuwenswarm.agents.harness.code.prompt.plan_approval import (
    PLAN_USER_APPROVED_FLAG,
    classify_plan_user_intent,
    is_direct_plan_implement_request,
)
from jiuwenswarm.common.schema.agent import AgentRequest
from jiuwenswarm.common.schema.message import ReqMethod
from jiuwenswarm.server.agent_ws_server import (
    _check_and_handle_pending_approval,
    _pending_plan_approvals,
    _plan_approved_sessions,
    AgentWebSocketServer,
)


def _make_request(session_id: str, query: str, mode: str = "code.normal") -> AgentRequest:
    return AgentRequest(
        request_id="req_test",
        channel_id="tui",
        session_id=session_id,
        params={"query": query, "mode": mode},
    )


def setup_function() -> None:
    _pending_plan_approvals.clear()
    _plan_approved_sessions.clear()


def test_direct_plan_implement_requires_strong_signal() -> None:
    assert is_direct_plan_implement_request("好，那按计划实现吧") is True
    assert is_direct_plan_implement_request("按计划实现") is True
    assert is_direct_plan_implement_request("好") is False
    assert is_direct_plan_implement_request("可以") is False


def test_classify_implement_intent_as_approve() -> None:
    assert classify_plan_user_intent("按计划实现") == "approve"
    assert classify_plan_user_intent("开始实现吧") == "approve"
    assert classify_plan_user_intent("implement the plan") == "approve"


def test_classify_revision_intent() -> None:
    assert classify_plan_user_intent("多添加几个边界测试用例") == "revise"
    assert classify_plan_user_intent("第二步改成异步") == "revise"
    assert classify_plan_user_intent("不行，先别做") == "revise"


def test_classify_mixed_revision_overrides_short_approval() -> None:
    assert classify_plan_user_intent("可以，但是要把第二步改成异步") == "revise"


def test_pending_approval_accepts_free_text_approve() -> None:
    session_id = "sess_approve"
    _pending_plan_approvals[session_id] = {
        "pending": True,
        "plan_content": "# Plan\n\nDo the thing",
        "plan_slug": "test-plan",
        "plan_path": "/tmp/plan.md",
    }
    request = _make_request(session_id, "可以，开始吧")

    assert _check_and_handle_pending_approval(request, language="cn") is True
    assert session_id not in _pending_plan_approvals
    assert request.params["mode"] == "code.normal"
    assert request.params[PLAN_USER_APPROVED_FLAG] is True
    assert session_id in _plan_approved_sessions
    assert "用户已批准" in request.params["query"]
    assert "Do the thing" in request.params["query"]


def test_approve_registers_session_level_flag() -> None:
    session_id = "sess_flag"
    _pending_plan_approvals[session_id] = {
        "pending": True,
        "plan_content": "# Plan",
        "plan_slug": "test-plan",
        "plan_path": "/tmp/plan.md",
    }
    request = _make_request(session_id, "按计划实现")

    assert _check_and_handle_pending_approval(request, language="cn") is True
    assert session_id in _plan_approved_sessions


def test_skills_list_does_not_sync_code_mode() -> None:
    request = AgentRequest(
        request_id="req_skills",
        channel_id="tui",
        session_id="sess_skills",
        req_method=ReqMethod.SKILLS_LIST,
        params={"mode": "code.normal"},
    )
    assert AgentWebSocketServer._should_sync_code_mode_state(request) is False


def test_pending_approval_accepts_implement_phrase() -> None:
    session_id = "sess_implement"
    _pending_plan_approvals[session_id] = {
        "pending": True,
        "plan_content": "# Plan",
        "plan_slug": "test-plan",
        "plan_path": "/tmp/plan.md",
    }
    request = _make_request(session_id, "按计划实现")

    assert _check_and_handle_pending_approval(request, language="cn") is True
    assert request.params["mode"] == "code.normal"
    assert request.params[PLAN_USER_APPROVED_FLAG] is True


def test_pending_approval_accepts_feedback_text() -> None:
    session_id = "sess_feedback"
    _pending_plan_approvals[session_id] = {
        "pending": True,
        "plan_content": "# Plan",
        "plan_slug": "test-plan",
        "plan_path": "/tmp/plan.md",
    }
    request = _make_request(session_id, "多添加几个边界测试用例")

    assert _check_and_handle_pending_approval(request, language="cn") is True
    assert request.params["mode"] == "code.plan"
    assert PLAN_USER_APPROVED_FLAG not in request.params
    assert "用户要求修订计划" in request.params["query"]
    assert "多添加几个边界测试用例" in request.params["query"]


def test_pending_approval_ignores_structured_a2ui_event() -> None:
    session_id = "sess_a2ui_event"
    _pending_plan_approvals[session_id] = {
        "pending": True,
        "plan_content": "# Plan",
        "plan_slug": "test-plan",
        "plan_path": "/tmp/plan.md",
    }
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
        session_id=session_id,
        params={"query": event, "content": event, "mode": "agent.fast"},
    )

    assert _check_and_handle_pending_approval(request, language="cn") is False
    assert _pending_plan_approvals[session_id]["pending"] is True
    assert request.params["query"] is event
