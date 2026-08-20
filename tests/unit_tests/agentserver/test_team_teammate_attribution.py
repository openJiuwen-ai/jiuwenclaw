# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Teammate stream attribution: member_name enrichment for relay routing."""

from __future__ import annotations

from types import SimpleNamespace

from jiuwenclaw.agentserver.deep_agent.team_helpers import (
    _enrich_leader_event,
    _enrich_teammate_event,
    _resolve_chunk_member_name,
)
from jiuwenclaw.agentserver.stream_utils import propagate_stream_source_id
from jiuwenclaw.schema.agent import AgentResponseChunk


def test_enrich_teammate_sets_member_name_from_source_member() -> None:
    chunk = SimpleNamespace(source_member="planning", role="teammate")
    parsed = _enrich_teammate_event({"event_type": "chat.delta", "content": "x"}, chunk)
    assert parsed["role"] == "teammate"
    assert parsed["member_name"] == "planning"


def test_enrich_leader_sets_member_name_from_source_member() -> None:
    chunk = SimpleNamespace(source_member="product", role="leader")
    for event_type in ("chat.reasoning", "chat.tool_call", "chat.delta"):
        parsed = _enrich_leader_event({"event_type": event_type}, chunk)
        assert parsed["role"] == "leader"
        assert parsed["member_name"] == "product"


def test_enrich_teammate_tool_and_reasoning_get_member_name() -> None:
    chunk = SimpleNamespace(source_member="agentteams")
    tool = _enrich_teammate_event(
        {"event_type": "chat.tool_call", "tool_call": {"name": "read_file"}},
        chunk,
    )
    reasoning = _enrich_teammate_event(
        {"event_type": "chat.reasoning", "content": "think"},
        chunk,
    )
    assert tool["member_name"] == "agentteams"
    assert reasoning["member_name"] == "agentteams"


def test_resolve_chunk_member_name_falls_back_to_payload() -> None:
    chunk = SimpleNamespace(source_member=None, payload={"member_name": "ms9qokx11j54c0"})
    assert _resolve_chunk_member_name({}, chunk) == "ms9qokx11j54c0"


def test_propagate_copies_role_and_member_name_from_tagged_chunk() -> None:
    """A tagged chunk (role+source_member attrs) must propagate them alongside stream_source_id."""
    chunk = SimpleNamespace(
        stream_source_id="leader",
        role="leader",
        source_member="office",
        payload={"event_type": "chat.delta", "content": "hi"},
    )
    result = propagate_stream_source_id(chunk, {"event_type": "chat.delta", "content": "hi"})
    assert result["stream_source_id"] == "leader"
    assert result["role"] == "leader"
    assert result["member_name"] == "office"


def test_propagate_copies_role_and_member_name_from_agent_response_chunk() -> None:
    """AgentResponseChunk stamped by _tag_chunk must propagate role/member_name to the parsed payload."""
    chunk = AgentResponseChunk(
        request_id="r1",
        channel_id="c",
        payload={"event_type": "chat.delta", "content": "tok", "source_chunk_type": "content_chunk"},
    )
    # Simulate the team stream controller stamping role/source_member onto a
    # non-OutputSchema chunk (the new _tag_chunk behavior).
    chunk.role = "teammate"
    chunk.source_member = "agentteam"
    result = propagate_stream_source_id(chunk, {"event_type": "chat.delta", "content": "tok"})
    assert result.get("role") == "teammate"
    assert result.get("member_name") == "agentteam"


def test_propagate_noop_for_non_team_chunk() -> None:
    """A plain chunk without role/source_member must not gain attribution fields."""
    chunk = AgentResponseChunk(
        request_id="r2",
        channel_id="c",
        payload={"event_type": "chat.delta", "content": "x", "stream_source_id": "main"},
    )
    result = propagate_stream_source_id(chunk, {"event_type": "chat.delta", "content": "x"})
    assert result.get("stream_source_id") == "main"
    assert "role" not in result
    assert "member_name" not in result
