import asyncio
import json

import pytest

from jiuwenswarm.common.schema.message import EventType, Message
from jiuwenswarm.gateway.channel_manager.base import RobotMessageRouter
from jiuwenswarm.gateway.channel_manager.web.web_connect import (
    WebChannel,
    WebChannelConfig,
)
from jiuwenswarm.gateway.routing.keys import RoutingKey
from jiuwenswarm.gateway.routing.session_sharing import RoutingTarget


class _FakeClient:
    def __init__(self):
        self.frames = []
        self.closed = False
        self.remote_address = ("127.0.0.1", 12345)

    async def send(self, data):
        self.frames.append(json.loads(data))


@pytest.mark.asyncio
async def test_web_channel_preserves_symphony_status_payload():
    channel = WebChannel(WebChannelConfig(enabled=True), RobotMessageRouter())
    client = _FakeClient()
    routing_key = RoutingKey(
        channel_id="web",
        app_id="default",
        user_id="test_user",
        session_id="sess-1",
        agent_ref=None,
    )

    msg = Message(
        id="req-1",
        type="event",
        channel_id="web",
        session_id="sess-1",
        params={},
        timestamp=0.0,
        ok=True,
        payload={
            "source": "symphony_compose_score",
            "operation_id": "call-1",
            "phase": "checking_score",
            "content": "Symphony status",
            "status": "in_progress",
        },
        event_type=EventType.CHAT_SYMPHONY_STATUS,
    )

    # 创建 RoutingTarget 包含 routing_keys
    routing_target = RoutingTarget(
        intent="godview",  # 必需参数
        routing_keys=[routing_key],
        member_names=(),
    )

    # 走真实 _register 建 ws 映射 + 起 per-ws writer（send 现在是非阻塞入队）
    await channel.register_ws(client, routing_key)
    try:
        await channel.send(msg, routing_target=routing_target)
        # writer 异步送出，flush 一下再断言
        for _ in range(20):
            if client.frames:
                break
            await asyncio.sleep(0.005)
        assert client.frames == [
            {
                "type": "event",
                "event": "chat.symphony_status",
                "payload": {
                    "source": "symphony_compose_score",
                    "operation_id": "call-1",
                    "phase": "checking_score",
                    "content": "Symphony status",
                    "status": "in_progress",
                    "session_id": "sess-1",
                },
            }
        ]
    finally:
        await channel.unregister_ws(client)


@pytest.mark.asyncio
async def test_web_channel_chat_send_ack_before_forward_callback_finishes():
    channel = WebChannel(WebChannelConfig(enabled=True), RobotMessageRouter())
    client = _FakeClient()
    callback_started = asyncio.Event()
    release_callback = asyncio.Event()

    async def chat_send_ack(ws, req_id, params, session_id):
        await channel.send_response(
            ws,
            req_id,
            ok=True,
            payload={"accepted": True, "session_id": session_id},
        )

    async def slow_forward_callback(msg):
        callback_started.set()
        await release_callback.wait()
        return True

    channel.register_method("chat.send", chat_send_ack)
    channel.on_message(slow_forward_callback)

    raw = json.dumps(
        {
            "type": "req",
            "id": "req-chat",
            "method": "chat.send",
            "params": {"session_id": "sess-chat", "content": "hello"},
        }
    )
    task = asyncio.create_task(channel._handle_raw_message(client, raw, {}))
    try:
        await asyncio.wait_for(callback_started.wait(), timeout=1)
        assert client.frames == [
            {
                "type": "res",
                "id": "req-chat",
                "ok": True,
                "payload": {"accepted": True, "session_id": "sess-chat"},
            }
        ]
    finally:
        release_callback.set()
        await task
        await channel.unregister_ws(client)


@pytest.mark.asyncio
async def test_web_channel_routes_rpc_response_by_request_ws_id():
    channel = WebChannel(WebChannelConfig(enabled=True), RobotMessageRouter())
    client = _FakeClient()
    other_client = _FakeClient()
    routing_key = RoutingKey(
        channel_id="web",
        app_id="default",
        user_id="test_user",
        session_id="sess-real",
        agent_ref=None,
    )
    other_routing_key = RoutingKey(
        channel_id="web",
        app_id="default",
        user_id="other_user",
        session_id="sess-other",
        agent_ref=None,
    )

    await channel.register_ws(client, routing_key)
    await channel.register_ws(other_client, other_routing_key)
    try:
        msg = Message(
            id="req-graph",
            type="res",
            channel_id="web",
            session_id="sess-temp",
            params={},
            timestamp=0.0,
            ok=True,
            payload={"success": True},
            metadata={"ws_id": getattr(client, "_jiuwen_ws_id", "")},
        )

        await channel.send(msg)
        for _ in range(20):
            if client.frames:
                break
            await asyncio.sleep(0.005)

        assert client.frames == [
            {
                "type": "res",
                "id": "req-graph",
                "ok": True,
                "payload": {"success": True},
            }
        ]
        assert other_client.frames == []
    finally:
        await channel.unregister_ws(client)
        await channel.unregister_ws(other_client)
