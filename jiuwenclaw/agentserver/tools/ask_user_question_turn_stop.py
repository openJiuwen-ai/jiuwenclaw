# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Helpers to end the agent turn after ask_user_question text_only results.

Kept in a separate module to avoid circular imports between stream_event_rail
and ask_user_question_tool (which imports AgentWebSocketServer).
"""

from __future__ import annotations

import json
from typing import Any

ASK_USER_QUESTION_TOOL_NAMES = frozenset(
    {"ask_user_question", "jiuwenclaw_ask_user_question"},
)

_TEXT_ONLY_STOP_STATUSES = frozenset({"text_only"})


def is_ask_user_question_tool_name(tool_name: str) -> bool:
    """Return True when *tool_name* refers to the AskUserQuestion tool."""
    return str(tool_name or "").strip() in ASK_USER_QUESTION_TOOL_NAMES


def _coerce_tool_result_dict(tool_result: Any) -> dict[str, Any] | None:
    if isinstance(tool_result, dict):
        return tool_result
    if isinstance(tool_result, str):
        stripped = tool_result.strip()
        if not stripped:
            return None
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def extract_text_only_stop_payload(tool_result: Any) -> dict[str, Any] | None:
    """When ask_user_question returns text_only, extract user-visible stop text.

    Returns a dict with ``formatted_questions`` when the tool signals that the
    agent turn must end and wait for the user's next message. Otherwise None.
    """
    raw = _coerce_tool_result_dict(tool_result)
    if raw is None:
        return None
    status = str(raw.get("status", "") or "").strip()
    if status not in _TEXT_ONLY_STOP_STATUSES:
        return None
    formatted = str(raw.get("formatted_questions", "") or "").strip()
    if not formatted:
        return None
    return {
        "status": status,
        "formatted_questions": formatted,
        "message": str(raw.get("message", "") or "").strip(),
    }
