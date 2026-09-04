# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Feishu standalone notices must not open a second CardKit streaming card."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from jiuwenswarm.common.schema.message import EventType, Message
from jiuwenswarm.gateway.channel_manager.base import RobotMessageRouter
from jiuwenswarm.gateway.channel_manager.im_platforms.feishu.feishu_connect import (
    FeishuChannel,
    FeishuConfig,
)


def _make_channel() -> FeishuChannel:
    config = FeishuConfig(
        enabled=True,
        app_id="test_app_id",
        app_secret="test_app_secret",
        enable_streaming=True,
    )
    channel = FeishuChannel(config, MagicMock(spec=RobotMessageRouter))
    channel._api_client = MagicMock()
    channel._send_feishu_message = AsyncMock()
    channel._handle_cardkit_streaming_event = AsyncMock(return_value=True)
    return channel


def _standalone_notice() -> Message:
    return Message(
        id="req-1-compaction",
        type="res",
        channel_id="feishu",
        session_id="s1",
        params={"content": "🗜️ Compacted earlier messages."},
        timestamp=0.0,
        ok=True,
        payload={
            "event_type": "chat.final",
            "content": "🗜️ Compacted earlier messages.",
        },
        event_type=EventType.CHAT_FINAL,
        metadata={
            "standalone_notice": True,
            "feishu_open_id": "ou_test",
        },
    )


@pytest.mark.asyncio
async def test_standalone_notice_skips_cardkit_and_sends_plain() -> None:
    channel = _make_channel()

    await channel.send(_standalone_notice())

    channel._handle_cardkit_streaming_event.assert_not_awaited()
    assert channel._cardkit_sessions == {}
    channel._send_feishu_message.assert_awaited_once()
    args = channel._send_feishu_message.await_args.args
    assert args[0] == "ou_test"
    assert "Compacted" in args[2]


@pytest.mark.asyncio
async def test_ordinary_chat_final_still_uses_cardkit() -> None:
    channel = _make_channel()
    msg = Message(
        id="req-1",
        type="res",
        channel_id="feishu",
        session_id="s1",
        params={"content": "hello"},
        timestamp=0.0,
        ok=True,
        payload={"event_type": "chat.final", "content": "hello"},
        event_type=EventType.CHAT_FINAL,
        metadata={"feishu_open_id": "ou_test"},
    )

    await channel.send(msg)

    channel._handle_cardkit_streaming_event.assert_awaited_once()
    channel._send_feishu_message.assert_not_awaited()
