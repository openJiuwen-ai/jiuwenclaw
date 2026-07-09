import asyncio
import time
from dataclasses import dataclass

import pytest

from jiuwenswarm.common.schema.message import EventType, Message
from jiuwenswarm.gateway.channel_manager.base import BaseChannel, RobotMessageRouter
from jiuwenswarm.gateway.channel_manager.channel_manager import ChannelManager
from jiuwenswarm.gateway.channel_manager.im_platforms.wechat.login_service import WechatLoginService
from jiuwenswarm.gateway.channel_manager.spec import (
    ChannelCapabilities,
    ChannelConfigError,
    ChannelSpec,
    require_fields,
)
from jiuwenswarm.gateway.im_pipeline.im_outbound import IMOutboundPipeline
from jiuwenswarm.gateway.im_pipeline.outbound_artifacts import outbound_artifact_store


@dataclass
class _Config:
    enabled: bool = False
    token: str = ""


class _Channel(BaseChannel):
    name = "test"

    @property
    def channel_id(self) -> str:
        return self.name

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def send(self, msg: Message) -> None:
        return None

    def on_message(self, callback) -> None:
        self._callback = callback


def _spec() -> ChannelSpec:
    return ChannelSpec(
        channel_id="test",
        config_model=_Config,
        factory=lambda raw: _Channel(_Config(**raw), RobotMessageRouter()),
        capabilities=ChannelCapabilities(streaming=True),
        validator=require_fields("token"),
    )


@pytest.mark.asyncio
async def test_channel_manager_validates_and_rolls_back_failed_runtime_update():
    manager = ChannelManager(object(), config={"test": {"enabled": False}})
    manager.register_spec(_spec())
    persisted = []
    manager.set_config_persister(lambda channel_id, conf: persisted.append((channel_id, conf)))

    async def apply(config):
        manager.unregister_channel("test")
        raw = config.get("test", {})
        if raw.get("enabled"):
            channel = manager.build_channel("test", raw)
            manager.register_channel(channel)
            await channel.start()

    manager.set_config_callback(apply)

    with pytest.raises(ChannelConfigError):
        await manager.set_conf("test", {"enabled": True})
    assert manager.get_conf("test") == {"enabled": False}

    await manager.set_conf("test", {"enabled": True, "token": "ok"})
    assert manager.get_channel("test").is_running
    assert persisted[-1][0] == "test"


@pytest.mark.asyncio
async def test_channel_manager_serializes_config_updates():
    manager = ChannelManager(object(), config={"test": {"enabled": False}})
    manager.register_spec(_spec())
    active = 0
    max_active = 0

    async def apply(config):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.02)
        raw = config["test"]
        manager.unregister_channel("test")
        channel = manager.build_channel("test", raw)
        manager.register_channel(channel)
        await channel.start()
        active -= 1

    manager.set_config_callback(apply)
    await asyncio.gather(
        manager.set_conf("test", {"enabled": True, "token": "one"}),
        manager.set_conf("test", {"enabled": True, "token": "two"}),
    )
    assert max_active == 1
    assert manager.get_conf("test")["token"] == "two"


@pytest.mark.asyncio
async def test_wechat_login_operations_are_isolated_by_requester():
    service = WechatLoginService()
    first = await service.begin(requester_channel_id="qq", requester_session_id="qq-1")
    await service.update(phase="awaiting_scan", qr={"kind": "text", "value": "first"})
    second = await service.begin(requester_channel_id="weibo", requester_session_id="wb-1")
    await service.update(phase="awaiting_scan", qr={"kind": "text", "value": "second"})

    assert (await service.snapshot(first))["qr"]["value"] == "first"
    assert (await service.snapshot(second))["qr"]["value"] == "second"
    assert await service.find_operation_id("qq", "qq-1") == first


@pytest.mark.asyncio
async def test_outbound_pipeline_consumes_only_matching_session_artifact():
    pipeline = IMOutboundPipeline()
    await outbound_artifact_store.register(
        "qq",
        "session-a",
        {"text": "请扫码", "artifacts": [{"kind": "link", "url": "https://example.test/qr"}]},
    )
    other = Message(
        id="other",
        type="event",
        channel_id="qq",
        session_id="session-b",
        params={},
        payload={"event_type": "chat.final", "content": "普通回复"},
        event_type=EventType.CHAT_FINAL,
        timestamp=time.time(),
        ok=True,
    )
    matching = Message(
        id="matching",
        type="event",
        channel_id="qq",
        session_id="session-a",
        params={},
        payload={"event_type": "chat.final", "content": "模型回复"},
        event_type=EventType.CHAT_FINAL,
        timestamp=time.time(),
        ok=True,
    )

    await pipeline.apply(other)
    await pipeline.apply(matching)

    assert other.payload["content"] == "普通回复"
    assert matching.payload["content"] == "请扫码"
    assert matching.metadata["outbound_delivery"]["artifacts"][0]["kind"] == "link"
