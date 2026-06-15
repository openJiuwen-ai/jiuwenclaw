# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import json

import pytest

from jiuwenclaw.channel.enterprise_web_channel import (
    EnterpriseWebChannel,
    EnterpriseWebChannelConfig,
    _UplinkSocket,
)
from jiuwenclaw.schema.message import Message, EventType


class _DummyBus:
    async def publish_user_messages(self, msg: Message) -> None:
        pass


class _UplinkRawStub:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_uplink_raw(self, data: str) -> None:
        self.sent.append(data)


@pytest.mark.asyncio
async def test_uplink_socket_forwards_send() -> None:
    stub = _UplinkRawStub()
    shim = _UplinkSocket(stub)
    await shim.send('{"type":"res","id":"r1","ok":true,"payload":{}}')
    assert stub.sent == ['{"type":"res","id":"r1","ok":true,"payload":{}}']


class _RecordingEnterpriseWebChannel(EnterpriseWebChannel):
    def __init__(self, router: _DummyBus, sent: list[str]) -> None:
        super().__init__(EnterpriseWebChannelConfig(enabled=True), router)
        self._sent = sent
        self._uplink_ws = object()

    async def send_uplink_raw(self, data: str) -> None:
        self._sent.append(data)


@pytest.mark.asyncio
async def test_send_event_encodes_session_id() -> None:
    sent: list[str] = []
    channel = _RecordingEnterpriseWebChannel(_DummyBus(), sent)

    msg = Message(
        id="req-1",
        type="event",
        channel_id="web",
        session_id="sess_abc",
        params={},
        timestamp=0.0,
        ok=True,
        event_type=EventType.CHAT_DELTA,
        payload={"content": "hello"},
    )
    await channel.send(msg)
    assert sent
    frame = json.loads(sent[0])
    assert frame["type"] == "event"
    assert frame["payload"]["session_id"] == "sess_abc"
    assert frame["request_id"] == "req-1"
