# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Xiaoyi must not merge compaction notices into the in-flight A2A stream."""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jiuwenswarm.common.schema.message import EventType, Message
from jiuwenswarm.gateway.channel_manager.im_platforms.xiaoyi.xiaoyi_connect import (
    XiaoyiChannel,
    XiaoyiChannelConfig,
)
from jiuwenswarm.gateway.routing.keys import XiaoyiDeliveryTarget
from jiuwenswarm.gateway.routing.session_sharing import RoutingTarget


def _make_channel(*, api_id: str = "") -> XiaoyiChannel:
    config = XiaoyiChannelConfig(
        enabled=True,
        channel_id="xiaoyi",
        enable_streaming=True,
        api_id=api_id,
    )
    channel = XiaoyiChannel(config, router=MagicMock())
    channel._ws_connections = {"ws1": object()}
    channel._send_text_response = AsyncMock()
    return channel


def _standalone_notice(*, content: str = "🗜️ Compacted earlier messages.") -> Message:
    return Message(
        id="req-1-compaction",
        type="res",
        channel_id="xiaoyi",
        session_id="logical-sess",
        params={"content": content},
        timestamp=0.0,
        ok=True,
        payload={"event_type": "chat.final", "content": content},
        event_type=EventType.CHAT_FINAL,
        metadata={
            "standalone_notice": True,
            "xiaoyi_session_id": "sid-1",
            # task id stripped by compression_notice rewrite
        },
    )


@pytest.mark.asyncio
async def test_standalone_notice_does_not_flush_ws_buffer() -> None:
    channel = _make_channel()
    channel._ws_flush_buffers["task-ABC"] = "partial answer"

    await channel._send_ws_to_user(
        "sid-1",
        "task-ABC",
        _standalone_notice(),
        "🗜️ Compacted earlier messages.",
    )

    assert channel._ws_flush_buffers["task-ABC"] == "partial answer"
    channel._send_text_response.assert_awaited_once()
    kwargs = channel._send_text_response.await_args
    assert kwargs.args[1] == "req-1-compaction"  # notice task id, not active stream
    assert kwargs.kwargs["is_final"] is False
    assert kwargs.kwargs["last_chunk"] is True
    assert "Compacted" in kwargs.args[2]


@pytest.mark.asyncio
async def test_standalone_notice_legacy_does_not_clobber_accumulated_text() -> None:
    channel = _make_channel()
    channel._accumulated_texts["sid-1"] = "partial answer so far"

    await channel._send_legacy(_standalone_notice())

    assert channel._accumulated_texts["sid-1"] == "partial answer so far"
    channel._send_text_response.assert_awaited_once()
    kwargs = channel._send_text_response.await_args
    assert kwargs.kwargs["is_final"] is False
    assert kwargs.args[1] == "req-1-compaction"


@pytest.mark.asyncio
async def test_standalone_notice_team_push_sends_immediately_without_merge() -> None:
    """Inactive team users use push; notices must not enter the merge window."""
    channel = _make_channel(api_id="api-1")
    channel._active_push_sessions.clear()
    pending = "other team reply still buffering"
    channel._push_merge_buffers["agent-1"] = [
        (time.time(), pending, pending[:30], "push-1"),
    ]

    mock_service = MagicMock()
    mock_service.send_push = AsyncMock()
    delivery = XiaoyiDeliveryTarget(agent_id="agent-1", push_id="push-1")
    target = RoutingTarget(intent="godview", delivery=delivery)
    notice = _standalone_notice(content="Context 85% — compacting…")

    with patch(
        "jiuwenswarm.gateway.channel_manager.im_platforms.xiaoyi.xiaoyi_connect.XiaoYiPushService",
        return_value=mock_service,
    ):
        await channel._send_team(notice, target)

    mock_service.send_push.assert_awaited_once()
    _summary, pushed = mock_service.send_push.await_args.args
    assert "compacting" in pushed
    assert pending not in pushed
    assert channel._push_merge_buffers["agent-1"][0][1] == pending
    assert all("compacting" not in entry[1] for entry in channel._push_merge_buffers["agent-1"])
    assert "agent-1" not in channel._push_flush_tasks or channel._push_flush_tasks["agent-1"].done()
