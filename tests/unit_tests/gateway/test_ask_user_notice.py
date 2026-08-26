# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Rendering an ``ask_user`` question for a channel with no card.

The agent is blocked on the tool result while this event is in flight, so the
thing being pinned here is not cosmetic: on the eight channels with no handler
the question produces nothing and the conversation hangs.
"""

from __future__ import annotations

import pytest

from jiuwenswarm.common.schema import Message
from jiuwenswarm.common.schema.message import EventType
from jiuwenswarm.gateway.channel_manager.im_platforms.platform_adapter.ask_user_notice import (
    as_text_message,
    channel_renders_questions,
    format_question_notice,
    parse_question,
)


def _payload(question="Which one?", options=("Alpha", "Beta"), request_id="req-7", header=""):
    entry = {"question": question}
    if options is not None:
        entry["options"] = [{"label": label} for label in options]
    if header:
        entry["header"] = header
    return {
        "event_type": "chat.ask_user_question",
        "request_id": request_id,
        "questions": [entry],
    }


def _msg(channel_id="telegram", payload=None, metadata=None):
    return Message(
        id="req-42",
        type="event",
        channel_id=channel_id,
        session_id="s1",
        params={},
        timestamp=0.0,
        ok=True,
        payload=payload if payload is not None else _payload(),
        metadata=metadata if metadata is not None else {},
    )


# ---------------------------------------------------------------- capability


@pytest.mark.parametrize("channel", ["feishu", "web", "tui", "acp", "ssh", "FEISHU"])
def test_channels_with_their_own_question_ui_are_left_alone(channel: str) -> None:
    """Feishu draws a card; a text copy would be a duplicate question."""
    assert channel_renders_questions(channel) is True
    original = _msg(channel_id=channel)
    assert as_text_message(original) is original


@pytest.mark.parametrize(
    "channel", ["telegram", "slack", "discord", "wechat", "wecom", "dingtalk", "whatsapp", "xiaoyi"],
)
def test_text_only_channels_get_a_rewrite(channel: str) -> None:
    assert channel_renders_questions(channel) is False
    out = as_text_message(_msg(channel_id=channel))
    assert out is not None
    assert "Alpha" in out.payload["content"]


# ------------------------------------------------------------------ parsing


def test_options_are_parsed_in_order() -> None:
    parsed = parse_question(_payload(options=("Alpha", "Beta", "Gamma")))
    assert parsed is not None
    assert parsed.options == ("Alpha", "Beta", "Gamma")
    assert parsed.request_id == "req-7"


def test_bare_string_options_are_accepted() -> None:
    """Not every producer wraps a label in a dict."""
    payload = _payload(options=None)
    payload["questions"][0]["options"] = ["Alpha", "Beta"]
    parsed = parse_question(payload)
    assert parsed is not None
    assert parsed.options == ("Alpha", "Beta")


def test_a_free_text_question_parses_with_no_options() -> None:
    parsed = parse_question(_payload(options=None))
    assert parsed is not None
    assert parsed.options == ()


def test_a_payload_with_no_questions_is_not_a_question() -> None:
    assert parse_question({"request_id": "x", "questions": []}) is None
    assert parse_question({"request_id": "x"}) is None
    assert parse_question(None) is None


def test_only_the_first_question_is_used() -> None:
    """Two interleaved option sets could not be answered by ``2``."""
    payload = _payload()
    payload["questions"].append({"question": "Second?", "options": [{"label": "Later"}]})
    parsed = parse_question(payload)
    assert parsed is not None
    assert parsed.question == "Which one?"
    assert parsed.options == ("Alpha", "Beta")


# --------------------------------------------------------------- formatting


def test_options_are_numbered_from_one() -> None:
    text = format_question_notice(parse_question(_payload(options=("Alpha", "Beta"))))
    assert "1. Alpha" in text
    assert "2. Beta" in text
    assert "Which one?" in text


def test_a_free_text_question_says_what_is_expected() -> None:
    """An empty option list would reproduce the hang in a new way."""
    text = format_question_notice(parse_question(_payload(options=None)))
    assert "Reply to this message" in text
    assert "1." not in text


def test_the_header_is_shown_when_present() -> None:
    text = format_question_notice(parse_question(_payload(header="Deploy")))
    assert "Deploy" in text


# ----------------------------------------------------------------- envelope


def test_the_notice_is_shaped_like_an_ordinary_chat_final() -> None:
    """WeCom/WeChat only deliver CHAT_FINAL / plain res; content alone is dropped."""
    original = _msg(metadata={"wecom_req_id": "stream-1", "chat_type": "dm"})
    out = as_text_message(original)

    assert out is not None and out is not original
    assert out.type == "res"
    assert out.event_type == EventType.CHAT_FINAL
    assert out.payload["event_type"] == "chat.final"
    assert out.id == "req-42-question"
    assert out.params["content"] == out.payload["content"]


def test_stream_binding_metadata_is_stripped() -> None:
    out = as_text_message(_msg(metadata={"wecom_req_id": "stream-1", "chat_type": "dm"}))
    assert "wecom_req_id" not in out.metadata
    assert out.metadata["chat_type"] == "dm"


def test_the_notice_is_marked_standalone() -> None:
    """WeChat clears its delta accumulator on CHAT_FINAL; the answer must survive."""
    out = as_text_message(_msg())
    assert out.metadata["standalone_notice"] is True


def test_the_request_id_travels_with_the_notice() -> None:
    """The reply has to be correlated back to this exact question."""
    out = as_text_message(_msg())
    assert out.metadata["ask_user_request_id"] == "req-7"


def test_a_non_question_event_is_untouched() -> None:
    original = _msg(payload={"event_type": "chat.final", "content": "hi"})
    assert as_text_message(original) is original


def test_a_question_with_nothing_in_it_is_not_delivered() -> None:
    assert as_text_message(_msg(payload={"event_type": "chat.ask_user_question", "questions": []})) is None


def test_a_question_without_request_id_is_not_delivered() -> None:
    """An empty request_id cannot resume the blocked tool call."""
    assert as_text_message(_msg(payload=_payload(request_id=""))) is None
    assert parse_question(_payload(request_id="")) is None
    assert parse_question(_payload(request_id="   ")) is None


def test_as_text_message_honours_an_explicit_target_channel() -> None:
    """Fan-out may deliver to a text channel while msg.channel_id is a rich one."""
    original = _msg(channel_id="feishu")
    assert as_text_message(original) is original
    out = as_text_message(original, channel_id="telegram")
    assert out is not None and out is not original
    assert "Alpha" in out.payload["content"]
