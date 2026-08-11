# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for the steer.applied -> chat.steer_applied bridge.

agent-core emits ``steer.applied`` as an interaction event saying which steers
reached model context and which a rail removed.  Nothing forwards interaction
events generically: ``_parse_stream_chunk`` bridges them one type at a time, so
an event without a branch there is silently dropped rather than surfaced.  This
event is the only way a client can learn that text it acknowledged was never
actually read, which is why the branch and this test exist.
"""

from __future__ import annotations

from unittest.mock import patch

from jiuwenswarm.server.runtime.agent_adapter import interface_deep
from jiuwenswarm.server.runtime.agent_adapter.interface_deep import JiuWenSwarmDeepAdapter

_APPLIED_TYPE = "steer.applied"


class _TypedChunk:
    """Stands in for agent-core's InteractionEvent (has .type and .payload)."""

    def __init__(self, type_: str, payload: object) -> None:
        self.type = type_
        self.payload = payload


# --- the payload builder ------------------------------------------------------


def test_applied_and_dropped_survive_as_lists() -> None:
    """The two lists are the whole point; a count would lose which text it was."""
    payload = JiuWenSwarmDeepAdapter._interaction_steer_applied_payload(
        {
            "applied": [{"id": "s1", "text": "prefer the async client"}],
            "dropped": ["s2"],
        }
    )

    assert payload["event_type"] == "chat.steer_applied"
    assert payload["applied"] == [{"id": "s1", "text": "prefer the async client"}]
    assert payload["dropped"] == ["s2"]


def test_a_non_dict_payload_still_yields_the_event() -> None:
    """A malformed event must not become a missing event."""
    for bad in (None, "steer.applied", 42, []):
        payload = JiuWenSwarmDeepAdapter._interaction_steer_applied_payload(bad)
        assert payload["event_type"] == "chat.steer_applied"
        assert payload["applied"] == []
        assert payload["dropped"] == []


def test_non_list_members_are_coerced_rather_than_forwarded() -> None:
    """A client iterates these; a string would iterate character by character."""
    payload = JiuWenSwarmDeepAdapter._interaction_steer_applied_payload(
        {"applied": "s1", "dropped": {"s2": True}}
    )

    assert payload["applied"] == []
    assert payload["dropped"] == []


def test_an_empty_applied_list_is_preserved_not_defaulted() -> None:
    """Everything dropped is a real outcome and must not look like no data."""
    payload = JiuWenSwarmDeepAdapter._interaction_steer_applied_payload(
        {"applied": [], "dropped": ["s1", "s2"]}
    )

    assert payload["applied"] == []
    assert payload["dropped"] == ["s1", "s2"]


# --- the two parse branches ---------------------------------------------------


def test_a_typed_chunk_is_bridged() -> None:
    parsed = JiuWenSwarmDeepAdapter._parse_stream_chunk(
        _TypedChunk(_APPLIED_TYPE, {"applied": [{"id": "s1", "text": "x"}], "dropped": []})
    )

    assert parsed is not None
    assert parsed["event_type"] == "chat.steer_applied"
    assert parsed["applied"] == [{"id": "s1", "text": "x"}]


def test_a_dict_chunk_is_bridged() -> None:
    """agent-core reaches this adapter as a typed event or as a plain dict."""
    parsed = JiuWenSwarmDeepAdapter._parse_stream_chunk(
        {"type": _APPLIED_TYPE, "payload": {"applied": [], "dropped": ["s9"]}}
    )

    assert parsed is not None
    assert parsed["event_type"] == "chat.steer_applied"
    assert parsed["dropped"] == ["s9"]


def test_an_unrelated_event_is_untouched() -> None:
    """Control: the branch must key on the event type, not swallow everything."""
    parsed = JiuWenSwarmDeepAdapter._parse_stream_chunk(
        {"type": "something.else", "output": "hello"}
    )

    assert parsed == {"event_type": "chat.delta", "content": "hello"}


def test_the_wire_string_is_compared_not_the_enum_member() -> None:
    """Why there is no version guard here any more.

    The event is matched as the string that arrives on the queue, so the bridge
    works against any agent-core -- including the commit pinned in uv.lock,
    whose InteractionEventType has no STEER_APPLIED member at all. Reading the
    enum would have made this parser go quiet on that pin while the generic
    parser the Team stream uses kept working, which is the sort of split nobody
    notices until one client stops reporting drops.
    """
    from openjiuwen.harness.schema.interaction import InteractionEventType

    member = getattr(InteractionEventType, "STEER_APPLIED", None)
    if member is not None:
        # When the installed core does define it, the string must agree.
        assert member.value == interface_deep.STEER_APPLIED_CORE_TYPE
    # Either way the bridge fires, because it never consults the enum.
    parsed = JiuWenSwarmDeepAdapter._parse_stream_chunk(
        _TypedChunk(interface_deep.STEER_APPLIED_CORE_TYPE, {"applied": [], "dropped": []})
    )
    assert parsed is not None
    assert parsed["event_type"] == "chat.steer_applied"


# --- the Team path uses the generic parser ------------------------------------
#
# team_helpers streams through server/utils/stream_utils.parse_stream_chunk, not
# through the adapter's parser. While the mapping lived only on the adapter, a
# Team leader's applied event arrived under agent-core's raw name and every
# client ignored it.


def test_the_generic_parser_maps_a_typed_steer_applied_chunk() -> None:
    from jiuwenswarm.server.utils.stream_utils import parse_stream_chunk

    parsed = parse_stream_chunk(
        _TypedChunk(_APPLIED_TYPE, {"applied": [{"id": "s1", "text": "x"}], "dropped": ["s2"]})
    )

    assert parsed is not None
    assert parsed["event_type"] == "chat.steer_applied"
    assert parsed["applied"] == [{"id": "s1", "text": "x"}]
    assert parsed["dropped"] == ["s2"]


def test_the_generic_parser_maps_a_dict_steer_applied_chunk() -> None:
    from jiuwenswarm.server.utils.stream_utils import parse_stream_chunk

    parsed = parse_stream_chunk(
        {"type": _APPLIED_TYPE, "payload": {"applied": [], "dropped": ["s9"]}}
    )

    assert parsed is not None
    assert parsed["event_type"] == "chat.steer_applied"
    assert parsed["dropped"] == ["s9"]


def test_the_raw_core_name_never_reaches_a_client() -> None:
    """The bug this closes: clients listen for chat.steer_applied only."""
    from jiuwenswarm.server.utils.stream_utils import parse_stream_chunk

    for chunk in (
        _TypedChunk(_APPLIED_TYPE, {"applied": [], "dropped": []}),
        {"type": _APPLIED_TYPE, "payload": {"applied": [], "dropped": []}},
    ):
        parsed = parse_stream_chunk(chunk)
        assert parsed is not None
        assert parsed["event_type"] != _APPLIED_TYPE


def test_both_parsers_produce_the_same_shape() -> None:
    """One normaliser, so the Team and single-agent payloads cannot drift."""
    from jiuwenswarm.server.utils.stream_utils import parse_stream_chunk

    payload = {"applied": [{"id": "s1", "text": "x"}], "dropped": ["s2"]}
    generic = parse_stream_chunk(_TypedChunk(_APPLIED_TYPE, dict(payload)))
    adapter = JiuWenSwarmDeepAdapter._parse_stream_chunk(
        _TypedChunk(_APPLIED_TYPE, dict(payload))
    )

    assert generic == adapter
