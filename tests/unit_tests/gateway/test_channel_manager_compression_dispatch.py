"""Integration tests for compaction notice on ChannelManager dispatch paths.

Exercises real ``_dispatch_robot_messages`` / ``dispatch_to_session`` wiring
with fakes — not formatter-only unit tests.
"""
from __future__ import annotations

import asyncio

import pytest

from jiuwenswarm.common.schema.message import EventType, Message
from jiuwenswarm.gateway.channel_manager.channel_manager import ChannelManager
from jiuwenswarm.gateway.routing.keys import AgentRef, RoutingKey, make_delivery_target
from jiuwenswarm.gateway.routing.session_sharing import (
    LogicalTarget,
    SessionSharingRegistry,
    SubRole,
)


class _FakeMessageHandler:
    """Minimal MessageHandler for dispatch-loop tests."""

    def __init__(self, registry: SessionSharingRegistry) -> None:
        self._session_sharing = registry
        self._robot_messages: asyncio.Queue[Message] = asyncio.Queue()
        self._last_originators: dict[str, tuple[str, str]] = {}

    def get_session_sharing_registry(self) -> SessionSharingRegistry:
        return self._session_sharing

    def get_session_last_originator(self, session_id: str | None) -> tuple[str, str] | None:
        if not session_id:
            return None
        return self._last_originators.get(str(session_id))

    async def consume_robot_messages(self, timeout: float | None = None) -> Message | None:
        try:
            if timeout is None:
                return await self._robot_messages.get()
            return await asyncio.wait_for(self._robot_messages.get(), timeout=timeout)
        except asyncio.TimeoutError:
            return None

    async def publish_robot_messages(self, msg: Message) -> None:
        await self._robot_messages.put(msg)

    def resolve_app_id(self, msg: Message) -> str | None:  # noqa: ARG002
        return "default"


class _FakeChannel:
    """Captures outbound ``send`` calls."""

    def __init__(self, channel_id: str, app_id: str = "default") -> None:
        self.channel_id = channel_id
        self.app_id = app_id
        self.sent: list[tuple[Message, object | None]] = []

    async def send(self, msg: Message, routing_target=None) -> None:
        self.sent.append((msg, routing_target))

    def on_message(self, callback) -> None:  # noqa: ARG002
        pass


async def _make_subscription(
    registry: SessionSharingRegistry, session_id: str, member_name: str, channel_id: str,
) -> None:
    rk = RoutingKey(
        user_id=f"u_{channel_id}",
        channel_id=channel_id,
        app_id="default",
        agent_ref=AgentRef("team", "default"),
        session_id=session_id,
    )
    dt = make_delivery_target(
        channel_id,
        chat_id=f"chat_{channel_id}",
        physical_user_id=f"u_{channel_id}",
        ws_id=f"ws_{channel_id}",
    )
    await registry.register(session_id, member_name, rk, dt)


def _compression_payload(**overrides) -> dict:
    payload = {
        "event_type": "context.compression_state",
        "status": "completed",
        "processor": "DialogueCompressor",
        "before": {"tokens": 100_000},
        "after": {"tokens": 25_000},
    }
    payload.update(overrides)
    return payload


def _compression_msg(
    channel_id: str,
    *,
    session_id: str = "s1",
    metadata: dict | None = None,
) -> Message:
    return Message(
        id="evt-1",
        type="event",
        channel_id=channel_id,
        session_id=session_id,
        params={},
        timestamp=0.0,
        ok=True,
        payload=_compression_payload(),
        metadata=dict(metadata or {}),
    )


async def _wait_until(predicate, *, timeout: float = 2.5, interval: float = 0.05) -> None:
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(interval)
    raise AssertionError("condition not met before timeout")


@pytest.mark.asyncio
async def test_dispatch_robot_messages_rewrites_compression_for_slack() -> None:
    handler = _FakeMessageHandler(SessionSharingRegistry())
    cm = ChannelManager(handler)
    slack = _FakeChannel("slack")
    cm.register_channel(slack)

    await handler.publish_robot_messages(_compression_msg("slack"))

    await cm.start_dispatch()
    try:
        await _wait_until(lambda: bool(slack.sent))

        assert len(slack.sent) == 1
        out, routing_target = slack.sent[0]
        assert routing_target is None
        assert out.event_type == EventType.CHAT_FINAL
        assert "Compacted" in out.payload["content"]
        assert out.payload["event_type"] == EventType.CHAT_FINAL.value
    finally:
        await cm.stop_dispatch()


@pytest.mark.asyncio
async def test_team_fan_out_rewrites_compression_for_feishu_when_origin_is_web() -> None:
    registry = SessionSharingRegistry()
    session_id = "s1"
    await _make_subscription(registry, session_id, SubRole.GODVIEW, "web")
    await _make_subscription(registry, session_id, SubRole.GODVIEW, "feishu")

    handler = _FakeMessageHandler(registry)
    cm = ChannelManager(handler)
    web = _FakeChannel("web")
    feishu = _FakeChannel("feishu")
    cm.register_channel(web)
    cm.register_channel(feishu)

    msg = _compression_msg(
        "web",
        session_id=session_id,
        metadata={"fan_out_targets": [LogicalTarget(intent="godview")]},
    )
    await handler.publish_robot_messages(msg)

    await cm.start_dispatch()
    try:
        await _wait_until(lambda: bool(feishu.sent))

        assert len(feishu.sent) == 1
        feishu_out, _ = feishu.sent[0]
        assert feishu_out.event_type == EventType.CHAT_FINAL
        assert "Compacted" in feishu_out.payload["content"]
        assert feishu_out.payload["event_type"] != "context.compression_state"

        assert len(web.sent) == 1
        web_out, _ = web.sent[0]
        assert web_out.payload.get("event_type") == "context.compression_state"
    finally:
        await cm.stop_dispatch()


@pytest.mark.asyncio
async def test_dispatch_rewrites_high_occupancy_started_for_im_keeps_web_raw() -> None:
    registry = SessionSharingRegistry()
    session_id = "s1"
    await _make_subscription(registry, session_id, SubRole.GODVIEW, "web")
    await _make_subscription(registry, session_id, SubRole.GODVIEW, "feishu")

    handler = _FakeMessageHandler(registry)
    cm = ChannelManager(handler)
    web = _FakeChannel("web")
    feishu = _FakeChannel("feishu")
    cm.register_channel(web)
    cm.register_channel(feishu)

    msg = Message(
        id="evt-started",
        type="event",
        channel_id="web",
        session_id=session_id,
        params={},
        timestamp=0.0,
        ok=True,
        payload={
            "event_type": "context.compression_state",
            "status": "started",
            "processor": "DialogueCompressor",
            "before": {"tokens": 100_000, "context_percent": 90},
        },
        metadata={"fan_out_targets": [LogicalTarget(intent="godview")]},
    )

    await handler.publish_robot_messages(msg)

    await cm.start_dispatch()
    try:
        await _wait_until(lambda: bool(feishu.sent) and bool(web.sent))

        assert len(feishu.sent) == 1
        feishu_out, _ = feishu.sent[0]
        assert feishu_out.event_type == EventType.CHAT_FINAL
        assert feishu_out.id == "evt-started-compaction-started"
        assert "compacting" in feishu_out.payload["content"].lower()
        assert "90%" in feishu_out.payload["content"]

        assert len(web.sent) == 1
        web_out, _ = web.sent[0]
        assert web_out.payload.get("event_type") == "context.compression_state"
        assert web_out.payload.get("status") == "started"
    finally:
        await cm.stop_dispatch()


@pytest.mark.asyncio
async def test_dispatch_keeps_raw_compression_event_for_acp_and_it_yields_a_session_update() -> None:
    """P2 fix: ACP is a rich renderer (event passes through unmodified), but
    unlike before that raw event must now turn into a real, non-final ACP
    session/update downstream instead of being silently dropped.
    """
    handler = _FakeMessageHandler(SessionSharingRegistry())
    cm = ChannelManager(handler)
    acp = _FakeChannel("acp")
    cm.register_channel(acp)

    msg = Message(
        id="evt-acp-1",
        type="event",
        channel_id="acp",
        session_id="s1",
        params={},
        timestamp=0.0,
        ok=True,
        payload=_compression_payload(),
        event_type=EventType.CONTEXT_COMPRESSION_STATE,
    )
    await handler.publish_robot_messages(msg)

    await cm.start_dispatch()
    try:
        await _wait_until(lambda: bool(acp.sent))

        assert len(acp.sent) == 1
        out, _ = acp.sent[0]
        # ACP keeps the raw event (it is a rich renderer) -- it must not be
        # downgraded into the IM CHAT_FINAL rewrite.
        assert out.event_type == EventType.CONTEXT_COMPRESSION_STATE
        assert out.payload.get("event_type") == "context.compression_state"

        from jiuwenswarm.common.e2a.acp.session_updates import build_acp_session_update

        class _State:
            assistant_message_id = None
            assistant_text = None
            thought_message_id = None
            thought_text = None
            user_message_id = None
            tool_call_cache: dict = {}

        update = build_acp_session_update(out, out.payload, _State())
        assert update is not None
        assert update["sessionUpdate"] == "agent_message_chunk"
        assert "75%" in update["content"]["text"]
    finally:
        await cm.stop_dispatch()


@pytest.mark.asyncio
async def test_dispatch_drops_started_without_occupancy_on_im() -> None:
    handler = _FakeMessageHandler(SessionSharingRegistry())
    cm = ChannelManager(handler)
    slack = _FakeChannel("slack")
    cm.register_channel(slack)

    msg = Message(
        id="evt-started",
        type="event",
        channel_id="slack",
        session_id="s1",
        params={},
        timestamp=0.0,
        ok=True,
        payload={
            "event_type": "context.compression_state",
            "status": "started",
            "processor": "DialogueCompressor",
            "before": {"tokens": 3146},
        },
    )
    await handler.publish_robot_messages(msg)

    await cm.start_dispatch()
    try:
        await asyncio.sleep(0.3)
        assert slack.sent == []
    finally:
        await cm.stop_dispatch()
