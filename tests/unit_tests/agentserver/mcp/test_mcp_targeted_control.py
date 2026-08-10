# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests: phase-2 targeted single-MCP control (no full reload).

connect/disconnect/enable/disable handlers now call
AgentManager.apply_mcp_change → adapter.register_mcp_by_name /
unregister_mcp_by_name / toggle_mcp_enabled_by_name, which touch ONE MCP
via _register_mcp_server / _unregister_mcp_server. This must NOT trigger
reload_agents_config (the heavy full-resync of all mcp.servers).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class _FakeAgent:
    """Fake JiuWenSwarm exposing apply_mcp_change for AgentManager fanout."""
    def __init__(self, *, ok=True):
        self._ok = ok
        self.calls: list = []

    async def apply_mcp_change(self, name, action, *, enabled=True, **kw):
        self.calls.append((name, action, enabled))
        return self._ok


@pytest.mark.anyio
async def test_agent_manager_apply_mcp_change_fans_out_to_all_agents() -> None:
    """apply_mcp_change forwards to every live agent instance."""
    from jiuwenswarm.server.runtime.agent_manager import AgentManager

    mgr = AgentManager.__new__(AgentManager)
    a1 = _FakeAgent()
    a2 = _FakeAgent()
    mgr.agents = {"web": {"k1": a1}, "tui": {"k2": a2}}
    ok = await mgr.apply_mcp_change("baidu", "add")
    assert ok is True
    assert a1.calls == [("baidu", "add", True)]
    assert a2.calls == [("baidu", "add", True)]


@pytest.mark.anyio
async def test_agent_manager_apply_mcp_change_target_channel_only() -> None:
    from jiuwenswarm.server.runtime.agent_manager import AgentManager
    mgr = AgentManager.__new__(AgentManager)
    a1 = _FakeAgent(); a2 = _FakeAgent()
    mgr.agents = {"web": {"k1": a1}, "tui": {"k2": a2}}
    await mgr.apply_mcp_change("baidu", "remove", target_channel_id="web")
    assert a1.calls == [("baidu", "remove", True)]
    assert a2.calls == []  # tui not touched


@pytest.mark.anyio
async def test_agent_manager_apply_mcp_change_surfaces_all_adapter_failure() -> None:
    """When NO adapter applied the change (all returned ok=False), apply_mcp_change
    RAISES RuntimeError carrying the reason — so a failed register surfaces to the
    connect handler and the frontend gets connect_failed instead of a silent
    "connected". Previously it returned False silently, masking register failures."""
    from jiuwenswarm.server.runtime.agent_manager import AgentManager
    mgr = AgentManager.__new__(AgentManager)
    bad = _FakeAgent(ok=False)
    mgr.agents = {"web": {"k1": bad}}
    with pytest.raises(RuntimeError, match="ok=False"):
        await mgr.apply_mcp_change("baidu", "add")


@pytest.mark.anyio
async def test_agent_manager_apply_mcp_change_one_success_among_failures() -> None:
    """A single adapter succeeding among others returning ok=False still returns
    True (no raise) — the fan-out must not abort on a per-adapter failure, and a
    partial success is still a success."""
    from jiuwenswarm.server.runtime.agent_manager import AgentManager
    mgr = AgentManager.__new__(AgentManager)
    bad = _FakeAgent(ok=False)
    good = _FakeAgent(ok=True)
    mgr.agents = {"web": {"k1": bad}, "tui": {"k2": good}}
    ok = await mgr.apply_mcp_change("baidu", "add")
    assert ok is True


@pytest.mark.anyio
async def test_adapter_register_mcp_by_name_uses_single_register_no_reload() -> None:
    """register_mcp_by_name builds cfg from get_mcp_server_config + calls
    _register_mcp_server once; it must not invoke reload_agents_config."""
    from jiuwenswarm.server.runtime.agent_adapter.interface_deep import (
        JiuWenSwarmDeepAdapter,
    )
    from openjiuwen.core.foundation.tool import McpServerConfig

    adapter = JiuWenSwarmDeepAdapter.__new__(JiuWenSwarmDeepAdapter)
    adapter._instance = MagicMock()
    adapter._registered_mcp_server_ids = set()
    adapter._registered_mcp_servers = {}

    fake_entry = {"name": "baidu", "transport": "sse", "url": "https://x",
                  "enabled": True, "server_id_scope": "mcp:baidu"}

    with patch(
        "jiuwenswarm.server.runtime.agent_adapter.interface_deep.get_mcp_server_config",
        return_value=fake_entry,
    ), patch.object(
        adapter, "_build_mcp_server_config", return_value=McpServerConfig(
            server_name="baidu", server_path="https://x", client_type="sse",
        ),
    ), patch.object(
        adapter, "_register_mcp_server", AsyncMock(return_value=True),
    ) as reg:
        ok = await adapter.register_mcp_by_name("baidu")

    assert ok is True
    reg.assert_awaited_once()


@pytest.mark.anyio
async def test_adapter_unregister_mcp_by_name_no_reload() -> None:
    from jiuwenswarm.server.runtime.agent_adapter.interface_deep import (
        JiuWenSwarmDeepAdapter,
    )
    from openjiuwen.core.foundation.tool import McpServerConfig

    adapter = JiuWenSwarmDeepAdapter.__new__(JiuWenSwarmDeepAdapter)
    adapter._instance = MagicMock()
    cfg = McpServerConfig(server_id="sid1", server_name="baidu",
                          server_path="https://x", client_type="sse")
    adapter._registered_mcp_server_ids = {"sid1"}
    adapter._registered_mcp_servers = {"sid1": cfg}

    with patch.object(adapter, "_unregister_mcp_server", AsyncMock()) as unreg:
        ok = await adapter.unregister_mcp_by_name("baidu")

    assert ok is True
    unreg.assert_awaited_once_with("sid1")


@pytest.mark.anyio
async def test_adapter_toggle_disable_routes_to_unregister() -> None:
    from jiuwenswarm.server.runtime.agent_adapter.interface_deep import (
        JiuWenSwarmDeepAdapter,
    )
    adapter = JiuWenSwarmDeepAdapter.__new__(JiuWenSwarmDeepAdapter)
    adapter._instance = MagicMock()
    with patch.object(adapter, "register_mcp_by_name", AsyncMock(return_value=True)) as reg, \
         patch.object(adapter, "unregister_mcp_by_name", AsyncMock(return_value=True)) as unreg:
        await adapter.toggle_mcp_enabled_by_name("baidu", True)
        await adapter.toggle_mcp_enabled_by_name("baidu", False)
    reg.assert_awaited_once()
    unreg.assert_awaited_once()


@pytest.mark.anyio
async def test_connector_connect_handler_calls_apply_mcp_change_not_reload() -> None:
    """connect handler must route to apply_mcp_change, not reload_agents_config."""
    import asyncio, json
    from jiuwenswarm.common.schema.agent import AgentRequest
    from jiuwenswarm.common.schema.message import ReqMethod
    from jiuwenswarm.server.agent_ws_server import AgentWebSocketServer

    class _FakeWS:
        def __init__(self): self.sent = []
        async def send(self, d): self.sent.append(d)

    class _AM:
        def __init__(self):
            self.reloaded = False
            self.applied = []
        async def reload_agents_config(self, *a, **kw):
            self.reloaded = True
        async def apply_mcp_change(self, name, action, *, enabled=True, target_channel_id=None):
            self.applied.append((name, action, enabled))
            return True

    server = AgentWebSocketServer.__new__(AgentWebSocketServer)
    am = _AM()
    server._agent_manager = am
    server._mask_sensitive_fields = lambda i: i
    ws = _FakeWS()
    request = AgentRequest(request_id="r", session_id="s", channel_id="web",
                           req_method=ReqMethod.MCP_CONNECT,
                           params={"name": "baidu"})
    with patch("jiuwenswarm.server.runtime.mcp.registry.connect_mcp",
               return_value={"name": "baidu", "transport": "sse", "url": "https://x"}), \
         patch("jiuwenswarm.server.agent_ws_server.preflight_mcp_server_reachable",
               return_value=(True, "")):
        await server._handle_mcp_connect(ws, request, asyncio.Lock())

    payload = json.loads(ws.sent[0])
    # drill to payload
    body = payload.get("body", {})
    result = body.get("result") or body.get("details") or {}
    assert result.get("type") == "connected"
    assert am.applied == [("baidu", "add", True)]  # targeted
    assert am.reloaded is False  # no full reload
