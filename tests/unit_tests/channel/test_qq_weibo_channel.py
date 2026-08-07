import json
import time

import pytest

from jiuwenswarm.common.schema.message import EventType, Message
from jiuwenswarm.gateway.channel_manager.base import RobotMessageRouter
from jiuwenswarm.gateway.channel_manager.im_platforms.qq.qq_connect import QQChannel, QQChannelConfig
from jiuwenswarm.gateway.channel_manager.im_platforms.weibo.weibo_connect import WeiboChannel, WeiboChannelConfig


class _C2CMessage:
    id = "qq-message-1"
    content = "hello qq"

    class Author:
        user_openid = "qq-user"

    author = Author()


class _WebSocket:
    closed = False

    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_str(self, value: str) -> None:
        self.sent.append(value)


class _QQApi:
    def __init__(self) -> None:
        self.c2c_messages: list[dict] = []
        self.group_messages: list[dict] = []

    async def post_c2c_message(self, **kwargs):
        self.c2c_messages.append(kwargs)

    async def post_group_message(self, **kwargs):
        self.group_messages.append(kwargs)


class _QQClient:
    def __init__(self) -> None:
        self.api = _QQApi()


def _setup_qq_channel_for_send(channel: QQChannel, client: _QQClient) -> None:
    """Set up a QQChannel instance with a mock client for outbound tests."""
    channel.set_test_client(client)


def _setup_weibo_channel_ws(channel: WeiboChannel, ws: _WebSocket) -> None:
    """Set up a WeiboChannel instance with a mock WebSocket for outbound tests."""
    channel.set_test_ws(ws)


@pytest.mark.asyncio
async def test_qq_c2c_message_uses_standard_message_callback():
    channel = QQChannel(QQChannelConfig(enabled=True), RobotMessageRouter())
    received = []
    channel.on_message(received.append)

    await channel.handle_c2c_message(_C2CMessage())

    assert len(received) == 1
    assert received[0].channel_id == "qq"
    assert received[0].session_id == "qq_c2c:qq-user"
    assert received[0].params["content"] == "hello qq"
    assert received[0].metadata["user_openid"] == "qq-user"
    assert received[0].is_stream is True


def test_qq_extracts_output_payload_for_im_reply():
    msg = Message(
        id="reply-qq",
        type="res",
        channel_id="qq",
        session_id="qq_c2c:qq-user",
        params={},
        payload={"result": {"output": "reply from result"}},
        timestamp=time.time(),
        ok=True,
    )

    assert QQChannel.extract_content(msg) == "reply from result"


def test_qq_interrupt_reply_is_human_readable_and_not_tool_authorization():
    msg = Message(
        id="reply-qq-interrupt",
        type="res",
        channel_id="qq",
        session_id="qq_c2c:qq-user",
        params={},
        payload={
            "content": {
                "result_type": "interrupt",
                "state": [
                    {
                        "payload": {
                            "value": {
                                "message": "需要在网页端授权",
                            },
                        },
                    },
                ],
            },
        },
        timestamp=time.time(),
        ok=True,
    )

    assert QQChannel.extract_content(msg) == "正在继续处理，请稍后。"


@pytest.mark.asyncio
async def test_qq_outbound_sends_native_markdown_to_botpy():
    channel = QQChannel(QQChannelConfig(enabled=True), RobotMessageRouter())
    client = _QQClient()
    _setup_qq_channel_for_send(channel, client)

    await channel.send(
        Message(
            id="reply-qq",
            type="res",
            channel_id="qq",
            session_id="qq_c2c:qq-user",
            params={},
            payload={"content": "## 标题\n\n**重点**"},
            timestamp=time.time(),
            ok=True,
            metadata={"qq_scene": "c2c", "user_openid": "qq-user", "message_id": "inbound-1"},
        )
    )

    sent = client.api.c2c_messages[0]
    assert sent["msg_type"] == 2
    assert sent["markdown"]["content"] == "## 标题\n\n**重点**"
    assert sent["msg_id"] == "inbound-1"


@pytest.mark.asyncio
async def test_qq_outbound_skips_intermediate_events():
    channel = QQChannel(QQChannelConfig(enabled=True), RobotMessageRouter())
    client = _QQClient()
    _setup_qq_channel_for_send(channel, client)

    await channel.send(
        Message(
            id="reply-qq-tool",
            type="res",
            channel_id="qq",
            session_id="qq_c2c:qq-user",
            params={},
            payload={"event_type": "chat.tool_call", "content": {"name": "read_file"}},
            event_type=EventType.CHAT_TOOL_CALL,
            timestamp=time.time(),
            ok=True,
            metadata={"qq_scene": "c2c", "user_openid": "qq-user"},
        )
    )

    assert client.api.c2c_messages == []


@pytest.mark.asyncio
async def test_weibo_inbound_and_outbound_use_direct_message_metadata():
    channel = WeiboChannel(WeiboChannelConfig(enabled=True), RobotMessageRouter())
    received = []
    channel.on_message(received.append)

    await channel.handle_inbound_message(
        {"fromUserId": "weibo-user", "messageId": "wb-1", "text": "hello weibo"}
    )
    assert len(received) == 1
    assert received[0].session_id == "weibo_dm_weibo-user"
    assert received[0].is_stream is True

    ws = _WebSocket()
    _setup_weibo_channel_ws(channel, ws)
    await channel.send(
        Message(
            id="reply-1",
            type="res",
            channel_id="weibo",
            session_id="weibo_dm_weibo-user",
            params={},
            payload={"content": "reply text"},
            timestamp=time.time(),
            ok=True,
            metadata=received[0].metadata,
        )
    )

    sent = json.loads(ws.sent[0])
    assert sent["payload"]["toUserId"] == "weibo-user"
    assert sent["payload"]["text"] == "reply text"


@pytest.mark.asyncio
async def test_weibo_outbound_uses_output_payload():
    channel = WeiboChannel(WeiboChannelConfig(enabled=True), RobotMessageRouter())
    ws = _WebSocket()
    _setup_weibo_channel_ws(channel, ws)

    await channel.send(
        Message(
            id="reply-output",
            type="res",
            channel_id="weibo",
            session_id="weibo_dm_weibo-user",
            params={},
            payload={"output": "reply from output"},
            timestamp=time.time(),
            ok=True,
            metadata={"from_user_id": "weibo-user"},
        )
    )

    sent = json.loads(ws.sent[0])
    assert sent["payload"]["text"] == "reply from output"


@pytest.mark.asyncio
async def test_weibo_outbound_keeps_interrupt_human_readable():
    channel = WeiboChannel(WeiboChannelConfig(enabled=True), RobotMessageRouter())
    ws = _WebSocket()
    _setup_weibo_channel_ws(channel, ws)

    await channel.send(
        Message(
            id="reply-interrupt",
            type="res",
            channel_id="weibo",
            session_id="weibo_dm_weibo-user",
            params={},
            payload={
                "content": {
                    "result_type": "interrupt",
                    "state": [
                        {
                            "payload": {
                                "value": {
                                    "message": "需要在网页端授权",
                                },
                            },
                        },
                    ],
                },
            },
            timestamp=time.time(),
            ok=True,
            metadata={"from_user_id": "weibo-user"},
        )
    )

    sent = json.loads(ws.sent[0])
    assert sent["payload"]["text"] == "正在继续处理，请稍后。"


@pytest.mark.asyncio
async def test_weibo_outbound_skips_intermediate_events():
    channel = WeiboChannel(WeiboChannelConfig(enabled=True), RobotMessageRouter())
    ws = _WebSocket()
    _setup_weibo_channel_ws(channel, ws)

    await channel.send(
        Message(
            id="reply-tool",
            type="res",
            channel_id="weibo",
            session_id="weibo_dm_weibo-user",
            params={},
            payload={"event_type": "chat.tool_call", "content": {"name": "read_file"}},
            event_type=EventType.CHAT_TOOL_CALL,
            timestamp=time.time(),
            ok=True,
            metadata={"from_user_id": "weibo-user"},
        )
    )

    assert ws.sent == []
