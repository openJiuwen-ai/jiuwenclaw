import asyncio
import json

import pytest

from jiuwenclaw.server import agent_ws_server as agent_ws_server_module
from jiuwenclaw.common.schema.agent import AgentRequest
from jiuwenclaw.common.schema.message import ReqMethod


class FakeWebSocket:
    def __init__(self):
        self.sent = []

    async def send(self, payload):
        self.sent.append(json.loads(payload))


class AgentWebSocketServerHarness(agent_ws_server_module.AgentWebSocketServer):
    async def handle_command_add_dir_for_test(self, ws, request, send_lock):
        await self._handle_command_add_dir(ws, request, send_lock)

    async def handle_command_compact_for_test(self, ws, request, send_lock):
        await self._handle_command_compact(ws, request, send_lock)

    async def handle_command_diff_for_test(self, ws, request, send_lock):
        await self._handle_command_diff(ws, request, send_lock)

    async def handle_command_model_for_test(self, ws, request, send_lock):
        await self._handle_command_model(ws, request, send_lock)

    async def handle_command_mcp_for_test(self, ws, request, send_lock):
        await self._handle_command_mcp(ws, request, send_lock)

    async def handle_command_resume_for_test(self, ws, request, send_lock):
        await self._handle_command_resume(ws, request, send_lock)

    async def handle_command_session_for_test(self, ws, request, send_lock):
        await self._handle_command_session(ws, request, send_lock)

    def get_agent_manager_for_test(self):
        return self._agent_manager


def fake_encode_agent_response_for_wire(resp, response_id):
    return {
        "response_id": response_id,
        "payload": resp.payload,
        "ok": resp.ok,
    }


@pytest.fixture
def server():
    return AgentWebSocketServerHarness()


@pytest.fixture
def fake_ws():
    return FakeWebSocket()


@pytest.fixture(autouse=True)
def patch_wire_encoder(monkeypatch):
    monkeypatch.setattr(
        agent_ws_server_module,
        "encode_agent_response_for_wire",
        fake_encode_agent_response_for_wire,
    )


@pytest.mark.asyncio
async def test_handle_command_add_dir_returns_path_and_remember(
    server, fake_ws, monkeypatch
):
    persist_stub = {
        "ok": True,
        "normalized": "/tmp/demo",
        "path_pattern": "re:^/tmp/demo(?:$|/)",
        "shell_pattern": "re:.*/tmp/demo.*",
        "tiered_overrides": True,
    }
    monkeypatch.setattr(
        agent_ws_server_module,
        "persist_cli_trusted_directory",
        lambda _raw: persist_stub,
    )
    request = AgentRequest(
        request_id="req-add-dir",
        channel_id="tui",
        req_method=ReqMethod.COMMAND_ADD_DIR,
        params={"path": "/tmp/demo", "remember": True},
    )

    await server.handle_command_add_dir_for_test(fake_ws, request, asyncio.Lock())

    assert fake_ws.sent == [
        {
            "response_id": "req-add-dir",
            "payload": {
                "path": "/tmp/demo",
                "remember": True,
                "persist": persist_stub,
            },
            "ok": True,
        }
    ]


@pytest.mark.asyncio
async def test_handle_command_compact_returns_custom_instructions(server, fake_ws, monkeypatch):
    request = AgentRequest(
        request_id="req-compact",
        channel_id="tui",
        req_method=ReqMethod.COMMAND_COMPACT,
        params={"instructions": "focus on architecture"},
    )

    class MockAgent:
        async def compress_context(self, session_id):
            return {
                "result": "compressed",
                "stats": {
                    "raw_total_tokens": 1000,
                    "total_tokens": 300,
                },
            }

    mock_agent = MockAgent()

    async def mock_get_agent(channel_id, mode, project_dir=None):
        return mock_agent

    async def mock_send_push(msg):
        pass

    monkeypatch.setattr(
        server.get_agent_manager_for_test(),
        "get_agent",
        mock_get_agent,
    )
    monkeypatch.setattr(
        server,
        "send_push",
        mock_send_push,
    )

    await server.handle_command_compact_for_test(fake_ws, request, asyncio.Lock())

    assert fake_ws.sent == [
        {
            "response_id": "req-compact",
            "payload": {
                "result": "compressed",
                "stats": {
                    "raw_total_tokens": 1000,
                    "total_tokens": 300,
                },
            },
            "ok": True,
        }
    ]


@pytest.mark.asyncio
async def test_handle_command_diff_returns_summary_payload(server, fake_ws):
    request = AgentRequest(
        request_id="req-diff",
        channel_id="tui",
        req_method=ReqMethod.COMMAND_DIFF,
        params={},
    )

    await server.handle_command_diff_for_test(fake_ws, request, asyncio.Lock())

    assert fake_ws.sent == [
        {
            "response_id": "req-diff",
            "payload": {
                "type": "list",
                "turns": [],
            },
            "ok": True,
        }
    ]


@pytest.mark.asyncio
async def test_handle_command_model_no_action_shows_current(
    server, fake_ws, monkeypatch
):
    """No action → returns current model from os.environ and available list."""
    monkeypatch.setenv("MODEL_NAME", "test-model")
    request = AgentRequest(
        request_id="req-model",
        channel_id="tui",
        req_method=ReqMethod.COMMAND_MODEL,
        params={},
    )

    await server.handle_command_model_for_test(fake_ws, request, asyncio.Lock())

    assert fake_ws.sent == [
        {
            "response_id": "req-model",
            "payload": {
                "current": "test-model",
                "available": ["default-model"],
            },
            "ok": True,
        }
    ]


@pytest.mark.asyncio
async def test_handle_command_model_add_model(server, fake_ws):
    """action=add_model → returns model_added confirmation."""
    request = AgentRequest(
        request_id="req-add",
        channel_id="cli",
        req_method=ReqMethod.COMMAND_MODEL,
        params={"action": "add_model", "target": "my-model", "config": {}},
    )

    await server.handle_command_model_for_test(fake_ws, request, asyncio.Lock())

    assert fake_ws.sent == [
        {
            "response_id": "req-add",
            "payload": {"type": "model_added", "name": "my-model"},
            "ok": True,
        }
    ]


@pytest.mark.asyncio
async def test_handle_command_mcp_list(server, fake_ws, monkeypatch):
    monkeypatch.setattr(
        agent_ws_server_module,
        "get_mcp_servers",
        lambda: [{"name": "demo", "transport": "stdio", "enabled": True, "env": {"TOKEN": "abc"}}],
    )
    request = AgentRequest(
        request_id="req-mcp-list",
        channel_id="tui",
        req_method=ReqMethod.COMMAND_MCP,
        params={"action": "list"},
    )

    await server.handle_command_mcp_for_test(fake_ws, request, asyncio.Lock())

    assert fake_ws.sent == [
        {
            "response_id": "req-mcp-list",
            "payload": {
                "type": "list",
                "items": [{"name": "demo", "transport": "stdio", "enabled": True, "env": {"TOKEN": "***"}}],
            },
            "ok": True,
        }
    ]


@pytest.mark.asyncio
async def test_handle_command_mcp_add_triggers_reload(server, fake_ws, monkeypatch):
    monkeypatch.setattr(
        agent_ws_server_module,
        "upsert_mcp_server_in_config",
        lambda payload: (payload, True),
    )
    monkeypatch.setattr(agent_ws_server_module, "get_config", lambda: {"mcp": {"servers": []}})

    called = {"reload": 0}

    async def _reload(_config, _env):
        called["reload"] += 1

    monkeypatch.setattr(server.get_agent_manager(), "reload_agents_config", _reload)
    request = AgentRequest(
        request_id="req-mcp-add",
        channel_id="tui",
        req_method=ReqMethod.COMMAND_MCP,
        params={
            "action": "add",
            "name": "demo",
            "transport": "stdio",
            "command": "python",
            "args": ["server.py"],
        },
    )

    await server.handle_command_mcp_for_test(fake_ws, request, asyncio.Lock())
    assert called["reload"] == 1
    assert fake_ws.sent == [
        {
            "response_id": "req-mcp-add",
            "payload": {"type": "added", "name": "demo", "applied": True},
            "ok": True,
        }
    ]


@pytest.mark.asyncio
async def test_handle_command_mcp_enable_not_found(server, fake_ws, monkeypatch):
    def _raise_not_found(_name, _enabled):
        raise KeyError("MCP server 'demo' not found")

    monkeypatch.setattr(agent_ws_server_module, "set_mcp_server_enabled_in_config", _raise_not_found)
    request = AgentRequest(
        request_id="req-mcp-enable",
        channel_id="tui",
        req_method=ReqMethod.COMMAND_MCP,
        params={"action": "enable", "name": "demo"},
    )

    await server.handle_command_mcp_for_test(fake_ws, request, asyncio.Lock())
    assert fake_ws.sent == [
        {
            "response_id": "req-mcp-enable",
            "payload": {"error": "\"MCP server 'demo' not found\"", "code": "MCP_NOT_FOUND"},
            "ok": False,
        }
    ]


@pytest.mark.asyncio
async def test_handle_command_mcp_remove(server, fake_ws, monkeypatch):
    monkeypatch.setattr(
        agent_ws_server_module,
        "remove_mcp_server_in_config",
        lambda name: {"name": name, "enabled": True, "transport": "sse", "url": "http://127.0.0.1:9000/sse"},
    )
    monkeypatch.setattr(agent_ws_server_module, "get_config", lambda: {"mcp": {"servers": []}})

    async def _reload(_config, _env):
        return None

    monkeypatch.setattr(server.get_agent_manager(), "reload_agents_config", _reload)
    request = AgentRequest(
        request_id="req-mcp-remove",
        channel_id="tui",
        req_method=ReqMethod.COMMAND_MCP,
        params={"action": "remove", "name": "demo"},
    )

    await server.handle_command_mcp_for_test(fake_ws, request, asyncio.Lock())
    assert fake_ws.sent == [
        {
            "response_id": "req-mcp-remove",
            "payload": {
                "type": "removed",
                "name": "demo",
                "applied": True,
                "item": {"name": "demo", "enabled": True, "transport": "sse", "url": "http://127.0.0.1:9000/sse"},
            },
            "ok": True,
        }
    ]


@pytest.mark.asyncio
async def test_handle_command_mcp_update(server, fake_ws, monkeypatch):
    monkeypatch.setattr(
        agent_ws_server_module,
        "get_mcp_server_config",
        lambda name: {"name": name, "enabled": True, "transport": "sse", "url": "http://127.0.0.1:9000/sse"},
    )
    monkeypatch.setattr(
        agent_ws_server_module,
        "upsert_mcp_server_in_config",
        lambda payload: (payload, False),
    )
    monkeypatch.setattr(agent_ws_server_module, "get_config", lambda: {"mcp": {"servers": []}})

    async def _reload(_config, _env):
        return None

    monkeypatch.setattr(server.get_agent_manager(), "reload_agents_config", _reload)
    request = AgentRequest(
        request_id="req-mcp-update",
        channel_id="tui",
        req_method=ReqMethod.COMMAND_MCP,
        params={"action": "update", "name": "demo", "enabled": False, "url": "http://127.0.0.1:9010/sse"},
    )

    await server.handle_command_mcp_for_test(fake_ws, request, asyncio.Lock())
    assert fake_ws.sent == [
        {
            "response_id": "req-mcp-update",
            "payload": {
                "type": "updated",
                "name": "demo",
                "applied": True,
                "item": {
                    "name": "demo",
                    "enabled": False,
                    "transport": "sse",
                    "url": "http://127.0.0.1:9010/sse",
                },
            },
            "ok": True,
        }
    ]


@pytest.mark.asyncio
async def test_handle_command_mcp_minimal_flow_add_list_disable(server, fake_ws, monkeypatch):
    state = {"servers": []}

    def _upsert(payload):
        state["servers"] = [item for item in state["servers"] if item.get("name") != payload.get("name")]
        state["servers"].append(dict(payload))
        return payload, True

    def _get_servers():
        return [dict(item) for item in state["servers"]]

    def _set_enabled(name, enabled):
        for item in state["servers"]:
            if item.get("name") == name:
                item["enabled"] = bool(enabled)
                return dict(item)
        raise KeyError(f"MCP server '{name}' not found")

    monkeypatch.setattr(agent_ws_server_module, "upsert_mcp_server_in_config", _upsert)
    monkeypatch.setattr(agent_ws_server_module, "get_mcp_servers", _get_servers)
    monkeypatch.setattr(agent_ws_server_module, "set_mcp_server_enabled_in_config", _set_enabled)
    monkeypatch.setattr(agent_ws_server_module, "get_config", lambda: {"mcp": {"servers": _get_servers()}})

    async def _reload(_config, _env):
        return None

    monkeypatch.setattr(server.get_agent_manager(), "reload_agents_config", _reload)

    add_req = AgentRequest(
        request_id="req-flow-add",
        channel_id="tui",
        req_method=ReqMethod.COMMAND_MCP,
        params={
            "action": "add",
            "name": "flow-demo",
            "transport": "sse",
            "url": "http://127.0.0.1:9000/sse",
        },
    )
    await server.handle_command_mcp_for_test(fake_ws, add_req, asyncio.Lock())

    list_req = AgentRequest(
        request_id="req-flow-list",
        channel_id="tui",
        req_method=ReqMethod.COMMAND_MCP,
        params={"action": "list"},
    )
    await server.handle_command_mcp_for_test(fake_ws, list_req, asyncio.Lock())

    disable_req = AgentRequest(
        request_id="req-flow-disable",
        channel_id="tui",
        req_method=ReqMethod.COMMAND_MCP,
        params={"action": "disable", "name": "flow-demo"},
    )
    await server.handle_command_mcp_for_test(fake_ws, disable_req, asyncio.Lock())

    assert fake_ws.sent[0]["payload"]["type"] == "added"
    assert fake_ws.sent[1]["payload"]["items"][0]["name"] == "flow-demo"
    assert fake_ws.sent[2]["payload"]["type"] == "disabled"
    assert fake_ws.sent[2]["payload"]["item"]["enabled"] is False


@pytest.mark.asyncio
async def test_handle_command_resume_returns_mock_session(server, fake_ws):
    request = AgentRequest(
        request_id="req-resume",
        channel_id="tui",
        req_method=ReqMethod.COMMAND_RESUME,
        params={"query": "sess_123"},
    )

    await server.handle_command_resume_for_test(fake_ws, request, asyncio.Lock())

    assert fake_ws.sent == [
        {
            "response_id": "req-resume",
            "payload": {
                "session_id": "sess_123",
                "query": "sess_123",
                "resumed": True,
                "preview": "Mock resumed conversation",
            },
            "ok": True,
        }
    ]


@pytest.mark.asyncio
async def test_handle_command_session_returns_remote_handoff(server, fake_ws):
    request = AgentRequest(
        request_id="req-session",
        channel_id="tui",
        session_id="sess_demo",
        req_method=ReqMethod.COMMAND_SESSION,
        params={},
    )

    await server.handle_command_session_for_test(fake_ws, request, asyncio.Lock())

    assert fake_ws.sent == [
        {
            "response_id": "req-session",
            "payload": {
                "session_id": "sess_demo",
                "remote_url": "https://example.com/session/sess_demo",
                "qr_text": "session:sess_demo",
            },
            "ok": True,
        }
    ]
