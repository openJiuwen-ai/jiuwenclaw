# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for ``ephemeral_stdio_mcp_tool.EphemeralStdioMcpTool``.

Covers the qualified-name plumbing introduced to disambiguate tools that
share a bare name across multiple MCP servers. ``card.name`` is the
LLM-facing name (qualified), while ``_raw_tool_name`` is what is sent to
the underlying MCP server's ``call_tool``.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openjiuwen.core.foundation.tool import ToolCard

_TOOLS_DIR = Path(__file__).resolve().parents[3] / "jiuwenclaw" / "agentserver" / "tools"
_EPH_PATH = _TOOLS_DIR / "ephemeral_stdio_mcp_tool.py"


def _load_eph_module():
    """Load ``ephemeral_stdio_mcp_tool`` as a standalone module.

    Importing it via the ``jiuwenclaw.agentserver.tools`` package pulls in
    transitive dependencies (memory_tools, reload_result, etc.) that this
    test does not need. Loading the file in isolation keeps the surface
    area minimal.
    """
    spec = importlib.util.spec_from_file_location("_eph_test_module", _EPH_PATH)
    assert spec and spec.loader, "ephemeral_stdio_mcp_tool.py failed to load"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def eph_module():
    return _load_eph_module()


def _make_card(qualified_name: str, raw_name: str) -> ToolCard:
    return ToolCard(
        id=f"{qualified_name.split('__', 1)[0]}::req.{raw_name}",
        name=qualified_name,
        description="desc",
        input_params={},
    )


class TestQualifiedNamePlumbing:
    """``EphemeralStdioMcpTool`` must keep qualified and raw names separate."""

    def test_explicit_raw_tool_name_kept(self, eph_module):
        card = _make_card("members__execute_sql", "execute_sql")
        tool = eph_module.EphemeralStdioMcpTool(
            card, lambda: {"command": "npx", "args": []}, raw_tool_name="execute_sql"
        )
        # LLM-facing (card.name) stays qualified; raw stays raw.
        assert tool._card.name == "members__execute_sql"
        assert tool._raw_tool_name == "execute_sql"

    def test_default_raw_tool_name_falls_back_to_card_name(self, eph_module):
        card = _make_card("orders-3__list_tables", "list_tables")
        tool = eph_module.EphemeralStdioMcpTool(card, lambda: {"command": "npx", "args": []})
        assert tool._raw_tool_name == "orders-3__list_tables"
        assert tool._card.name == "orders-3__list_tables"

    def test_two_servers_same_bare_tool_no_collision(self, eph_module):
        """The two tool instances must keep their own _raw_tool_name."""
        card_a = _make_card("orders-3__execute_sql", "execute_sql")
        card_b = _make_card("members__execute_sql", "execute_sql")
        tool_a = eph_module.EphemeralStdioMcpTool(
            card_a, lambda: {"command": "npx", "args": []}, raw_tool_name="execute_sql"
        )
        tool_b = eph_module.EphemeralStdioMcpTool(
            card_b, lambda: {"command": "npx", "args": []}, raw_tool_name="execute_sql"
        )
        assert tool_a._card.name != tool_b._card.name
        assert tool_a._card.id != tool_b._card.id
        assert tool_a._raw_tool_name == tool_b._raw_tool_name == "execute_sql"


class TestInvokeUsesRawToolName:
    """``invoke`` must hand the raw name to ``call_tool`` (not the qualified one)."""

    @pytest.mark.asyncio
    async def test_invoke_calls_underlying_mcp_with_raw_name(self, eph_module):
        captured: dict = {}

        class _FakeCallResult:
            def __init__(self, text: str) -> None:
                self.content = [SimpleNamespace(text=text)]

        class _FakeSession:
            async def initialize(self_inner):  # noqa: N805
                return None

            async def call_tool(self_inner, name, arguments):  # noqa: N805
                captured["name"] = name
                captured["args"] = arguments
                return _FakeCallResult("ok")

        class _FakeSessionCM:
            def __init__(self) -> None:
                self.session = _FakeSession()

            async def __aenter__(self_inner):
                return self_inner.session

            async def __aexit__(self_inner, *exc):
                return False

        class _FakeStdioClientCM:
            def __init__(self, params) -> None:
                self.params = params

            async def __aenter__(self_inner):
                return (MagicMock(), MagicMock())

            async def __aexit__(self_inner, *exc):
                return False

        # mcp.client.stdio.stdio_client is a sync callable that returns an async CM
        def _fake_stdio_client(params):
            return _FakeStdioClientCM(params)

        def _fake_client_session(read, write, *, sampling_callback=None):
            return _FakeSessionCM()

        card = _make_card("orders-3__execute_sql", "execute_sql")
        tool = eph_module.EphemeralStdioMcpTool(
            card,
            lambda: {"command": "npx", "args": ["-y", "x"]},
            raw_tool_name="execute_sql",
        )

        fake_mcp = SimpleNamespace(
            ClientSession=_fake_client_session,
            StdioServerParameters=lambda **kw: SimpleNamespace(**kw),
        )
        with patch.dict(
            sys.modules,
            {
                "mcp": fake_mcp,
                "mcp.client": SimpleNamespace(stdio=SimpleNamespace(stdio_client=_fake_stdio_client)),
                "mcp.client.stdio": SimpleNamespace(stdio_client=_fake_stdio_client),
            },
        ):
            result = await tool.invoke({"query": "SELECT 1"})

        assert result == {"result": "ok"}
        # Critical: MCP must be called with the raw name, not the qualified one.
        assert captured["name"] == "execute_sql", captured
        assert captured["args"] == {"query": "SELECT 1"}

    @pytest.mark.asyncio
    async def test_invoke_with_default_raw_uses_card_name(self, eph_module):
        """When ``raw_tool_name`` is omitted, ``call_tool`` receives ``card.name``."""
        captured: dict = {}

        class _FakeCallResult:
            content = [SimpleNamespace(text="done")]

        class _FakeSession:
            async def initialize(self_inner):
                return None

            async def call_tool(self_inner, name, arguments):
                captured["name"] = name
                return _FakeCallResult()

        class _FakeSessionCM:
            def __init__(self) -> None:
                self.session = _FakeSession()

            async def __aenter__(self_inner):
                return self_inner.session

            async def __aexit__(self_inner, *exc):
                return False

        class _FakeStdioClientCM:
            async def __aenter__(self_inner):
                return (MagicMock(), MagicMock())

            async def __aexit__(self_inner, *exc):
                return False

        def _fake_stdio_client(params):
            return _FakeStdioClientCM()

        def _fake_client_session(read, write, *, sampling_callback=None):
            return _FakeSessionCM()

        card = _make_card("members__list_tables", "list_tables")
        tool = eph_module.EphemeralStdioMcpTool(card, lambda: {"command": "npx", "args": []})

        fake_mcp = SimpleNamespace(
            ClientSession=_fake_client_session,
            StdioServerParameters=lambda **kw: SimpleNamespace(**kw),
        )
        with patch.dict(
            sys.modules,
            {
                "mcp": fake_mcp,
                "mcp.client": SimpleNamespace(stdio=SimpleNamespace(stdio_client=_fake_stdio_client)),
                "mcp.client.stdio": SimpleNamespace(stdio_client=_fake_stdio_client),
            },
        ):
            await tool.invoke({})

        assert captured["name"] == "members__list_tables"
