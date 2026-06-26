# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Event processing decisions: interactivity detection and stream termination."""

from __future__ import annotations

from typing import Any


def is_terminal_event(event_type: str, payload: dict[str, Any]) -> bool:
    if event_type == "chat.error":
        return True
    if event_type == "chat.final":
        inner = payload.get("event_type", "")
        if inner == "keepalive":
            return False
        # Team control events (team.runtime_ready, team.completed) are
        # broadcast through the chat.final envelope because they lack an
        # EventType enum mapping on the gateway side. They are NOT terminal —
        # the real round-complete signal is
        # chat.processing_status(is_processing=False). team.error is the
        # exception: it indicates a failed team stream and should terminate.
        if isinstance(inner, str) and inner.startswith("team."):
            return inner == "team.error"
        return True
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
