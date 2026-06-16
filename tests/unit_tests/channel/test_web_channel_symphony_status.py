import json

import pytest

from jiuwenswarm.common.schema.message import EventType, Message
from jiuwenswarm.gateway.channel_manager.base import RobotMessageRouter
from jiuwenswarm.gateway.channel_manager.web.web_connect import (
    WebChannel,
    WebChannelConfig,
)


class _FakeClient:
    def __init__(self):
        self.frames = []

    async def send(self, data):
        self.frames.append(json.loads(data))


@pytest.mark.asyncio
async def test_web_channel_preserves_symphony_status_payload():
    channel = WebChannel(WebChannelConfig(enabled=True), RobotMessageRouter())
    client = _FakeClient()
    getattr(channel, "_clients").add(client)

    await channel.send(
        Message(
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
    )

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
