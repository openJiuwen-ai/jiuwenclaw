# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Streaming reaches the four silent channels without flooding them.

``chat.ask_user_question`` is only emitted as a stream chunk, so Discord, Slack,
WhatsApp and DingTalk -- which all built their inbound request without
``is_stream`` -- never saw the question at all. Turning streaming on is half the
fix; the other half is not posting every delta and tool call as its own message.
"""

from __future__ import annotations

import pytest

from jiuwenswarm.common.schema import Message
from jiuwenswarm.common.schema.message import EventType
from jiuwenswarm.gateway.channel_manager.im_platforms.platform_adapter.text_delivery import (
    STREAM_INTERMEDIATE_EVENTS,
    is_stream_intermediate,
)


def _msg(event_type=None, *, payload_event=None, content="hi"):
    payload = {"content": content}
    if payload_event is not None:
        payload["event_type"] = payload_event
    return Message(
        id="m1",
        type="res",
        channel_id="slack",
        session_id="s1",
        params={"content": content},
        timestamp=0.0,
        ok=True,
        event_type=event_type,
        payload=payload,
        metadata={},
    )


# ------------------------------------------------------------ what is dropped


@pytest.mark.parametrize("event", sorted(STREAM_INTERMEDIATE_EVENTS, key=lambda e: e.value))
def test_every_stream_intermediate_is_dropped(event) -> None:
    assert is_stream_intermediate(_msg(event)) is True


def test_the_raw_question_is_dropped() -> None:
    """The plain-text fallback rewrites it before send(); arriving raw means the rewrite declined."""
    assert is_stream_intermediate(_msg(EventType.CHAT_ASK_USER_QUESTION)) is True


def test_an_event_type_only_in_the_payload_is_still_recognised() -> None:
    """A forwarded chunk may carry the string without the enum."""
    assert is_stream_intermediate(_msg(payload_event="chat.tool_call")) is True


# ------------------------------------------------------------ what survives


@pytest.mark.parametrize(
    "event",
    [
        EventType.CHAT_FINAL,
        EventType.CHAT_ERROR,
        EventType.CHAT_FILE,
        EventType.CHAT_MEDIA,
        EventType.TEAM_MESSAGE,
        EventType.CHAT_INTERRUPT_RESULT,
        EventType.HEARTBEAT_RELAY,
    ],
)
def test_deliverable_events_survive(event) -> None:
    """The rule removes what streaming newly introduces, nothing else."""
    assert is_stream_intermediate(_msg(event)) is False


def test_a_message_with_no_event_type_survives() -> None:
    """A plain unary response envelope must still be delivered."""
    assert is_stream_intermediate(_msg(None)) is False


def test_the_b3_notice_survives() -> None:
    """The plain-text fallback rewrites the question into a chat.final, which must get through."""
    notice = _msg(EventType.CHAT_FINAL, payload_event="chat.final", content="1. Alpha")
    assert is_stream_intermediate(notice) is False


# --------------------------------------------------- the four inbound requests


@pytest.mark.parametrize(
    "module_path",
    [
        "jiuwenswarm.gateway.channel_manager.im_platforms.slack.slack_connect",
        "jiuwenswarm.gateway.channel_manager.im_platforms.discord.discord_connect",
        "jiuwenswarm.gateway.channel_manager.im_platforms.whatsapp.whatsapp_connect",
        "jiuwenswarm.gateway.channel_manager.im_platforms.dingtalk.dingtalk_connect",
    ],
)
def test_the_channel_module_imports(module_path: str) -> None:
    """The guard is called at send time, so a bad import fails only in production."""
    import importlib

    module = importlib.import_module(module_path)
    assert module is not None


@pytest.mark.parametrize(
    "source_path",
    [
        "jiuwenswarm/gateway/channel_manager/im_platforms/slack/slack_connect.py",
        "jiuwenswarm/gateway/channel_manager/im_platforms/discord/discord_connect.py",
        "jiuwenswarm/gateway/channel_manager/im_platforms/whatsapp/whatsapp_connect.py",
        "jiuwenswarm/gateway/channel_manager/im_platforms/dingtalk/dingtalk_connect.py",
    ],
)
def test_the_inbound_request_streams(source_path: str) -> None:
    """Without is_stream the question is never emitted and the plain-text fallback is inert."""
    from pathlib import Path

    source = Path(source_path).read_text(encoding="utf-8")
    assert "is_stream=True" in source, f"{source_path} still builds a non-streaming request"
