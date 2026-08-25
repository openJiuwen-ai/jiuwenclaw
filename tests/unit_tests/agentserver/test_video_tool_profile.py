from __future__ import annotations

from types import SimpleNamespace

import pytest

from jiuwenswarm.common.video_tool_profile import (
    VIDEO_READONLY_TOOL_NAMES,
    VIDEO_READONLY_TOOL_PROFILE,
    VIDEO_TOOL_CHANNEL_ID,
)
from jiuwenswarm.server.runtime.agent_adapter.interface_deep import JiuWenSwarmDeepAdapter
from jiuwenswarm.server.runtime.agent_manager import AgentManager


@pytest.mark.asyncio
async def test_video_tool_channel_uses_full_core_agent_profile(monkeypatch) -> None:
    manager = AgentManager()
    created = []
    sentinel = SimpleNamespace()

    async def fake_create_agent(agent_key, mode, config, sub_mode, cache_key):
        created.append((agent_key, mode, config, sub_mode, cache_key))
        return sentinel

    monkeypatch.setattr(manager, "_create_agent", fake_create_agent)

    agent = await manager.get_agent(channel_id=VIDEO_TOOL_CHANNEL_ID, mode="agent")

    assert agent is sentinel
    assert len(created) == 1
    agent_key, mode, config, sub_mode, _ = created[0]
    assert agent_key == VIDEO_TOOL_CHANNEL_ID
    assert mode == "agent"
    assert sub_mode is None
    assert config == {"channel_id": VIDEO_TOOL_CHANNEL_ID}


@pytest.mark.asyncio
async def test_explicit_legacy_video_readonly_profile_exposes_only_web_tools(monkeypatch) -> None:
    adapter = JiuWenSwarmDeepAdapter()
    adapter._instance_overrides = {"tool_profile": VIDEO_READONLY_TOOL_PROFILE}
    registered = []
    monkeypatch.setattr(adapter, "_register_agent_owned_tool", lambda tool, owner: registered.append((tool, owner)))

    cards = await adapter._get_tool_cards("video-agent-owner")

    assert {card.name for card in cards} == VIDEO_READONLY_TOOL_NAMES
    assert {tool.card.name for tool, _ in registered} == VIDEO_READONLY_TOOL_NAMES
    assert {owner for _, owner in registered} == {"video-agent-owner"}
