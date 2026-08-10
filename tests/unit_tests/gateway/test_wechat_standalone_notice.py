# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""WeChat standalone notices must not tear down in-flight streaming state."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from jiuwenswarm.common.schema.message import EventType, Message
from jiuwenswarm.gateway.channel_manager.im_platforms.wechat.wechat_connect import (
    WechatChannel,
    WechatConfig,
)


def _ready_channel() -> WechatChannel:
    channel = WechatChannel(
        WechatConfig(enabled=True, bot_token="test-token"),
        bus=MagicMock(),
    )
    channel._http = MagicMock()
    channel._send_text_chunks_to_user = AsyncMock()
    channel._should_skip_due_to_send_limit = AsyncMock(return_value=False)
    return channel


@pytest.mark.asyncio
async def test_standalone_notice_keeps_streaming_session_and_round() -> None:
    channel = _ready_channel()
    channel._streaming_sessions.add("user-1")
    channel._continue_active_sessions.add("user-1")
    channel.current_round = 3
    channel._current_round_session_key = "user-1"

    msg = Message(
        id="req-1-compaction",
        type="res",
        channel_id="wechat",
        session_id="s1",
        params={"content": "🗜️ Compacted earlier messages."},
        timestamp=0.0,
        ok=True,
        payload={
            "event_type": "chat.final",
            "content": "🗜️ Compacted earlier messages.",
        },
        event_type=EventType.CHAT_FINAL,
        user_id="user-1",
        metadata={
            "standalone_notice": True,
            "im_sender_user_id": "user-1",
        },
    )

    await channel.send(msg)

    channel._send_text_chunks_to_user.assert_awaited_once()
    assert "user-1" in channel._streaming_sessions
    assert "user-1" in channel._continue_active_sessions
    assert channel.current_round == 3
    assert channel._current_round_session_key == "user-1"


@pytest.mark.asyncio
async def test_ordinary_chat_final_still_clears_streaming_state() -> None:
    channel = _ready_channel()
    channel._streaming_sessions.add("user-1")
    channel.current_round = 3
    channel._current_round_session_key = "user-1"

    msg = Message(
        id="req-1",
        type="res",
        channel_id="wechat",
        session_id="s1",
        params={"content": "hello"},
        timestamp=0.0,
        ok=True,
        payload={"event_type": "chat.final", "content": "hello"},
        event_type=EventType.CHAT_FINAL,
        user_id="user-1",
        metadata={"im_sender_user_id": "user-1"},
    )

    await channel.send(msg)

    assert "user-1" not in channel._streaming_sessions
    assert channel.current_round == 0
    assert channel._current_round_session_key == ""
