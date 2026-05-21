# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for CodeAdapter ACP tool registration."""

from types import SimpleNamespace

from jiuwenswarm.server.runtime.agent_adapter.interface_code import JiuwenClawCodeAdapter


class _FakeResourceMgr:
    def __init__(self) -> None:
        self._tools: dict[str, object] = {}

    def get_tool(self, tool_id: str) -> object | None:
        return self._tools.get(tool_id)

    def add_tool(self, tool: object) -> None:
        self._tools[tool.card.id] = tool


def test_code_adapter_builds_acp_chat_when_profile_configured(monkeypatch):
    monkeypatch.setattr(
        "jiuwenswarm.server.runtime.agent_adapter.interface_code.get_config",
        lambda: {
            "acp_agents": {"codex": {"command": "npx", "args": []}},
            "modes": {"code": {"tools": ["acp_chat"]}},
        },
    )
    monkeypatch.setattr(
        "jiuwenswarm.server.runtime.agent_adapter.interface_code.Runner",
        SimpleNamespace(resource_mgr=_FakeResourceMgr()),
    )

    cards = JiuwenClawCodeAdapter().build_code_tool_cards("agent-id")

    assert [card.name for card in cards] == ["acp_chat"]


def test_code_adapter_skips_acp_chat_without_profiles(monkeypatch):
    monkeypatch.setattr(
        "jiuwenswarm.server.runtime.agent_adapter.interface_code.get_config",
        lambda: {
            "acp_agents": {},
            "modes": {"code": {"tools": ["acp_chat"]}},
        },
    )
    monkeypatch.setattr(
        "jiuwenswarm.server.runtime.agent_adapter.interface_code.Runner",
        SimpleNamespace(resource_mgr=_FakeResourceMgr()),
    )

    cards = JiuwenClawCodeAdapter().build_code_tool_cards("agent-id")

    assert cards == []
