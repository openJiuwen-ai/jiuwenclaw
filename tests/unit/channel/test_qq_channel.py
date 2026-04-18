# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
# pylint: disable=protected-access

"""Tests for QQChannel functionality."""

from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from jiuwenclaw.channel.qq_channel import QQChannel, QQChannelConfig
from jiuwenclaw.channel.base import RobotMessageRouter
from jiuwenclaw.schema.message import Message, ReqMethod


# ---------------------------------------------------------------------------
# Helper Functions
# ---------------------------------------------------------------------------

def _make_channel() -> QQChannel:
    """Create a QQChannel instance for testing."""
    config = QQChannelConfig(
        enabled=True,
        app_id="test_app_id_123456",
        app_secret="test_app_secret",
        allow_from=["user_001", "user_002"],
        enable_streaming=True,
        enable_guild=True,
        enable_group=True,
        enable_c2c=True,
    )
    router = MagicMock(spec=RobotMessageRouter)
    return QQChannel(config, router)


def _make_mock_guild_message(**kwargs) -> MagicMock:
    """Create a mock botpy guild message."""
    msg = MagicMock()
    msg.id = kwargs.get("id", "guild_msg_001")
    msg.guild_id = kwargs.get("guild_id", "guild_001")
    msg.channel_id = kwargs.get("channel_id", "channel_001")
    msg.author.id = kwargs.get("author_id", "user_001")
    msg.content = kwargs.get("content", "@bot 你好世界")
    msg.mentions = kwargs.get("mentions", [])
    return msg


def _make_mock_group_message(**kwargs) -> MagicMock:
    """Create a mock botpy group message."""
    msg = MagicMock()
    msg.msg_id = kwargs.get("msg_id", "group_msg_001")
    msg.group_openid = kwargs.get("group_openid", "group_openid_001")
    msg.author.member_openid = kwargs.get("member_openid", "user_001")
    msg.content = kwargs.get("content", "@bot 你好")
    return msg


def _make_mock_c2c_message(**kwargs) -> MagicMock:
    """Create a mock botpy C2C message."""
    msg = MagicMock()
    msg.id = kwargs.get("id", "c2c_msg_001")
    msg.author = kwargs.get("author", "user_001")
    msg.content = kwargs.get("content", "你好")
    msg.user_openid = kwargs.get("user_openid", "user_001")
    return msg


# ---------------------------------------------------------------------------
# Test: QQChannelConfig Defaults
# ---------------------------------------------------------------------------

def test_qq_config_defaults():
    """Test QQChannelConfig default values."""
    config = QQChannelConfig()

    assert config.enabled is False
    assert config.app_id == ""
    assert config.app_secret == ""
    assert config.allow_from == []
    assert config.enable_streaming is True
    assert config.enable_guild is True
    assert config.enable_group is True
    assert config.enable_c2c is True


def test_qq_config_custom():
    """Test QQChannelConfig with custom values."""
    config = QQChannelConfig(
        enabled=True,
        app_id="app123",
        app_secret="secret456",
        allow_from=["user1"],
        enable_streaming=False,
        enable_guild=False,
    )

    assert config.enabled is True
    assert config.app_id == "app123"
    assert config.app_secret == "secret456"
    assert config.allow_from == ["user1"]
    assert config.enable_streaming is False
    assert config.enable_guild is False


# ---------------------------------------------------------------------------
# Test: QQChannel Initialization
# ---------------------------------------------------------------------------

def test_qq_channel_init():
    """Test QQChannel initialization."""
    config = QQChannelConfig(enabled=True, app_id="app", app_secret="secret")
    router = MagicMock(spec=RobotMessageRouter)
    channel = QQChannel(config, router)

    assert channel.name == "qq"
    assert channel.channel_id == "qq"
    assert channel.config is config
    assert channel.is_running is False
    assert channel.message_callback is None
    assert channel.sessions == {}


def test_qq_channel_clients_empty():
    """Test QQChannel clients property returns empty set."""
    channel = _make_channel()
    assert channel.clients == set()


def test_qq_channel_on_message():
    """Test QQChannel on_message callback registration."""
    channel = _make_channel()
    cb = MagicMock()
    channel.on_message(cb)
    assert channel.message_callback is cb


# ---------------------------------------------------------------------------
# Test: _strip_at_mention
# ---------------------------------------------------------------------------

def test_strip_at_mention_basic():
    """Test basic @ mention stripping."""
    assert QQChannel.strip_at_mention("@bot 你好") == "bot 你好"


def test_strip_at_mention_with_mentions():
    """Test stripping with mention objects."""
    mention = MagicMock()
    mention.id = "12345"
    result = QQChannel.strip_at_mention("<@12345> 你好世界", [mention])
    assert result == "你好世界"


def test_strip_at_mention_no_at():
    """Test stripping when no @ prefix."""
    assert QQChannel.strip_at_mention("你好世界") == "你好世界"


def test_strip_at_mention_empty():
    """Test stripping empty string."""
    assert QQChannel.strip_at_mention("") == ""


def test_strip_at_mention_multiple_mentions():
    """Test stripping multiple mentions."""
    m1, m2 = MagicMock(), MagicMock()
    m1.id = "111"
    m2.id = "222"
    result = QQChannel.strip_at_mention("<@111> <@222> 你好", [m1, m2])
    assert result == "你好"


# ---------------------------------------------------------------------------
# Test: extract_content
# ---------------------------------------------------------------------------

def test_extract_content_from_params():
    """Test content extraction from params."""
    msg = Message(
        id="1", type="req", channel_id="qq", session_id="s1",
        params={"content": "hello"}, timestamp=0, ok=True,
        req_method=ReqMethod.CHAT_SEND,
    )
    assert QQChannel.extract_content(msg) == "hello"


def test_extract_content_from_payload():
    """Test content extraction from payload."""
    msg = Message(
        id="1", type="req", channel_id="qq", session_id="s1",
        params={}, timestamp=0, ok=True,
        req_method=ReqMethod.CHAT_SEND,
        payload={"content": "world"},
    )
    assert QQChannel.extract_content(msg) == "world"


def test_extract_content_dict_with_output():
    """Test content extraction when content is a dict with output key."""
    msg = Message(
        id="1", type="req", channel_id="qq", session_id="s1",
        params={"content": {"output": "extracted"}}, timestamp=0, ok=True,
        req_method=ReqMethod.CHAT_SEND,
    )
    assert QQChannel.extract_content(msg) == "extracted"


def test_extract_content_empty():
    """Test content extraction when no content available."""
    msg = Message(
        id="1", type="req", channel_id="qq", session_id="s1",
        params={}, timestamp=0, ok=True,
        req_method=ReqMethod.CHAT_SEND,
    )
    assert QQChannel.extract_content(msg) == ""


# ---------------------------------------------------------------------------
# Test: Guild Message Handling
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_handle_guild_message_allowed():
    """Test handling guild message from allowed user."""
    channel = _make_channel()
    dispatch_spy = AsyncMock()
    channel._dispatch = dispatch_spy

    mention = MagicMock()
    mention.id = "bot_id"
    msg = _make_mock_guild_message(
        content="<@bot_id> 你好世界",
        mentions=[mention],
        author_id="user_001",
    )

    await channel.handle_guild_message(msg)

    assert dispatch_spy.called
    dispatched_msg = dispatch_spy.call_args[0][0]
    assert dispatched_msg.params["content"] == "你好世界"
    assert dispatched_msg.metadata["qq_scene"] == "guild"
    assert dispatched_msg.metadata["guild_id"] == "guild_001"
    assert dispatched_msg.metadata["channel_id"] == "channel_001"
    assert dispatched_msg.metadata["user_id"] == "user_001"


@pytest.mark.asyncio
async def test_handle_guild_message_unauthorized():
    """Test guild message from unauthorized user is rejected."""
    channel = _make_channel()
    dispatch_spy = AsyncMock()
    channel._dispatch = dispatch_spy

    msg = _make_mock_guild_message(author_id="bad_user")

    await channel.handle_guild_message(msg)

    assert not dispatch_spy.called


@pytest.mark.asyncio
async def test_handle_guild_message_session_caching():
    """Test that guild sessions are cached."""
    channel = _make_channel()
    channel._dispatch = AsyncMock()

    msg = _make_mock_guild_message(content="hello")
    await channel.handle_guild_message(msg)

    session_key = "guild_guild_001_channel_001"
    assert session_key in channel.sessions
    assert channel.sessions[session_key] == "qq_guild_guild_001_channel_001"


# ---------------------------------------------------------------------------
# Test: Group Message Handling
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_handle_group_message_allowed():
    """Test handling group message from allowed user."""
    channel = _make_channel()
    channel._dispatch = AsyncMock()

    msg = _make_mock_group_message(
        content="@bot 测试群聊",
        member_openid="user_001",
    )

    await channel.handle_group_message(msg)

    channel._dispatch.assert_called_once()
    dispatched_msg = channel._dispatch.call_args[0][0]
    assert dispatched_msg.params["content"] == "bot 测试群聊"
    assert dispatched_msg.metadata["qq_scene"] == "group"
    assert dispatched_msg.metadata["group_openid"] == "group_openid_001"


@pytest.mark.asyncio
async def test_handle_group_message_unauthorized():
    """Test group message from unauthorized user is rejected."""
    channel = _make_channel()
    channel._dispatch = AsyncMock()

    msg = _make_mock_group_message(member_openid="bad_user")

    await channel.handle_group_message(msg)

    assert not channel._dispatch.called


@pytest.mark.asyncio
async def test_handle_group_message_session_caching():
    """Test that group sessions are cached."""
    channel = _make_channel()
    channel._dispatch = AsyncMock()

    msg = _make_mock_group_message()
    await channel.handle_group_message(msg)

    session_key = "group_group_openid_001"
    assert session_key in channel.sessions


# ---------------------------------------------------------------------------
# Test: C2C Message Handling
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_handle_c2c_message_allowed():
    """Test handling C2C message from allowed user."""
    channel = _make_channel()
    channel._dispatch = AsyncMock()

    msg = _make_mock_c2c_message(
        content="私信你好",
        author="user_001",
        user_openid="user_001",
    )

    await channel.handle_c2c_message(msg)

    channel._dispatch.assert_called_once()
    dispatched_msg = channel._dispatch.call_args[0][0]
    assert dispatched_msg.params["content"] == "私信你好"
    assert dispatched_msg.metadata["qq_scene"] == "c2c"
    assert dispatched_msg.metadata["user_openid"] == "user_001"


@pytest.mark.asyncio
async def test_handle_c2c_message_unauthorized():
    """Test C2C message from unauthorized user is rejected."""
    channel = _make_channel()
    channel._dispatch = AsyncMock()

    msg = _make_mock_c2c_message(author="bad_user", user_openid="bad_user")

    await channel.handle_c2c_message(msg)

    assert not channel._dispatch.called


@pytest.mark.asyncio
async def test_handle_c2c_message_session_caching():
    """Test that C2C sessions are cached."""
    channel = _make_channel()
    channel._dispatch = AsyncMock()

    msg = _make_mock_c2c_message()
    await channel.handle_c2c_message(msg)

    session_key = "c2c_user_001"
    assert session_key in channel.sessions


# ---------------------------------------------------------------------------
# Test: _dispatch
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_dispatch_with_callback():
    """Test dispatch with registered callback."""
    channel = _make_channel()
    cb = AsyncMock()
    channel.on_message(cb)

    msg = Message(
        id="1", type="req", channel_id="qq", session_id="s1",
        params={"content": "test"}, timestamp=0, ok=True,
        req_method=ReqMethod.CHAT_SEND,
    )
    await channel._dispatch(msg)

    cb.assert_called_once_with(msg)


@pytest.mark.asyncio
async def test_dispatch_without_callback():
    """Test dispatch without callback routes to bus."""
    channel = _make_channel()
    channel.bus.route_user_message = AsyncMock()

    msg = Message(
        id="1", type="req", channel_id="qq", session_id="s1",
        params={"content": "test"}, timestamp=0, ok=True,
        req_method=ReqMethod.CHAT_SEND,
    )
    await channel._dispatch(msg)

    channel.bus.route_user_message.assert_called_once_with(msg)


# ---------------------------------------------------------------------------
# Test: send
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_send_guild_message():
    """Test sending guild message."""
    channel = _make_channel()
    channel._running = True

    mock_api = AsyncMock()
    channel._botpy_client = MagicMock()
    channel._botpy_client.api = mock_api

    msg = Message(
        id="1", type="req", channel_id="qq", session_id="s1",
        params={"content": "回复内容"}, timestamp=0, ok=True,
        req_method=ReqMethod.CHAT_SEND,
        metadata={"qq_scene": "guild", "channel_id": "ch_001"},
    )
    await channel.send(msg)

    mock_api.post_message.assert_called_once_with(
        channel_id="ch_001",
        content="回复内容",
    )


@pytest.mark.asyncio
async def test_send_group_message():
    """Test sending group message."""
    channel = _make_channel()
    channel._running = True

    mock_api = AsyncMock()
    channel._botpy_client = MagicMock()
    channel._botpy_client.api = mock_api

    msg = Message(
        id="1", type="req", channel_id="qq", session_id="s1",
        params={"content": "群回复"}, timestamp=0, ok=True,
        req_method=ReqMethod.CHAT_SEND,
        metadata={"qq_scene": "group", "group_openid": "grp_001"},
    )
    await channel.send(msg)

    mock_api.post_group_message.assert_called_once()


@pytest.mark.asyncio
async def test_send_c2c_message():
    """Test sending C2C message."""
    channel = _make_channel()
    channel._running = True

    mock_api = AsyncMock()
    channel._botpy_client = MagicMock()
    channel._botpy_client.api = mock_api

    msg = Message(
        id="1", type="req", channel_id="qq", session_id="s1",
        params={"content": "私信回复"}, timestamp=0, ok=True,
        req_method=ReqMethod.CHAT_SEND,
        metadata={"qq_scene": "c2c", "user_openid": "u_001"},
    )
    await channel.send(msg)

    mock_api.post_c2c_message.assert_called_once()


@pytest.mark.asyncio
async def test_send_not_running():
    """Test send when channel is not running."""
    channel = _make_channel()
    channel._running = False

    msg = Message(
        id="1", type="req", channel_id="qq", session_id="s1",
        params={"content": "test"}, timestamp=0, ok=True,
        req_method=ReqMethod.CHAT_SEND,
    )
    await channel.send(msg)  # Should not raise, just log warning


@pytest.mark.asyncio
async def test_send_empty_content():
    """Test send with empty content."""
    channel = _make_channel()
    channel._running = True

    mock_api = AsyncMock()
    channel._botpy_client = MagicMock()
    channel._botpy_client.api = mock_api

    msg = Message(
        id="1", type="req", channel_id="qq", session_id="s1",
        params={"content": ""}, timestamp=0, ok=True,
        req_method=ReqMethod.CHAT_SEND,
        metadata={"qq_scene": "guild", "channel_id": "ch_001"},
    )
    await channel.send(msg)

    mock_api.post_message.assert_not_called()


# ---------------------------------------------------------------------------
# Test: get_metadata
# ---------------------------------------------------------------------------

def test_get_metadata():
    """Test get_metadata returns correct ChannelMetadata."""
    channel = _make_channel()
    meta = channel.get_metadata()

    assert meta.channel_id == "qq"
    assert meta.source == "qq"
    assert meta.extra["app_id"] == "test_app_id_123456"
    assert meta.extra["enable_guild"] is True
    assert meta.extra["enable_group"] is True
    assert meta.extra["enable_c2c"] is True


# ---------------------------------------------------------------------------
# Test: Scene Toggle Configuration
# ---------------------------------------------------------------------------

def test_scene_toggles_reflected_in_config():
    """Test that scene toggle flags are stored in config correctly."""
    config = QQChannelConfig(
        enabled=True, app_id="app", app_secret="secret",
        enable_guild=False, enable_group=False, enable_c2c=True,
    )
    assert config.enable_guild is False
    assert config.enable_group is False
    assert config.enable_c2c is True


@pytest.mark.asyncio
async def test_handle_guild_message_dispatches_regardless_of_config():
    """Test that handle_guild_message itself does not check enable_guild.

    The enable_guild check is in _QQBotpyClient.on_at_message_create,
    not in handle_guild_message. This test confirms the handler logic
    is independent of the scene toggle.
    """
    config = QQChannelConfig(
        enabled=True, app_id="app", app_secret="secret",
        enable_guild=False,
    )
    router = MagicMock(spec=RobotMessageRouter)
    channel = QQChannel(config, router)
    channel._dispatch = AsyncMock()

    msg = _make_mock_guild_message(content="hello")
    await channel.handle_guild_message(msg)

    # handle_guild_message itself does NOT check enable_guild
    channel._dispatch.assert_called_once()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
