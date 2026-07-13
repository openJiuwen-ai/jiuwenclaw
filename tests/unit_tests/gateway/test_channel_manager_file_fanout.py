"""Unit tests for cross-channel file delivery fan_out injection.

Covers ``ChannelManager._inject_file_delivery_fanout`` and
``SendFileToolkit._normalize_target_channels`` — the core of the fix that
makes ``send_file_to_user`` route files to all channels joined to a team
session (e.g. Feishu) instead of only the originating channel.
"""
from __future__ import annotations

import pytest

from jiuwenswarm.agents.harness.common.tools.send_file_to_user import SendFileToolkit
from jiuwenswarm.common.schema.message import EventType, Message
from jiuwenswarm.gateway.channel_manager.channel_manager import ChannelManager
from jiuwenswarm.gateway.routing.keys import AgentRef, RoutingKey, make_delivery_target
from jiuwenswarm.gateway.routing.session_sharing import SessionSharingRegistry, SubRole


class _FakeMessageHandler:
    """Minimal MessageHandler exposing only the registry accessor."""

    def __init__(self, registry: SessionSharingRegistry) -> None:
        self._session_sharing = registry

    def get_session_sharing_registry(self) -> SessionSharingRegistry:
        return self._session_sharing


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
    dt = make_delivery_target(channel_id, chat_id=f"chat_{channel_id}", physical_user_id=f"u_{channel_id}")
    await registry.register(session_id, member_name, rk, dt)


def _make_channel_manager(registry: SessionSharingRegistry) -> ChannelManager:
    return ChannelManager(_FakeMessageHandler(registry))


def _make_file_msg(session_id: str, channel_id: str = "web", metadata: dict | None = None) -> Message:
    return Message(
        id="req-1",
        type="event",
        channel_id=channel_id,
        session_id=session_id,
        params={},
        timestamp=0.0,
        ok=True,
        payload={"event_type": "chat.file", "files": [{"path": "/tmp/a.txt", "name": "a.txt"}]},
        event_type=EventType.CHAT_FILE,
        metadata=metadata if metadata is not None else {},
    )


# ---------- _normalize_target_channels ----------


@pytest.mark.parametrize(
    "raw,expected",
    [
        (None, []),
        ("", []),
        ("feishu", ["feishu"]),
        ('["feishu","web"]', ["feishu", "web"]),
        (["feishu", "web"], ["feishu", "web"]),
        ([" feishu ", "", "web"], ["feishu", "web"]),
        ("reviewer-1", ["reviewer-1"]),
    ],
)
def test_normalize_target_channels(raw, expected):
    assert SendFileToolkit._normalize_target_channels(raw) == expected


# ---------- _inject_file_delivery_fanout ----------


async def test_inject_returns_none_for_non_file_event():
    reg = SessionSharingRegistry()
    cm = _make_channel_manager(reg)
    msg = _make_file_msg("s1")
    result = await cm._inject_file_delivery_fanout(msg, "chat.final")
    assert result is None
    assert "fan_out_targets" not in (msg.metadata or {})


async def test_inject_returns_none_when_registry_empty():
    reg = SessionSharingRegistry()
    cm = _make_channel_manager(reg)
    msg = _make_file_msg("s1")
    result = await cm._inject_file_delivery_fanout(msg, "chat.file")
    assert result is None
    # 纯 web 会话（无订阅）保持单 channel 兜底
    assert "fan_out_targets" not in (msg.metadata or {})


async def test_inject_auto_targets_godview_and_all_members():
    reg = SessionSharingRegistry()
    await _make_subscription(reg, "s1", SubRole.GODVIEW, "web")
    await _make_subscription(reg, "s1", "reviewer-1", "feishu")
    await _make_subscription(reg, "s1", "reviewer-2", "xiaoyi")
    cm = _make_channel_manager(reg)
    msg = _make_file_msg("s1", channel_id="web")
    result = await cm._inject_file_delivery_fanout(msg, "chat.file")
    # godview 覆盖 web；mention_all 覆盖所有人类成员（飞书/xiaoyi）
    assert result == [
        {"intent": "godview", "mention_all": False, "member_names": [], "speaker": None},
        {"intent": "mention", "mention_all": True, "member_names": [], "speaker": None},
    ]
    assert msg.metadata["fan_out_targets"] == result


async def test_inject_respects_explicit_send_file_targets_by_channel():
    reg = SessionSharingRegistry()
    await _make_subscription(reg, "s1", SubRole.GODVIEW, "web")
    await _make_subscription(reg, "s1", "reviewer-1", "feishu")
    cm = _make_channel_manager(reg)
    msg = _make_file_msg("s1", channel_id="web", metadata={"send_file_targets": ["feishu"]})
    result = await cm._inject_file_delivery_fanout(msg, "chat.file")
    assert result and result[0]["intent"] == "mention"
    assert "reviewer-1" in result[0]["member_names"]


async def test_inject_respects_explicit_send_file_targets_by_member_name():
    reg = SessionSharingRegistry()
    await _make_subscription(reg, "s1", SubRole.GODVIEW, "web")
    await _make_subscription(reg, "s1", "reviewer-1", "feishu")
    cm = _make_channel_manager(reg)
    msg = _make_file_msg("s1", channel_id="web", metadata={"send_file_targets": ["reviewer-1"]})
    result = await cm._inject_file_delivery_fanout(msg, "chat.file")
    assert result and result[0]["intent"] == "mention"
    assert "reviewer-1" in result[0]["member_names"]


async def test_inject_explicit_target_no_match_falls_back_to_godview():
    reg = SessionSharingRegistry()
    await _make_subscription(reg, "s1", SubRole.GODVIEW, "web")
    await _make_subscription(reg, "s1", "reviewer-1", "feishu")
    cm = _make_channel_manager(reg)
    msg = _make_file_msg("s1", channel_id="web", metadata={"send_file_targets": ["dingtalk"]})
    result = await cm._inject_file_delivery_fanout(msg, "chat.file")
    assert result == [{"intent": "godview", "mention_all": False, "member_names": [], "speaker": None}]


async def test_inject_explicit_target_excludes_godview_to_avoid_leak():
    # 飞书同时有 godview 订阅 + 人类成员订阅；指定 ["feishu"] 应只命中人类成员，
    # 不应把 "GodView" 放入 member_names（否则 lookup_member("GodView") 会广播到 web）。
    reg = SessionSharingRegistry()
    await _make_subscription(reg, "s1", SubRole.GODVIEW, "web")
    await _make_subscription(reg, "s1", SubRole.GODVIEW, "feishu")
    await _make_subscription(reg, "s1", "reviewer-1", "feishu")
    cm = _make_channel_manager(reg)
    msg = _make_file_msg("s1", channel_id="web", metadata={"send_file_targets": ["feishu"]})
    result = await cm._inject_file_delivery_fanout(msg, "chat.file")
    assert result and result[0]["intent"] == "mention"
    assert "reviewer-1" in result[0]["member_names"]
    assert "GodView" not in result[0]["member_names"]


async def test_inject_auto_reaches_member_only_channel_without_godview():
    # 飞书只有人类成员订阅（/join 后未再发消息，godview 尚未补注册）。
    # 自动模式必须仍能覆盖飞书（靠 mention_all），不能只靠 godview。
    reg = SessionSharingRegistry()
    await _make_subscription(reg, "s1", SubRole.GODVIEW, "web")
    await _make_subscription(reg, "s1", "reviewer-1", "feishu")
    cm = _make_channel_manager(reg)
    msg = _make_file_msg("s1", channel_id="web")
    result = await cm._inject_file_delivery_fanout(msg, "chat.file")
    intents = [t["intent"] for t in result]
    assert "godview" in intents and any(t["mention_all"] for t in result)


async def test_inject_preserves_existing_fan_out_targets():
    reg = SessionSharingRegistry()
    await _make_subscription(reg, "s1", SubRole.GODVIEW, "web")
    cm = _make_channel_manager(reg)
    existing = [{"intent": "mention", "mention_all": True, "member_names": [], "speaker": None}]
    msg = _make_file_msg("s1", channel_id="web", metadata={"fan_out_targets": existing})
    result = await cm._inject_file_delivery_fanout(msg, "chat.file")
    assert result is existing