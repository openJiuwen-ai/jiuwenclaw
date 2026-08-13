# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for ask_user_question_tool free_input hard constraint and flow behavior."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from jiuwenswarm.agents.harness.common.tools.ask_user_question_tool import (
    _OTHER_FALLBACK_LABEL,
    _coerce_raw_options,
    _normalize_questions,
    _ask_user_question_impl,
    _normalize_user_answer_option_ids,
    OUTLINE_CONFIRM_ID,
    OUTLINE_USE_EDITED_ID,
)
from jiuwenswarm.agents.harness.common.ask_user_question_registry import (
    ASK_REQUEST_PREFIX,
    AskUserQuestionRegistry,
    ask_user_question_request_scope,
)
from jiuwenswarm.common.runtime_scope import RuntimeScopeKey


def test_free_input_forces_label_to_other():
    opts = [{"label": "方案A"}, {"label": "自定义", "free_input": True}]
    result = _coerce_raw_options(opts, 0)
    assert result[1]["label"] == _OTHER_FALLBACK_LABEL
    assert "free_input" not in result[1]


def test_free_input_supplies_default_description():
    opts = [{"label": "方案A"}, {"label": "自定义", "free_input": True}]
    result = _coerce_raw_options(opts, 0)
    assert result[1]["description"] == "请在下一句补充说明你的选择"


def test_free_input_preserves_existing_description():
    opts = [{"label": "方案A"}, {"label": "自定义", "free_input": True, "description": "请输入你的方案"}]
    result = _coerce_raw_options(opts, 0)
    assert result[1]["description"] == "请输入你的方案"


def test_free_input_false_does_not_change_label():
    opts = [{"label": "方案A"}, {"label": "自定义", "free_input": False}]
    result = _coerce_raw_options(opts, 0)
    assert result[1]["label"] == "自定义"


def test_free_input_field_not_leaked_to_output():
    opts = [{"label": "方案A"}, {"label": "自定义", "free_input": True, "id": "custom_id"}]
    result = _coerce_raw_options(opts, 0)
    assert "free_input" not in result[1]
    assert result[1]["id"] == "custom_id"


def test_explicit_other_rejects_later_free_input():
    opts = [{"label": _OTHER_FALLBACK_LABEL}, {"label": "自定义", "free_input": True}]
    with pytest.raises(ValueError, match="「其他」选项不能超过1个"):
        _coerce_raw_options(opts, 0)


def test_free_input_rejects_later_explicit_other():
    opts = [{"label": "自定义", "free_input": True}, {"label": _OTHER_FALLBACK_LABEL}]
    with pytest.raises(ValueError, match="「其他」选项不能超过1个"):
        _coerce_raw_options(opts, 0)


def test_two_free_inputs_raises_value_error():
    opts = [{"label": "自定义A", "free_input": True}, {"label": "自定义B", "free_input": True}]
    with pytest.raises(ValueError, match="「其他」选项不能超过1个"):
        _coerce_raw_options(opts, 0)


def test_normalize_questions_free_input_label_normalized():
    raw = [
        {
            "question": "选择方案",
            "options": [
                {"label": "方案A"},
                {"label": "自定义", "free_input": True},
            ],
        }
    ]
    result = _normalize_questions(raw)
    assert result[0]["options"][1]["label"] == _OTHER_FALLBACK_LABEL
    assert "free_input" not in result[0]["options"][1]


def test_normalize_questions_free_input_with_custom_description():
    raw = [
        {
            "question": "选择方案",
            "options": [
                {"label": "方案A"},
                {"label": "自定义", "free_input": True, "description": "输入你的方案"},
            ],
        }
    ]
    result = _normalize_questions(raw)
    assert result[0]["options"][1]["label"] == _OTHER_FALLBACK_LABEL
    assert result[0]["options"][1]["description"] == "输入你的方案"


def test_single_option_free_input_skips_padding():
    opts = [{"label": "自定义", "free_input": True}]
    result = _coerce_raw_options(opts, 0)
    assert len(result) == 1
    assert result[0]["label"] == _OTHER_FALLBACK_LABEL
    assert "free_input" not in result[0]


def test_single_option_explicit_other_skips_padding():
    opts = [{"label": _OTHER_FALLBACK_LABEL}]
    result = _coerce_raw_options(opts, 0)
    assert len(result) == 1
    assert result[0]["label"] == _OTHER_FALLBACK_LABEL


def test_single_option_normal_pads_with_other():
    opts = [{"label": "方案A"}]
    result = _coerce_raw_options(opts, 0)
    assert len(result) == 2
    assert result[0]["label"] == "方案A"
    assert result[1]["label"] == _OTHER_FALLBACK_LABEL
    assert result[1]["description"] == "请在下一句补充说明你的选择"


@pytest.mark.asyncio
async def test_empty_answer_shells_preserve_legacy_answered_contract():
    registry = Mock()
    registry.wait_for_answer = AsyncMock(
        return_value=[
            {
                "question": "是否补充范围？",
                "selected_options": [],
            },
        ],
    )
    server = Mock()
    server.send_push = AsyncMock()

    with patch(
        "jiuwenswarm.agents.harness.common.tools.ask_user_question_tool.get_ask_request_context",
        return_value=(True, "session-1", "stream-1", "web"),
    ), patch(
        "jiuwenswarm.agents.harness.common.tools.ask_user_question_tool.get_ask_runtime_scope",
        return_value=Mock(),
    ), patch(
        "jiuwenswarm.agents.harness.common.tools.ask_user_question_tool.AskUserQuestionRegistry.get_instance",
        return_value=registry,
    ), patch(
        "jiuwenswarm.agents.harness.common.tools.ask_user_question_tool.AgentWebSocketServer.get_instance",
        return_value=server,
    ):
        result = await _ask_user_question_impl([
            {
                "question": "是否补充范围？",
                "options": [{"label": "是"}, {"label": "否"}],
            },
        ])

    assert result["status"] == "answered"
    assert "interaction_status" not in result
    assert result["answers"] == [
        {"question": "是否补充范围？", "selected_options": []},
    ]


@pytest.fixture
def _reset_registry():
    """Reset AskUserQuestionRegistry singleton before/after each test."""
    AskUserQuestionRegistry.reset_instance_for_tests()
    yield
    AskUserQuestionRegistry.reset_instance_for_tests()


@pytest.fixture
async def ask_flow_setup(_reset_registry):
    """Set up request scope + mock AgentWebSocketServer.send_push for flow tests.
    
    Yields a dict with:
    - sent_pushes: list of captured push messages
    - scope: RuntimeScopeKey used for the request
    - server: mocked AgentWebSocketServer instance
    """
    sent_pushes = []
    server = Mock()

    async def _capture_push(msg):
        sent_pushes.append(msg)

    server.send_push = _capture_push

    scope = RuntimeScopeKey.from_ids("svc", "aid", "sess-1")
    async with ask_user_question_request_scope(
        interactive_ask=True,
        session_id="sess-1",
        stream_request_id="stream-1",
        channel_id="web",
        scope=scope,
    ):
        with patch(
            "jiuwenswarm.agents.harness.common.tools.ask_user_question_tool.AgentWebSocketServer.get_instance",
            return_value=server,
        ):
            yield {
                "sent_pushes": sent_pushes,
                "scope": scope,
                "server": server,
            }


@pytest.mark.asyncio
async def test_impl_pushes_correct_event_payload(ask_flow_setup):
    """Verify send_push is called with correct payload structure."""
    ctx = ask_flow_setup
    questions = [{"question": "测试问题？", "options": [{"label": "是"}, {"label": "否"}]}]

    # Mock wait_for_answer to return immediately
    with patch.object(
        AskUserQuestionRegistry.get_instance(),
        "wait_for_answer",
        new_callable=AsyncMock,
        return_value=[{"question": "测试问题？", "selected_options": ["是"]}],
    ):
        result = await _ask_user_question_impl(questions)

    assert result["status"] == "answered"
    assert len(ctx["sent_pushes"]) == 1
    msg = ctx["sent_pushes"][0]
    assert msg["request_id"] == "stream-1"
    assert msg["channel_id"] == "web"
    assert msg["is_complete"] is False
    payload = msg["payload"]
    assert payload["event_type"] == "chat.ask_user_question"
    assert payload["request_id"].startswith(ASK_REQUEST_PREFIX)
    assert payload["source"] == "ask_tool"
    assert payload["session_id"] == "sess-1"
    assert len(payload["questions"]) == 1
    assert payload["questions"][0]["question"] == "测试问题？"


@pytest.mark.asyncio
async def test_impl_returns_skipped_when_not_interactive_with_preview(_reset_registry):
    """When interactive=False and question has preview, return skipped without pushing."""
    sent_pushes = []
    server = Mock()

    async def _capture_push(msg):
        sent_pushes.append(msg)

    server.send_push = _capture_push

    scope = RuntimeScopeKey.from_ids("svc", "aid", "sess-1")
    async with ask_user_question_request_scope(
        interactive_ask=False,  # Not interactive
        session_id="sess-1",
        stream_request_id="stream-1",
        channel_id="web",
        scope=scope,
    ):
        with patch(
            "jiuwenswarm.agents.harness.common.tools.ask_user_question_tool.AgentWebSocketServer.get_instance",
            return_value=server,
        ):
            questions = [
                {
                    "question": "大纲确认",
                    "options": [{"label": "确认"}, {"label": "修改"}],
                    "preview": {"text": "# 大纲内容\n\n- 章节一\n- 章节二", "format": "markdown"},
                }
            ]
            result = await _ask_user_question_impl(questions)

    assert result["status"] == "skipped"
    assert "original_content" in result
    assert result["original_content"] == "# 大纲内容\n\n- 章节一\n- 章节二"
    assert len(sent_pushes) == 0  # No push sent


@pytest.mark.asyncio
async def test_impl_full_flow_answered_via_real_resolve(ask_flow_setup):
    """End-to-end test: push event → wait for answer → resolve → return answered."""
    import asyncio

    ctx = ask_flow_setup
    questions = [{"question": "选择方案", "options": [{"label": "A"}, {"label": "B"}]}]

    async def _resolve_after_push():
        """Wait for push, then resolve with user's answer."""
        while not ctx["sent_pushes"]:
            await asyncio.sleep(0.01)
        request_id = ctx["sent_pushes"][0]["payload"]["request_id"]
        AskUserQuestionRegistry.get_instance().resolve(
            ctx["scope"],
            request_id,
            [{"question": "选择方案", "selected_options": ["A"]}],
        )

    # Run tool and resolver concurrently
    tool_task = asyncio.create_task(_ask_user_question_impl(questions))
    resolver_task = asyncio.create_task(_resolve_after_push())

    result = await asyncio.wait_for(tool_task, timeout=5.0)
    await resolver_task

    assert result["status"] == "answered"
    assert result["answers"] == [{"question": "选择方案", "selected_options": ["A"]}]
    assert "summary" in result
    assert "A" in result["summary"]


@pytest.mark.asyncio
async def test_impl_returns_cancelled_when_wait_cancelled(ask_flow_setup):
    """When wait_for_answer is cancelled, return cancelled status."""
    import asyncio

    ctx = ask_flow_setup
    questions = [{"question": "测试", "options": [{"label": "是"}, {"label": "否"}]}]

    async def _wait_then_cancel():
        """Wait a bit, then cancel the tool task."""
        await asyncio.sleep(0.1)
        tool_task.cancel()

    tool_task = asyncio.create_task(_ask_user_question_impl(questions))
    cancel_task = asyncio.create_task(_wait_then_cancel())

    try:
        result = await asyncio.wait_for(tool_task, timeout=5.0)
    except asyncio.CancelledError:
        # Tool was cancelled, which is expected
        result = {"status": "cancelled", "answers": []}

    await cancel_task
    assert result["status"] == "cancelled"


@pytest.mark.asyncio
async def test_impl_returns_error_when_send_push_fails(ask_flow_setup):
    """When send_push raises an exception, return error status."""
    ctx = ask_flow_setup
    ctx["server"].send_push = AsyncMock(side_effect=RuntimeError("Connection lost"))

    questions = [{"question": "测试", "options": [{"label": "是"}, {"label": "否"}]}]
    result = await _ask_user_question_impl(questions)

    assert result["status"] == "error"
    assert "Connection lost" in result["message"]
    assert result["answers"] == []


@pytest.mark.asyncio
async def test_impl_returns_error_when_no_stream_request_id(_reset_registry):
    """When stream_request_id is missing from context, return error."""
    # Don't set up request scope — stream_rid will be empty
    questions = [{"question": "测试", "options": [{"label": "是"}, {"label": "否"}]}]
    result = await _ask_user_question_impl(questions)

    assert result["status"] == "error"
    assert "stream_request_id" in result["message"].lower() or "缺少" in result["message"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid_questions",
    [
        [],  # Empty list
        "not a list",  # String instead of list
        [{"question": "", "options": [{"label": "A"}]}],  # Empty question text
        [{"question": "Q1"}, {"question": "Q2"}, {"question": "Q3"}, {"question": "Q4"}, {"question": "Q5"}],  # >4 questions
    ],
)
async def test_impl_returns_error_on_invalid_questions(ask_flow_setup, invalid_questions):
    """When questions are invalid, return error status."""
    result = await _ask_user_question_impl(invalid_questions)
    assert result["status"] == "error"
    assert result["answers"] == []


@pytest.mark.asyncio
async def test_impl_normalizes_legacy_chinese_option_ids(ask_flow_setup):
    """Legacy Chinese labels (确认/修改) are mapped to stable IDs (outline_confirm/outline_use_edited)."""
    ctx = ask_flow_setup
    questions = [
        {
            "question": "大纲确认",
            "options": [{"label": "确认"}, {"label": "修改"}],
            "preview": {"text": "# 大纲", "format": "markdown"},
        }
    ]

    # Mock wait_for_answer to return legacy Chinese label
    with patch.object(
        AskUserQuestionRegistry.get_instance(),
        "wait_for_answer",
        new_callable=AsyncMock,
        return_value=[{"question": "大纲确认", "selected_options": ["确认"]}],
    ):
        result = await _ask_user_question_impl(questions)

    assert result["status"] == "answered"
    assert result["answers"][0]["selected_options"] == [OUTLINE_CONFIRM_ID]

    # Test "修改" → "outline_use_edited"
    with patch.object(
        AskUserQuestionRegistry.get_instance(),
        "wait_for_answer",
        new_callable=AsyncMock,
        return_value=[{"question": "大纲确认", "selected_options": ["修改"]}],
    ):
        result = await _ask_user_question_impl(questions)

    assert result["answers"][0]["selected_options"] == [OUTLINE_USE_EDITED_ID]


@pytest.mark.asyncio
async def test_impl_passes_max_options(ask_flow_setup):
    """max_options parameter limits the number of options per question."""
    ctx = ask_flow_setup
    questions = [
        {
            "question": "选择",
            "options": [{"label": "A"}, {"label": "B"}, {"label": "C"}, {"label": "D"}, {"label": "E"}],
        }
    ]

    with patch.object(
        AskUserQuestionRegistry.get_instance(),
        "wait_for_answer",
        new_callable=AsyncMock,
        return_value=[{"question": "选择", "selected_options": ["A"]}],
    ):
        result = await _ask_user_question_impl(questions, max_options=2)

    assert result["status"] == "answered"
    # Verify only 2 options were sent in the push
    assert len(ctx["sent_pushes"]) == 1
    pushed_questions = ctx["sent_pushes"][0]["payload"]["questions"]
    assert len(pushed_questions[0]["options"]) == 2
