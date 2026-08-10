# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Telegram must use stream RPC without spamming the chat with deltas."""

from __future__ import annotations

import pytest

from jiuwenswarm.common.schema import Message
from jiuwenswarm.common.schema.message import EventType
from jiuwenswarm.gateway.channel_manager.im_platforms.telegram.telegram_connect import (
    build_telegram_inbound_message,
    should_deliver_telegram_outbound,
)


def _msg(*, event_type=None, content="", msg_type="res"):
    payload = {"content": content}
    if event_type is not None:
        payload["event_type"] = (
            event_type.value if isinstance(event_type, EventType) else event_type
        )
    return Message(
        id="m1",
        type=msg_type,
        channel_id="telegram",
        session_id="telegram_1",
        params={"content": content} if content else {},
        timestamp=0.0,
        ok=True,
        payload=payload,
        event_type=event_type if isinstance(event_type, EventType) else None,
        metadata={"chat_id": 1},
    )


@pytest.mark.parametrize(
    "event_type",
    [
        EventType.CHAT_FINAL,
        EventType.CHAT_ERROR,
        EventType.CHAT_INTERRUPT_RESULT,
        EventType.HEARTBEAT_RELAY,
    ],
)
def test_final_like_events_are_delivered(event_type: EventType) -> None:
    assert should_deliver_telegram_outbound(_msg(event_type=event_type, content="hi")) is True


def test_plain_res_without_event_type_is_delivered() -> None:
    """Unary complete responses still need to reach the user."""
    assert should_deliver_telegram_outbound(_msg(content="hello", msg_type="res")) is True


@pytest.mark.parametrize(
    "event_type",
    [
        EventType.CHAT_DELTA,
        EventType.CHAT_REASONING,
        EventType.CHAT_TOOL_CALL,
        EventType.CHAT_TOOL_RESULT,
        EventType.CHAT_PROCESSING_STATUS,
        EventType.CHAT_ASK_USER_QUESTION,
        EventType.TODO_UPDATED,
        EventType.CHAT_USAGE_METADATA,
    ],
)
def test_stream_noise_is_not_delivered(event_type: EventType) -> None:
    """Telegram has no edit-in-place UI; delivering deltas would spam the chat."""
    assert should_deliver_telegram_outbound(_msg(event_type=event_type, content="x")) is False


def test_empty_final_is_not_worth_delivering() -> None:
    assert should_deliver_telegram_outbound(_msg(event_type=EventType.CHAT_FINAL, content="")) is False


def test_inbound_chat_send_uses_stream_rpc() -> None:
    """Without stream RPC, ask_user never emits chat.ask_user_question to the gateway."""
    msg = build_telegram_inbound_message(
        message_id="9",
        session_id="telegram_1",
        text="hi",
        chat_id=1,
        user_id=1,
        username=None,
        is_group_chat=False,
    )
    from jiuwenswarm.common.schema.message import ReqMethod

    assert msg.is_stream is True
    assert msg.req_method == ReqMethod.CHAT_SEND
    assert msg.params["query"] == "hi"
