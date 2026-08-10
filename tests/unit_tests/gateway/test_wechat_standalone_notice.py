# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""WeChat must not wipe the delta accumulator for ask_user plain-text notices."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from jiuwenswarm.common.schema.message import EventType, Message
from jiuwenswarm.gateway.channel_manager.im_platforms.wechat.wechat_connect import (
    WechatChannel,
)


@pytest.mark.asyncio
async def test_standalone_notice_keeps_delta_session() -> None:
    channel = WechatChannel.__new__(WechatChannel)
    channel._http = object()
    channel.config = SimpleNamespace(bot_token="tok", enable_streaming=True)
    channel._delta_accumulator = SimpleNamespace(
        add_chunk=lambda *a, **k: None,
        flush=lambda *a, **k: "",
        clear=lambda key: cleared.append(key),
    )
    channel._delta_leading = {"sess": "partial answer"}
    channel._streaming_sessions = {"user-1"}
    channel._continue_active_sessions = set()
    channel.current_round = 3
    channel._current_round_session_key = "user-1"
    cleared: list[str] = []

    channel._extract_platform_user_id = lambda msg: "user-1"
    channel._is_reasoning_chunk = lambda msg: False
    channel._strip_think_tags = lambda text: text
    channel._is_thinking_only_content = lambda text: False
    channel._extract_content = lambda msg: "Which environment?\n\n1. staging\n2. production"
    channel._should_skip_due_to_send_limit = AsyncMock(return_value=False)
    channel._send_text_chunks_to_user = AsyncMock()
    channel._is_stream_accept_ack_only = lambda msg: False
    channel._is_stream_complete_marker = lambda msg: False
    channel._delta_session_key = lambda msg: "sess"
    channel._message_session_key = lambda msg: "user-1"
    channel._take_accumulated_delta = lambda msg: None

    msg = Message(
        id="q-1",
        type="res",
        channel_id="wechat",
        session_id="sess",
        params={"content": "Which environment?\n\n1. staging\n2. production"},
        timestamp=0.0,
        ok=True,
        event_type=EventType.CHAT_FINAL,
        payload={"content": "Which environment?\n\n1. staging\n2. production"},
        metadata={"standalone_notice": True, "ask_user_request_id": "req-1"},
    )

    assert await channel.send(msg) is True
    channel._send_text_chunks_to_user.assert_awaited_once()
    assert cleared == []
    assert channel._delta_leading == {"sess": "partial answer"}
    assert "user-1" in channel._streaming_sessions
    assert channel.current_round == 3
