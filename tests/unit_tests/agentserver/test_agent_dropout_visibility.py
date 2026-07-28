# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for AgentDropout stream visibility helpers."""

from __future__ import annotations

from jiuwenswarm.server.runtime.agent_adapter.team_helpers import (
    _is_agent_dropout_visibility_event,
)


def test_visibility_allows_agent_dropout_notice():
    assert _is_agent_dropout_visibility_event(
        {
            "event_type": "chat.notice",
            "notice_type": "agent_dropout_drop",
            "content": "dropped",
        }
    )
    assert _is_agent_dropout_visibility_event(
        {
            "event_type": "chat.notice",
            "notice_type": "agent_dropout_check",
            "content": "checking",
        }
    )


def test_visibility_allows_agent_dropout_reasoning_and_tools():
    assert _is_agent_dropout_visibility_event(
        {
            "event_type": "chat.reasoning",
            "source": "agent_dropout",
            "content": "Auditing…",
        }
    )
    assert _is_agent_dropout_visibility_event(
        {
            "event_type": "chat.tool_call",
            "tool_call": {"name": "agent_dropout_audit", "id": "ad-1"},
        }
    )
    assert _is_agent_dropout_visibility_event(
        {
            "event_type": "chat.tool_result",
            "tool_name": "agent_dropout_audit",
            "result": "PASS",
        }
    )
    assert not _is_agent_dropout_visibility_event(
        {"event_type": "chat.reasoning", "content": "normal think"}
    )


def test_visibility_allows_agent_dropout_shutdown():
    assert _is_agent_dropout_visibility_event(
        {
            "event_type": "team.member",
            "type": "team.member.shutdown",
            "reason": "agent_dropout",
            "member_id": "teammate-1",
        }
    )


def test_visibility_rejects_unrelated_events():
    assert not _is_agent_dropout_visibility_event(
        {"event_type": "chat.delta", "content": "hi"}
    )
    assert not _is_agent_dropout_visibility_event(
        {
            "event_type": "team.member",
            "type": "team.member.shutdown",
            "reason": "other",
        }
    )
