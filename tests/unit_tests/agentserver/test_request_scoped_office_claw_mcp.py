# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for Relay's legacy request-scoped ``office_claw_mcp`` field."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from jiuwenswarm.common.mcp_config import (
    OfficeClawMcpRegistration,
    extract_office_claw_mcp,
    validate_office_claw_mcp_config,
)
from jiuwenswarm.common.schema.agent import AgentRequest, AgentResponse, AgentResponseChunk
from jiuwenswarm.server.runtime.agent_adapter import interface_deep
from jiuwenswarm.server.runtime.agent_adapter.interface_deep import JiuWenSwarmDeepAdapter


def _request(config: object | None = None) -> AgentRequest:
    params = {"query": "一分钟后提醒我喝水"}
    if config is not None:
        params["office_claw_mcp"] = config
    return AgentRequest(
        request_id="req-123",
        channel_id="officeclaw",
        session_id="session-456",
        params=params,
    )


def _valid_config() -> dict[str, object]:
    return {
        "command": r"E:\node\node.exe",
        "args": [r"E:\relay\packages\mcp-server\dist\index.js"],
        "cwd": r"E:\relay",
        "env": {
            "OFFICE_CLAW_API_URL": "http://127.0.0.1:3000",
            "OFFICE_CLAW_INVOCATION_ID": "invoke-1",
            "OFFICE_CLAW_CALLBACK_TOKEN": "secret-token",
            "OFFICE_CLAW_MCP_EXCLUDED_TOOLS": "office_claw_list_tasks",
        },
    }


def _startup_env() -> dict[str, str]:
    return {
        "OFFICE_CLAW_MCP_COMMAND": r"E:\node\node.exe",
        "OFFICE_CLAW_MCP_ARGS_JSON": r'["E:\\relay\\packages\\mcp-server\\dist\\index.js"]',
        "OFFICE_CLAW_MCP_CWD": r"E:\relay",
    }


class _AbilityManager:
    def __init__(self) -> None:
        self.cards: dict[str, object] = {}
        self.removed: list[str] = []

    def add(self, card: object) -> SimpleNamespace:
        name = str(getattr(card, "name"))
        self.cards[name] = card
        return SimpleNamespace(added=True)

    def remove(self, name: str) -> None:
        self.removed.append(name)
        self.cards.pop(name, None)


class _ResourceManager:
    def __init__(self) -> None:
        self.tools: dict[str, object] = {}
        self.removed: list[str] = []

    def add_tool(self, tool: object, *, tag: str) -> None:
        assert tag == "office-claw"
        card = getattr(tool, "card")
        self.tools[str(card.id)] = tool

    def remove_tool(self, tool_id: str) -> None:
        self.removed.append(tool_id)
        self.tools.pop(tool_id, None)


def _bare_session_adapter() -> JiuWenSwarmDeepAdapter:
    adapter = object.__new__(JiuWenSwarmDeepAdapter)
    adapter._instance = SimpleNamespace(ability_manager=_AbilityManager())
    return adapter


def test_extract_supports_only_office_claw_mcp() -> None:
    config = _valid_config()

    extracted = extract_office_claw_mcp(
        {
            "office_claw_mcp": config,
            "request_mcp_servers": {"mcpServers": {"other": {"command": "bad"}}},
        }
    )

    assert extracted == config
    assert extracted is not config
    assert extract_office_claw_mcp({"request_mcp_servers": {"mcpServers": {}}}) is None
    assert extract_office_claw_mcp({"office_claw_mcp": "invalid"}) is None


def test_validation_pins_process_identity_to_relay_startup() -> None:
    config = _valid_config()

    validated = validate_office_claw_mcp_config(config, environ=_startup_env())

    assert validated == config
    changed = _valid_config()
    changed["args"] = [r"E:\attacker\script.js"]
    with pytest.raises(ValueError, match="args do not match"):
        validate_office_claw_mcp_config(changed, environ=_startup_env())


@pytest.mark.asyncio
async def test_registers_exact_tool_names_and_cleans_them_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for key, value in _startup_env().items():
        monkeypatch.setenv(key, value)
    resource_manager = _ResourceManager()
    monkeypatch.setattr(interface_deep.Runner, "resource_mgr", resource_manager)
    monkeypatch.setattr(
        interface_deep,
        "list_office_claw_mcp_tools",
        AsyncMock(
            return_value=[
                {
                    "name": "office_claw_preview_scheduled_task",
                    "description": "preview",
                    "input_params": {"type": "object"},
                },
                {
                    "name": "office_claw_register_scheduled_task",
                    "description": "register",
                    "input_params": {"type": "object"},
                },
            ]
        ),
    )
    adapter = _bare_session_adapter()

    registration = await adapter.register_request_scoped_office_claw_mcp(
        _request(_valid_config())
    )

    assert registration is not None
    assert registration.tool_names == (
        "office_claw_preview_scheduled_task",
        "office_claw_register_scheduled_task",
    )
    assert set(adapter._instance.ability_manager.cards) == set(registration.tool_names)
    assert len(resource_manager.tools) == 2

    await adapter.cleanup_request_scoped_office_claw_mcp(registration)

    assert resource_manager.tools == {}
    assert adapter._instance.ability_manager.cards == {}
    assert resource_manager.removed == list(registration.tool_ids)


@pytest.mark.asyncio
async def test_registration_failure_is_non_blocking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for key, value in _startup_env().items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(
        interface_deep,
        "list_office_claw_mcp_tools",
        AsyncMock(side_effect=RuntimeError("MCP unavailable")),
    )
    adapter = _bare_session_adapter()

    registration = await adapter.register_request_scoped_office_claw_mcp(
        _request(_valid_config())
    )

    assert registration is None
    assert adapter._instance.ability_manager.cards == {}


@pytest.mark.asyncio
async def test_unary_request_always_cleans_up_request_mcp() -> None:
    parent = object.__new__(JiuWenSwarmDeepAdapter)
    parent._is_session_scoped_adapter = False
    registration = OfficeClawMcpRegistration("req-123", ("tool-id",), ("tool",))
    response = AgentResponse(request_id="req-123", channel_id="officeclaw")
    child = SimpleNamespace(
        register_request_scoped_office_claw_mcp=AsyncMock(return_value=registration),
        process_message_impl=AsyncMock(return_value=response),
        cleanup_request_scoped_office_claw_mcp=AsyncMock(),
    )
    parent._get_or_create_session_adapter = AsyncMock(return_value=child)
    parent._evict_idle_session_adapters = AsyncMock()

    result = await parent.process_message_impl(_request(_valid_config()), {})

    assert result is response
    child.cleanup_request_scoped_office_claw_mcp.assert_awaited_once_with(registration)


@pytest.mark.asyncio
async def test_stream_request_cleans_up_when_consumer_finishes() -> None:
    parent = object.__new__(JiuWenSwarmDeepAdapter)
    parent._is_session_scoped_adapter = False
    registration = OfficeClawMcpRegistration("req-123", ("tool-id",), ("tool",))
    chunk = AgentResponseChunk(request_id="req-123", channel_id="officeclaw")

    class _Child:
        def __init__(self) -> None:
            self.register_request_scoped_office_claw_mcp = AsyncMock(
                return_value=registration
            )
            self.cleanup_request_scoped_office_claw_mcp = AsyncMock()

        async def process_message_stream_impl(self, request, inputs):
            yield chunk

    child = _Child()
    parent._get_or_create_session_adapter = AsyncMock(return_value=child)
    parent._evict_idle_session_adapters = AsyncMock()

    chunks = [
        item
        async for item in parent.process_message_stream_impl(
            _request(_valid_config()),
            {},
        )
    ]

    assert chunks == [chunk]
    child.cleanup_request_scoped_office_claw_mcp.assert_awaited_once_with(registration)
