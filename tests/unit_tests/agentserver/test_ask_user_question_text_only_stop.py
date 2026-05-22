# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for ask_user_question text_only turn-stop helpers."""

from __future__ import annotations

import json

import pytest

from jiuwenclaw.agentserver.tools.ask_user_question_turn_stop import (
    ASK_USER_QUESTION_TOOL_NAMES,
    extract_text_only_stop_payload,
    is_ask_user_question_tool_name,
)


@pytest.mark.parametrize("name", sorted(ASK_USER_QUESTION_TOOL_NAMES))
def test_is_ask_user_question_tool_name_recognizes_registered_names(name: str) -> None:
    assert is_ask_user_question_tool_name(name) is True


def test_is_ask_user_question_tool_name_rejects_other_tools() -> None:
    assert is_ask_user_question_tool_name("read_file") is False


def test_extract_text_only_stop_payload_from_dict() -> None:
    payload = {
        "status": "text_only",
        "formatted_questions": "## 需要您的确认\n\n**风格**",
        "message": "wait",
        "answers": [],
        "stop_agent_turn": True,
    }
    stop = extract_text_only_stop_payload(payload)
    assert stop is not None
    assert stop["formatted_questions"].startswith("## 需要您的确认")
    assert stop["status"] == "text_only"


def test_extract_text_only_stop_payload_from_json_string() -> None:
    inner = {
        "status": "text_only",
        "formatted_questions": "请选择风格",
    }
    stop = extract_text_only_stop_payload(json.dumps(inner, ensure_ascii=False))
    assert stop is not None
    assert stop["formatted_questions"] == "请选择风格"


def test_extract_text_only_stop_payload_ignores_answered_status() -> None:
    assert extract_text_only_stop_payload({"status": "answered", "answers": [{}]}) is None


def test_extract_text_only_stop_payload_requires_non_empty_formatted_questions() -> None:
    assert extract_text_only_stop_payload({"status": "text_only", "formatted_questions": ""}) is None
