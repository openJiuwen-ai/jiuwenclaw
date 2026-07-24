# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for ask_user_question_tool free_input hard constraint."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, Mock, patch

from jiuwenclaw.agentserver.tools.ask_user_question_tool import (
    _OTHER_FALLBACK_LABEL,
    _coerce_raw_options,
    _normalize_questions,
    _ask_user_question_impl,
)


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
async def test_explicit_skipped_answer_is_not_reported_as_answered():
    registry = Mock()
    registry.wait_for_answer = AsyncMock(
        return_value={"status": "skipped", "answers": []},
    )
    server = Mock()
    server.send_push = AsyncMock()

    with patch(
        "jiuwenclaw.agentserver.tools.ask_user_question_tool.get_ask_request_context",
        return_value=(True, "session-1", "stream-1", "web"),
    ), patch(
        "jiuwenclaw.agentserver.tools.ask_user_question_tool.get_ask_runtime_scope",
        return_value=Mock(),
    ), patch(
        "jiuwenclaw.agentserver.tools.ask_user_question_tool.AskUserQuestionRegistry.get_instance",
        return_value=registry,
    ), patch(
        "jiuwenclaw.agentserver.tools.ask_user_question_tool.AgentWebSocketServer.get_instance",
        return_value=server,
    ):
        result = await _ask_user_question_impl([
            {
                "question": "是否补充范围？",
                "options": [{"label": "是"}, {"label": "否"}],
            },
        ])

    assert result == {
        "status": "skipped",
        "message": "用户未提供额外反馈，继续执行原始请求。",
        "answers": [],
    }
