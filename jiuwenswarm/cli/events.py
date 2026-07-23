# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Event processing decisions: interactivity detection and stream termination."""

from __future__ import annotations

from typing import Any


def is_content_final(payload: dict[str, Any]) -> bool:
    """Return whether a chat.final envelope carries the assistant answer."""
    inner = payload.get("event_type", "")
    return not inner or inner == "chat.final"


def is_error_event(event_type: str, payload: dict[str, Any]) -> bool:
    """Return whether a terminal chat event carries an error."""
    return event_type == "chat.error" or (
        event_type == "chat.final"
        and (
            payload.get("event_type") == "team.error"
            or payload.get("error") not in (None, "")
        )
    )


def is_terminal_event(event_type: str, payload: dict[str, Any]) -> bool:
    if is_error_event(event_type, payload):
        return True
    if event_type == "chat.final":
        # Gateway uses chat.final as a compatibility envelope for event types
        # without an EventType mapping (team.runtime_ready, chat.llm_usage,
        # keepalive, etc.). Those control events do not end the response.
        return is_content_final(payload)
    if event_type == "chat.processing_status":
        if not payload.get("is_processing", True):
            return True
    return False


def needs_user_input(event_type: str) -> bool:
    return event_type in ("chat.ask_user_question", "plan.approval_required")


def event_kind(event_type: str) -> str:
    if event_type in ("chat.delta",):
        return "delta"
    if event_type in ("chat.reasoning",):
        return "reasoning"
    if event_type in ("chat.tool_call",):
        return "tool_call"
    if event_type in ("chat.tool_result",):
        return "tool_result"
    if event_type in ("chat.final",):
        return "final"
    if event_type in ("chat.error",):
        return "error"
    if event_type in ("chat.ask_user_question", "plan.approval_required"):
        return "interactive"
    if event_type in ("chat.processing_status",):
        return "processing_status"
    if event_type.startswith("chat.") or event_type.startswith("plan."):
        return "chat"
    return "other"
