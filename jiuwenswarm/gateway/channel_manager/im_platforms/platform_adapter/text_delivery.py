# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Suppress stream intermediates on channels that post one message at a time.

``chat.ask_user_question`` is only emitted as a stream chunk, so a channel that
requests a non-streaming RPC never sees the question at all and the
plain-text fallback cannot render it. Turning streaming on fixes that and creates a second problem: deltas,
reasoning and tool activity start arriving too, and a channel with no
edit-in-place UI would post each one as its own message.

This is a **denylist**, not an allowlist, and that is deliberate. These four
channels each deliver a slightly different set today -- Slack drops only
deltas, WhatsApp drops them conditionally, DingTalk routes files before text,
Discord filters nothing -- and none of them can be verified live here. Dropping
a message a channel used to deliver is a worse failure than letting one noisy
event through, so the rule only removes what streaming newly introduces and
leaves every existing behaviour untouched.

Telegram uses an allowlist instead (``should_deliver_telegram_outbound``). That
divergence is intentional for now: it is the one channel with a live round trip
behind it, so its stricter rule is backed by evidence the others do not yet
have. Converging the two is worth doing once the rest have been tested for
real.
"""

from __future__ import annotations

from typing import Any

from jiuwenswarm.common.schema.message import EventType

#: Events that exist only because the request streams. None of these was ever
#: delivered to a text-only channel before streaming was turned on for
#: these channels, so dropping them restores the previous experience
#: rather than changing it.
STREAM_INTERMEDIATE_EVENTS = frozenset({
    EventType.CHAT_DELTA,
    EventType.CHAT_REASONING,
    EventType.CHAT_RETRACT,
    EventType.CHAT_TOOL_CALL,
    EventType.CHAT_TOOL_UPDATE,
    EventType.CHAT_TOOL_RESULT,
    EventType.CHAT_USAGE_METADATA,
    EventType.CHAT_USAGE_SUMMARY,
    EventType.CHAT_SYMPHONY_STATUS,
    EventType.CHAT_PROCESSING_STATUS,
    EventType.CHAT_SUBTASK_UPDATE,
    EventType.CHAT_EVOLUTION_STATUS,
    # The raw question. The plain-text fallback rewrites it into a
    # chat.final before send() is reached, so seeing it here means the
    # rewrite declined -- and the payload
    # is a structured question, not a sentence, so posting it would be worse
    # than saying nothing.
    EventType.CHAT_ASK_USER_QUESTION,
})

_STREAM_INTERMEDIATE_VALUES = frozenset(
    event.value for event in STREAM_INTERMEDIATE_EVENTS
)


def is_stream_intermediate(msg: Any) -> bool:
    """Whether this outbound message is stream noise a text channel should skip.

    Reads the event type from the message and from ``payload`` because the two
    do not always agree: a rewritten message carries the enum, while a raw
    forwarded chunk may only have the string in its payload.
    """
    event_type = getattr(msg, "event_type", None)
    if event_type in STREAM_INTERMEDIATE_EVENTS:
        return True

    value = getattr(event_type, "value", None) or event_type
    if isinstance(value, str) and value in _STREAM_INTERMEDIATE_VALUES:
        return True

    payload = getattr(msg, "payload", None)
    if isinstance(payload, dict):
        payload_type = payload.get("event_type")
        if isinstance(payload_type, str) and payload_type in _STREAM_INTERMEDIATE_VALUES:
            return True
    return False


__all__ = [
    "STREAM_INTERMEDIATE_EVENTS",
    "is_stream_intermediate",
]
