# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
# pylint: disable=protected-access

"""Tests for WeiboChannel functionality."""

import json
import math
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jiuwenclaw.channel.weibo_channel import (
    WeiboChannel,
    WeiboChannelConfig,
    _DEFAULT_WS_ENDPOINT,
    _DEFAULT_TOKEN_ENDPOINT,
)
from jiuwenclaw.channel.base import RobotMessageRouter
from jiuwenclaw.schema.message import Message, ReqMethod


# ---------------------------------------------------------------------------
# Helper Functions
# ---------------------------------------------------------------------------

def _make_channel(**config_kwargs) -> WeiboChannel:
    """Create a WeiboChannel instance for testing."""
    config = WeiboChannelConfig(
        enabled=True,
        app_id="test_app_id_123456",
        app_secret="test_app_secret",
        allow_from=["user_001", "user_002"],
        enable_streaming=True,
        **config_kwargs,
    )
    router = MagicMock(spec=RobotMessageRouter)
    return WeiboChannel(config, router)


def _make_inbound_payload(**kwargs) -> dict:
    """Create a mock inbound Weibo message payload."""
    return {
        "from_user_id": kwargs.get("from_user_id", "user_001"),
        "text": kwargs.get("text", "你好微博"),
        "message_id": kwargs.get("message_id", "wb_msg_001"),
        "timestamp": kwargs.get("timestamp", 1744800000000),
    }


# ---------------------------------------------------------------------------
# Test: WeiboChannelConfig Defaults
# ---------------------------------------------------------------------------

def test_weibo_config_defaults():
    """Test WeiboChannelConfig default values."""
    config = WeiboChannelConfig()

    assert config.enabled is False
    assert config.app_id == ""
    assert config.app_secret == ""
    assert config.allow_from == []
    assert config.enable_streaming is True
    assert config.ws_endpoint == _DEFAULT_WS_ENDPOINT
    assert config.token_endpoint == _DEFAULT_TOKEN_ENDPOINT


def test_weibo_config_custom():
    """Test WeiboChannelConfig with custom values."""
    config = WeiboChannelConfig(
        enabled=True,
        app_id="weibo_app",
        app_secret="weibo_secret",
        allow_from=["user1"],
        enable_streaming=False,
    )

    assert config.enabled is True
    assert config.app_id == "weibo_app"
    assert config.app_secret == "weibo_secret"
    assert config.allow_from == ["user1"]
    assert config.enable_streaming is False


# ---------------------------------------------------------------------------
# Test: WeiboChannel Initialization
# ---------------------------------------------------------------------------

def test_weibo_channel_init():
    """Test WeiboChannel initialization."""
    config = WeiboChannelConfig(enabled=True, app_id="app", app_secret="secret")
    router = MagicMock(spec=RobotMessageRouter)
    channel = WeiboChannel(config, router)

    assert channel.name == "weibo"
    assert channel.channel_id == "weibo"
    assert channel.config is config
    assert channel.is_running is False
    assert channel.message_callback is None
    assert channel.token == ""
    assert math.isclose(channel.token_expire_at, 0.0)
    assert channel.sessions == {}
    assert channel.tasks == []


def test_weibo_channel_clients_empty():
    """Test WeiboChannel clients property returns empty set."""
    channel = _make_channel()
    assert channel.clients == set()


def test_weibo_channel_on_message():
    """Test WeiboChannel on_message callback registration."""
    channel = _make_channel()
    cb = MagicMock()
    channel.on_message(cb)
    assert channel.message_callback is cb


# ---------------------------------------------------------------------------
# Test: extract_content
# ---------------------------------------------------------------------------

def test_extract_content_from_params():
    """Test content extraction from params."""
    msg = Message(
        id="1", type="req", channel_id="weibo", session_id="s1",
        params={"content": "hello"}, timestamp=0, ok=True,
        req_method=ReqMethod.CHAT_SEND,
    )
    assert WeiboChannel.extract_content(msg) == "hello"


def test_extract_content_from_payload():
    """Test content extraction from payload."""
    msg = Message(
        id="1", type="req", channel_id="weibo", session_id="s1",
        params={}, timestamp=0, ok=True,
        req_method=ReqMethod.CHAT_SEND,
        payload={"content": "world"},
    )
    assert WeiboChannel.extract_content(msg) == "world"


def test_extract_content_dict_with_output():
    """Test content extraction when content is a dict with output key."""
    msg = Message(
        id="1", type="req", channel_id="weibo", session_id="s1",
        params={"content": {"output": "extracted"}}, timestamp=0, ok=True,
        req_method=ReqMethod.CHAT_SEND,
    )
    assert WeiboChannel.extract_content(msg) == "extracted"


def test_extract_content_empty():
    """Test content extraction when no content available."""
    msg = Message(
        id="1", type="req", channel_id="weibo", session_id="s1",
        params={}, timestamp=0, ok=True,
        req_method=ReqMethod.CHAT_SEND,
    )
    assert WeiboChannel.extract_content(msg) == ""


# ---------------------------------------------------------------------------
# Test: WebSocket Message Handling
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_handle_ws_message_type_message():
    """Test handling inbound 'message' type WebSocket message."""
    channel = _make_channel()
    inbound_spy = AsyncMock()
    channel._on_inbound_message = inbound_spy

    payload = _make_inbound_payload()
    raw = json.dumps({"type": "message", "payload": payload})

    await channel._handle_ws_message(raw)

    inbound_spy.assert_called_once_with(payload)


@pytest.mark.asyncio
async def test_handle_ws_message_type_ping():
    """Test handling server-side ping message."""
    channel = _make_channel()
    mock_ws = MagicMock()
    mock_ws.closed = False
    mock_ws.send_str = AsyncMock()
    channel._ws = mock_ws

    raw = json.dumps({"type": "ping"})
    await channel._handle_ws_message(raw)

    mock_ws.send_str.assert_called_once_with(json.dumps({"type": "pong"}))


@pytest.mark.asyncio
async def test_handle_ws_message_type_system():
    """Test handling system message (should just log)."""
    channel = _make_channel()
    # Should not raise
    raw = json.dumps({
        "type": "system",
        "payload": {"message": "系统通知"},
    })
    await channel._handle_ws_message(raw)


@pytest.mark.asyncio
async def test_handle_ws_message_type_ack():
    """Test handling ack message (should be ignored)."""
    channel = _make_channel()
    raw = json.dumps({"type": "ack"})
    await channel._handle_ws_message(raw)


@pytest.mark.asyncio
async def test_handle_ws_message_type_error():
    """Test handling error message (should log error)."""
    channel = _make_channel()
    raw = json.dumps({
        "type": "error",
        "payload": {"code": 500, "message": "server error"},
    })
    await channel._handle_ws_message(raw)


@pytest.mark.asyncio
async def test_handle_ws_message_invalid_json():
    """Test handling non-JSON message."""
    channel = _make_channel()
    # Should not raise
    await channel._handle_ws_message("not json at all")


@pytest.mark.asyncio
async def test_handle_ws_message_unknown_type():
    """Test handling unknown message type."""
    channel = _make_channel()
    raw = json.dumps({"type": "unknown_type"})
    await channel._handle_ws_message(raw)


# ---------------------------------------------------------------------------
# Test: Inbound Message Processing
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_on_inbound_message_allowed():
    """Test processing inbound message from allowed user."""
    channel = _make_channel()
    channel.bus.route_user_message = AsyncMock()

    payload = _make_inbound_payload(
        from_user_id="user_001",
        text="你好微博",
        message_id="wb_msg_001",
    )
    await channel._on_inbound_message(payload)

    channel.bus.route_user_message.assert_called_once()
    msg = channel.bus.route_user_message.call_args[0][0]
    assert msg.params["content"] == "你好微博"
    assert msg.metadata["from_user_id"] == "user_001"
    assert msg.metadata["weibo_scene"] == "dm"
    assert msg.channel_id == "weibo"


@pytest.mark.asyncio
async def test_on_inbound_message_unauthorized():
    """Test inbound message from unauthorized user is rejected."""
    channel = _make_channel()
    channel.bus.route_user_message = AsyncMock()

    payload = _make_inbound_payload(from_user_id="bad_user")
    await channel._on_inbound_message(payload)

    channel.bus.route_user_message.assert_not_called()


@pytest.mark.asyncio
async def test_on_inbound_message_with_callback():
    """Test inbound message routes to callback when registered."""
    channel = _make_channel()
    cb = AsyncMock()
    channel.on_message(cb)

    payload = _make_inbound_payload()
    await channel._on_inbound_message(payload)

    cb.assert_called_once()
    msg = cb.call_args[0][0]
    assert msg.params["content"] == "你好微博"


@pytest.mark.asyncio
async def test_on_inbound_message_missing_from_user_id():
    """Test inbound message without from_user_id is skipped."""
    channel = _make_channel()
    channel.bus.route_user_message = AsyncMock()

    payload = _make_inbound_payload()
    payload["from_user_id"] = ""
    await channel._on_inbound_message(payload)

    channel.bus.route_user_message.assert_not_called()


@pytest.mark.asyncio
async def test_on_inbound_message_with_input_text():
    """Test inbound message extracts text from input array."""
    channel = _make_channel()
    channel.bus.route_user_message = AsyncMock()

    payload = _make_inbound_payload(text="")
    payload["input"] = [{"text": "从input提取的文本"}]
    await channel._on_inbound_message(payload)

    channel.bus.route_user_message.assert_called_once()
    msg = channel.bus.route_user_message.call_args[0][0]
    assert msg.params["content"] == "从input提取的文本"


@pytest.mark.asyncio
async def test_on_inbound_message_session_caching():
    """Test that sessions are cached for repeat users."""
    channel = _make_channel()
    channel.bus.route_user_message = AsyncMock()

    payload = _make_inbound_payload(from_user_id="user_001")
    await channel._on_inbound_message(payload)

    session_key = "weibo_user_001"
    assert session_key in channel.sessions
    assert channel.sessions[session_key] == "weibo_dm_user_001"

    # Second message should reuse session
    await channel._on_inbound_message(payload)
    assert channel.bus.route_user_message.call_count == 2


@pytest.mark.asyncio
async def test_on_inbound_message_timestamp_parsing():
    """Test timestamp parsing for millisecond and second timestamps."""
    channel = _make_channel()
    channel.bus.route_user_message = AsyncMock()

    # Millisecond timestamp
    payload_ms = _make_inbound_payload(timestamp=1744800000000)
    await channel._on_inbound_message(payload_ms)
    msg_ms = channel.bus.route_user_message.call_args[0][0]
    assert msg_ms.timestamp == 1744800000.0

    # Second timestamp
    channel.bus.route_user_message.reset_mock()
    payload_s = _make_inbound_payload(timestamp=1744800000)
    await channel._on_inbound_message(payload_s)
    msg_s = channel.bus.route_user_message.call_args[0][0]
    assert msg_s.timestamp == 1744800000


@pytest.mark.asyncio
async def test_on_inbound_message_camel_case_fields():
    """Test inbound message with camelCase field names."""
    channel = _make_channel()
    channel.bus.route_user_message = AsyncMock()

    payload = {
        "fromUserId": "user_002",
        "text": "hello",
        "messageId": "wb_msg_002",
        "timestamp": 1744800000000,
    }
    await channel._on_inbound_message(payload)

    channel.bus.route_user_message.assert_called_once()
    msg = channel.bus.route_user_message.call_args[0][0]
    assert msg.metadata["from_user_id"] == "user_002"
    assert msg.metadata["message_id"] == "wb_msg_002"


# ---------------------------------------------------------------------------
# Test: send
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_send_message():
    """Test sending message via WebSocket."""
    channel = _make_channel()
    mock_ws = MagicMock()
    mock_ws.closed = False
    mock_ws.send_str = AsyncMock()
    channel._ws = mock_ws

    msg = Message(
        id="reply_001", type="req", channel_id="weibo", session_id="s1",
        params={"content": "微博回复"}, timestamp=0, ok=True,
        req_method=ReqMethod.CHAT_SEND,
        metadata={"from_user_id": "user_001"},
    )
    await channel.send(msg)

    mock_ws.send_str.assert_called_once()
    sent_data = json.loads(mock_ws.send_str.call_args[0][0])
    assert sent_data["type"] == "send_message"
    assert sent_data["payload"]["toUserId"] == "user_001"
    assert sent_data["payload"]["text"] == "微博回复"
    assert sent_data["payload"]["messageId"] == "reply_001"


@pytest.mark.asyncio
async def test_send_message_fallback_session_id():
    """Test send uses session_id as fallback for to_user_id."""
    channel = _make_channel()
    mock_ws = MagicMock()
    mock_ws.closed = False
    mock_ws.send_str = AsyncMock()
    channel._ws = mock_ws

    msg = Message(
        id="reply_002", type="req", channel_id="weibo",
        session_id="weibo_dm_user_002",
        params={"content": "回复"}, timestamp=0, ok=True,
        req_method=ReqMethod.CHAT_SEND,
        metadata={},
    )
    await channel.send(msg)

    sent_data = json.loads(mock_ws.send_str.call_args[0][0])
    assert sent_data["payload"]["toUserId"] == "user_002"


@pytest.mark.asyncio
async def test_send_not_connected():
    """Test send when WebSocket is not connected."""
    channel = _make_channel()
    channel._ws = None

    msg = Message(
        id="1", type="req", channel_id="weibo", session_id="s1",
        params={"content": "test"}, timestamp=0, ok=True,
        req_method=ReqMethod.CHAT_SEND,
    )
    await channel.send(msg)  # Should not raise, just log warning


@pytest.mark.asyncio
async def test_send_empty_content():
    """Test send with empty content does not send."""
    channel = _make_channel()
    mock_ws = MagicMock()
    mock_ws.closed = False
    mock_ws.send_str = AsyncMock()
    channel._ws = mock_ws

    msg = Message(
        id="1", type="req", channel_id="weibo", session_id="s1",
        params={"content": ""}, timestamp=0, ok=True,
        req_method=ReqMethod.CHAT_SEND,
        metadata={"from_user_id": "user_001"},
    )
    await channel.send(msg)

    mock_ws.send_str.assert_not_called()


@pytest.mark.asyncio
async def test_send_missing_to_user_id():
    """Test send with missing to_user_id does not send."""
    channel = _make_channel()
    mock_ws = MagicMock()
    mock_ws.closed = False
    mock_ws.send_str = AsyncMock()
    channel._ws = mock_ws

    msg = Message(
        id="1", type="req", channel_id="weibo", session_id="unknown_session",
        params={"content": "hello"}, timestamp=0, ok=True,
        req_method=ReqMethod.CHAT_SEND,
        metadata={},
    )
    await channel.send(msg)

    mock_ws.send_str.assert_not_called()


# ---------------------------------------------------------------------------
# Test: get_metadata
# ---------------------------------------------------------------------------

def test_get_metadata():
    """Test get_metadata returns correct ChannelMetadata."""
    channel = _make_channel()
    meta = channel.get_metadata()

    assert meta.channel_id == "weibo"
    assert meta.source == "weibo"
    assert meta.extra["app_id"] == "test_app_id_123456"
    assert meta.extra["ws_endpoint"] == _DEFAULT_WS_ENDPOINT


# ---------------------------------------------------------------------------
# Test: Token Refresh
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_refresh_token_success():
    """Test successful token refresh."""
    channel = _make_channel()

    mock_session = MagicMock()
    mock_resp = MagicMock()
    mock_resp.json = AsyncMock(return_value={
        "code": 0,
        "data": {
            "token": "new_ws_token_abc",
            "expire_in": 3600,
        },
    })
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)
    mock_session.post = MagicMock(return_value=mock_resp)
    channel._session = mock_session

    await channel._refresh_token()

    assert channel.token == "new_ws_token_abc"
    assert channel.token_expire_at > 0


@pytest.mark.asyncio
async def test_refresh_token_not_expired():
    """Test token refresh skipped when token is still valid."""
    channel = _make_channel()
    import time as _time
    channel._token = "existing_token"
    channel._token_expire_at = _time.time() + 3600  # 1 hour from now

    mock_session = MagicMock()
    channel._session = mock_session

    await channel._refresh_token()

    # Should not make any HTTP call
    mock_session.post.assert_not_called()


@pytest.mark.asyncio
async def test_refresh_token_failure():
    """Test token refresh failure raises RuntimeError."""
    channel = _make_channel()

    mock_session = MagicMock()
    mock_resp = MagicMock()
    mock_resp.json = AsyncMock(return_value={
        "code": 1001,
        "message": "invalid credentials",
    })
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)
    mock_session.post = MagicMock(return_value=mock_resp)
    channel._session = mock_session

    with pytest.raises(RuntimeError, match="微博 token 获取失败"):
        await channel._refresh_token()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
