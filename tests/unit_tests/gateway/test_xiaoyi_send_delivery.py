# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""``XiaoyiChannel.send()`` must report success only when it actually delivered.

Team fan-out (``deliver_with_ask_user_fallback`` /
``_channel_send_delivered``) registers a pending ``ask_user`` question only
when ``channel.send`` returns ``True``. ``_send_team`` had branches --
invalid delivery target, a filtered event, and no ws/push/legacy channel
available -- that logged a drop and sent nothing, yet ``send()`` always
returned ``True`` for the team path regardless of what ``_send_team`` did.
A pending question registered for a notice the human never received leaves
the ask_user tool call blocked with no way to answer it.
"""

from __future__ import annotations

import pytest

from jiuwenswarm.common.schema import Message
from jiuwenswarm.common.schema.message import EventType
from jiuwenswarm.gateway.channel_manager.im_platforms.xiaoyi.xiaoyi_connect import (
    XiaoyiChannel,
    XiaoyiChannelConfig,
)
from jiuwenswarm.gateway.channel_manager.base import RobotMessageRouter
from jiuwenswarm.gateway.routing.keys import XiaoyiDeliveryTarget
from jiuwenswarm.gateway.routing.session_sharing import RoutingTarget


def _channel() -> XiaoyiChannel:
    return XiaoyiChannel(XiaoyiChannelConfig(enabled=True, channel_id="xiaoyi"), RobotMessageRouter())


def _final_msg(content: str = "the answer") -> Message:
    return Message(
        id="msg-1",
        type="res",
        channel_id="xiaoyi",
        session_id="s1",
        params={},
        timestamp=0.0,
        ok=True,
        event_type=EventType.CHAT_FINAL,
        payload={"event_type": "chat.final", "content": content},
    )


def _routing_target(delivery) -> RoutingTarget:
    return RoutingTarget(intent="private", delivery=delivery)


# ------------------------------------------------------- the drop that lied


@pytest.mark.asyncio
async def test_send_reports_failure_when_no_ws_push_or_legacy_channel_exists() -> None:
    """The exact branch named in the bug report: '_send_team drop: no ws/push/legacy'."""
    channel = _channel()
    # No push_id, no active ws mapping for this agent_id, and no ws connections
    # at all -- _send_team must fall into its final "drop" branch.
    delivery = XiaoyiDeliveryTarget(agent_id="agent-1", push_id="")

    delivered = await channel.send(_final_msg(), routing_target=_routing_target(delivery))

    assert delivered is False


@pytest.mark.asyncio
async def test_send_reports_failure_for_an_invalid_delivery_target() -> None:
    channel = _channel()

    delivered = await channel.send(_final_msg(), routing_target=_routing_target(None))

    assert delivered is False


@pytest.mark.asyncio
async def test_send_reports_failure_for_a_filtered_intermediate_event() -> None:
    """CHAT_DELTA is intentionally dropped for team members; that is not a delivery."""
    channel = _channel()
    delivery = XiaoyiDeliveryTarget(agent_id="agent-1", push_id="push-1")
    msg = Message(
        id="msg-delta",
        type="res",
        channel_id="xiaoyi",
        session_id="s1",
        params={},
        timestamp=0.0,
        ok=True,
        event_type=EventType.CHAT_DELTA,
        payload={"event_type": "chat.delta", "delta": "partial"},
    )

    delivered = await channel.send(msg, routing_target=_routing_target(delivery))

    assert delivered is False


# --------------------------------------------------------- the real deliveries


@pytest.mark.asyncio
async def test_send_reports_success_when_a_push_is_actually_sent(monkeypatch) -> None:
    channel = _channel()
    sent: list[tuple[str, str, str]] = []

    async def _fake_push(agent_id: str, push_id: str, content: str) -> None:
        sent.append((agent_id, push_id, content))

    monkeypatch.setattr(channel, "_send_push_to_user", _fake_push)
    delivery = XiaoyiDeliveryTarget(agent_id="agent-1", push_id="push-1")

    delivered = await channel.send(_final_msg("hello"), routing_target=_routing_target(delivery))

    assert delivered is True
    assert sent == [("agent-1", "push-1", "hello")]


@pytest.mark.asyncio
async def test_send_reports_success_when_ws_is_active(monkeypatch) -> None:
    channel = _channel()
    channel._ws_connections["ws_url1"] = object()
    channel._active_push_sessions["agent-1"] = ("xy-session", "xy-task", "push-1", __import__("time").time())
    sent: list[tuple[str, str]] = []

    async def _fake_ws(session_id, task_id, msg, content):
        sent.append((session_id, task_id))

    monkeypatch.setattr(channel, "_send_ws_to_user", _fake_ws)
    delivery = XiaoyiDeliveryTarget(agent_id="agent-1", push_id="push-1")

    delivered = await channel.send(_final_msg("hi"), routing_target=_routing_target(delivery))

    assert delivered is True
    assert sent == [("xy-session", "xy-task")]


@pytest.mark.asyncio
async def test_send_reports_success_via_legacy_fallback(monkeypatch) -> None:
    """No push_id, no active session, but a ws connection exists -- legacy fallback."""
    channel = _channel()
    channel._ws_connections["ws_url1"] = object()
    sent: list[Message] = []

    async def _fake_legacy(msg: Message) -> None:
        sent.append(msg)

    monkeypatch.setattr(channel, "_send_legacy", _fake_legacy)
    delivery = XiaoyiDeliveryTarget(agent_id="agent-1", push_id="")

    msg = _final_msg("legacy path")
    delivered = await channel.send(msg, routing_target=_routing_target(delivery))

    assert delivered is True
    assert sent == [msg]


# ------------------------------------------------------- non-team path unchanged


@pytest.mark.asyncio
async def test_non_team_send_still_returns_false_without_a_ws_connection() -> None:
    channel = _channel()
    assert await channel.send(_final_msg()) is False


@pytest.mark.asyncio
async def test_non_team_send_still_returns_true_with_a_ws_connection(monkeypatch) -> None:
    channel = _channel()
    channel._ws_connections["ws_url1"] = object()

    async def _fake_legacy(msg: Message) -> None:
        return None

    monkeypatch.setattr(channel, "_send_legacy", _fake_legacy)

    assert await channel.send(_final_msg()) is True
