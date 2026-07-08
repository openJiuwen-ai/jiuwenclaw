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
