# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import asyncio
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


class _DeferredStartChannel(EnterpriseWebChannel):
    """Channel stub: start() blocks until stop_uplink_connect releases it."""

    def __init__(self) -> None:
        super().__init__(EnterpriseWebChannelConfig(enabled=True), _DummyBus())
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.start_call_count = 0

    async def start(self) -> None:
        self.start_call_count += 1
        self.started.set()
        await self.release.wait()


@pytest.mark.asyncio
async def test_start_uplink_connect_is_idempotent() -> None:
    channel = _DeferredStartChannel()
    channel.start_uplink_connect()
    await asyncio.wait_for(channel.started.wait(), timeout=1.0)
    assert channel.is_uplink_connect_running()

    channel.start_uplink_connect()
    assert channel.is_uplink_connect_running()
    assert channel.start_call_count == 1

    channel.release.set()
    await channel.stop_uplink_connect()
    assert not channel.is_uplink_connect_running()


@pytest.mark.asyncio
async def test_stop_uplink_connect_cancels_reconnect_loop() -> None:
    channel = _DeferredStartChannel()
    channel.start_uplink_connect()
    await asyncio.wait_for(channel.started.wait(), timeout=1.0)

    await channel.stop_uplink_connect()
    assert not channel.is_uplink_connect_running()
    assert channel.is_running is False
