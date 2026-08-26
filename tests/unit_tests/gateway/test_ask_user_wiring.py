# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""The two ends of the ask_user fallback joined up: dispatch registers, inbound converts.

These exist because the unit suites cannot see a NameError. Both hooks call
symbols that are imported locally in their modules, so a missing import fails
only at call time -- which is exactly how a previous change to this file broke
thirteen tests.
"""

from __future__ import annotations

import pytest

from jiuwenswarm.common.schema import Message
from jiuwenswarm.common.schema.message import EventType, ReqMethod
from jiuwenswarm.gateway.channel_manager.channel_manager import (
    conversation_key_of,
    deliver_with_ask_user_fallback,
    prepare_ask_user_outbound,
    register_ask_user_pending,
)
from jiuwenswarm.gateway.message_handler.message_handler import MessageHandler
from jiuwenswarm.gateway.routing.pending_question import PENDING_QUESTIONS


@pytest.fixture(autouse=True)
def _clean_registry():
    PENDING_QUESTIONS.discard("telegram", "s1")
    PENDING_QUESTIONS.discard("xiaoyi", "s1")
    yield
    PENDING_QUESTIONS.discard("telegram", "s1")
    PENDING_QUESTIONS.discard("xiaoyi", "s1")


def _question_msg(channel_id="telegram", session_id="s1", request_id="req-7", options=None):
    opts = options if options is not None else [{"label": "Alpha"}, {"label": "Beta"}]
    return Message(
        id="req-42",
        type="event",
        channel_id=channel_id,
        session_id=session_id,
        params={},
        timestamp=0.0,
        ok=True,
        payload={
            "event_type": "chat.ask_user_question",
            "request_id": request_id,
            "source": "ask_user_interrupt",
            "questions": [
                {"question": "Which one?", "options": opts}
            ],
        },
        metadata={},
    )


def _reply_msg(text, channel_id="telegram", session_id="s1"):
    return Message(
        id="in-1",
        type="req",
        channel_id=channel_id,
        session_id=session_id,
        params={"query": text},
        timestamp=0.0,
        ok=True,
        req_method=ReqMethod.CHAT_SEND,
        payload={},
        metadata={},
    )


def _deliver_and_register(msg, *, channel_id=None):
    """Simulate the production order: rewrite, then register only after 'send'."""
    outbound = prepare_ask_user_outbound(msg, channel_id=channel_id)
    if outbound is not None and outbound is not msg:
        register_ask_user_pending(msg, channel_id=channel_id or msg.channel_id)
    return outbound


# ------------------------------------------------------------------ outbound


def test_prepare_rewrites_without_registering() -> None:
    """A failed send must not leave a pending question that steals later replies."""
    out = prepare_ask_user_outbound(_question_msg())

    assert out is not None
    assert out.event_type == EventType.CHAT_FINAL
    assert "1. Alpha" in out.payload["content"]
    assert PENDING_QUESTIONS.peek("telegram", "s1") is None


def test_register_after_successful_delivery() -> None:
    out = _deliver_and_register(_question_msg())

    assert out is not None
    pending = PENDING_QUESTIONS.peek("telegram", "s1")
    assert pending is not None
    assert pending.request_id == "req-7"
    assert pending.options == ("Alpha", "Beta")
    assert pending.source == "ask_user_interrupt"


@pytest.mark.asyncio
async def test_deliver_registers_only_when_send_returns_true() -> None:
    """A skipped/failed send must not leave a pending question that steals ``1``."""

    class _Channel:
        def __init__(self, result):
            self.result = result
            self.sent = []

        async def send(self, msg, **kwargs):
            self.sent.append(msg)
            return self.result

    msg = _question_msg()

    skipped = _Channel(False)
    await deliver_with_ask_user_fallback(skipped, msg)
    assert skipped.sent and skipped.sent[0].event_type == EventType.CHAT_FINAL
    assert PENDING_QUESTIONS.peek("telegram", "s1") is None

    legacy_none = _Channel(None)
    await deliver_with_ask_user_fallback(legacy_none, msg)
    assert PENDING_QUESTIONS.peek("telegram", "s1") is None

    ok = _Channel(True)
    await deliver_with_ask_user_fallback(ok, msg)
    assert PENDING_QUESTIONS.peek("telegram", "s1") is not None


def test_a_channel_with_a_card_registers_nothing() -> None:
    """Feishu answers through its own callback; a pending record would double-answer."""
    original = _question_msg(channel_id="feishu")
    assert prepare_ask_user_outbound(original) is original
    register_ask_user_pending(original)
    assert PENDING_QUESTIONS.peek("feishu", "s1") is None


def test_fan_out_target_channel_drives_rewrite_and_registration() -> None:
    """Team fan-out delivers to a physical channel that may differ from msg.channel_id."""
    # Originator channel is feishu (rich), but the fan-out target is xiaoyi (text-only).
    msg = _question_msg(channel_id="feishu")
    out = prepare_ask_user_outbound(msg, channel_id="xiaoyi")

    assert out is not None and out is not msg
    assert "1. Alpha" in out.payload["content"]
    register_ask_user_pending(msg, channel_id="xiaoyi")
    assert PENDING_QUESTIONS.peek("xiaoyi", "s1") is not None
    assert PENDING_QUESTIONS.peek("feishu", "s1") is None


def test_the_conversation_key_is_the_session() -> None:
    assert conversation_key_of(_question_msg(session_id="s9")) == "s9"


# ------------------------------------------------------------------- inbound


def test_a_numbered_reply_becomes_an_interrupt_resume() -> None:
    """Web/CLI resume ask_user via chat.send + source; chat.user_answer does not."""
    _deliver_and_register(_question_msg())
    reply = _reply_msg("2")

    converted = MessageHandler._convert_pending_question_reply(None, reply)

    assert converted is True
    assert reply.req_method == ReqMethod.CHAT_SEND
    assert reply.is_stream is True
    assert reply.params["source"] == "ask_user_interrupt"
    assert reply.params["request_id"] == "req-7"
    assert reply.params["query"] == ""
    assert reply.params["answers"][0]["selected_options"] == ["Beta"]


def test_an_unrelated_message_stays_a_chat_send() -> None:
    """It must reach the agent, and the question must stay answerable."""
    _deliver_and_register(_question_msg())
    reply = _reply_msg("hold on, let me check")

    converted = MessageHandler._convert_pending_question_reply(None, reply)

    assert converted is False
    assert reply.req_method == ReqMethod.CHAT_SEND
    assert reply.params == {"query": "hold on, let me check"}
    assert PENDING_QUESTIONS.peek("telegram", "s1") is not None


def test_a_reply_with_no_pending_question_is_untouched() -> None:
    reply = _reply_msg("2")

    assert MessageHandler._convert_pending_question_reply(None, reply) is False
    assert reply.req_method == ReqMethod.CHAT_SEND


def test_an_answer_message_is_not_reconverted() -> None:
    """A card channel already sends CHAT_ANSWER; touching it would double-handle."""
    _deliver_and_register(_question_msg())
    already = _reply_msg("2")
    already.req_method = ReqMethod.CHAT_ANSWER

    assert MessageHandler._convert_pending_question_reply(None, already) is False


# ------------------------------------------------------------ Other / custom_input


_OTHER_OPTIONS = [{"label": "Alpha"}, {"label": "Beta"}, {"label": "Other"}]


def test_a_bare_other_reply_is_not_converted_and_stays_pending() -> None:
    """StructuredAskUserRail rejects a bare Other as an empty answer (#2330)."""
    _deliver_and_register(_question_msg(options=_OTHER_OPTIONS))
    reply = _reply_msg("Other")

    converted = MessageHandler._convert_pending_question_reply(None, reply)

    assert converted is False
    assert reply.params == {"query": "Other"}
    assert PENDING_QUESTIONS.peek("telegram", "s1") is not None


def test_free_text_resumes_as_other_custom_input() -> None:
    """The free text must satisfy StructuredAskUserRail via custom_input, not vanish."""
    _deliver_and_register(_question_msg(options=_OTHER_OPTIONS))
    reply = _reply_msg("Actually I want a third thing")

    converted = MessageHandler._convert_pending_question_reply(None, reply)

    assert converted is True
    assert reply.params["source"] == "ask_user_interrupt"
    assert reply.params["request_id"] == "req-7"
    assert reply.params["answers"][0]["selected_options"] == ["Other"]
    assert reply.params["answers"][0]["custom_input"] == "Actually I want a third thing"
    assert PENDING_QUESTIONS.peek("telegram", "s1") is None
