# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Team progress ping: relay stall watchdog liveness during E2A-suppressed segments."""

from __future__ import annotations

from jiuwenclaw.agentserver.deep_agent import team_helpers


def test_is_e2a_suppressed_event_matches_suppressed_types() -> None:
    assert team_helpers._is_e2a_suppressed_event("chat.tool_calls.delta") is True


def test_is_e2a_suppressed_event_rejects_business_types() -> None:
    for et in ("chat.delta", "chat.reasoning", "chat.final", "chat.error",
               "chat.usage_metadata", "chat.processing_status", "team.task",
               "team.member", "chat.tool_call", "chat.tool_result"):
        assert team_helpers._is_e2a_suppressed_event(et) is False, et


def test_is_e2a_suppressed_event_handles_none_and_blank() -> None:
    assert team_helpers._is_e2a_suppressed_event(None) is False
    assert team_helpers._is_e2a_suppressed_event("") is False
