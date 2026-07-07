import asyncio

import pytest

from jiuwenswarm.common.schema.agent import AgentRequest
from jiuwenswarm.common.schema.message import ReqMethod
from jiuwenswarm.server import agent_ws_server as agent_ws_server_module
from jiuwenswarm.server.runtime import agent_manager as agent_manager_module


class FakeWebSocket:
    def __init__(self):
        self.sent = []

    async def send(self, payload):
        self.sent.append(payload)


class FakeAgent:
    def __init__(self):
        self.reload_calls = []

    async def reload_agent_config(self, **kwargs):
        self.reload_calls.append(kwargs)


class FakeTeamManager:
    def __init__(self, channel_id, calls):
        self.channel_id = channel_id
        self.calls = calls

    async def update_evolution_config(self, config):
        self.calls.append((self.channel_id, config))


@pytest.mark.asyncio
async def test_reload_agents_config_limits_reload_to_explicit_channel_and_session(monkeypatch):
    manager = agent_manager_module.AgentManager()
    tui_agent = FakeAgent()
    web_agent = FakeAgent()
    manager.agents = {
        "tui": {"code": tui_agent},
        "web": {"agent": web_agent},
    }
    team_updates = []
    monkeypatch.setattr(
        agent_manager_module,
        "get_team_manager",
        lambda channel_id: FakeTeamManager(channel_id, team_updates),
    )

    config = {"models": {"defaults": []}}
    env = {"MODEL_NAME": "GLM-5"}
    await manager.reload_agents_config(
        config,
        env,
        target_channel_id="tui",
        target_session_id="tui_session_1",
    )

    assert tui_agent.reload_calls == [
        {
            "config_base": config,
            "env_overrides": env,
            "target_session_id": "tui_session_1",
        }
    ]
    assert web_agent.reload_calls == []
    assert team_updates == [("tui", config)]


@pytest.mark.asyncio
async def test_agent_reload_config_handler_passes_explicit_scope(monkeypatch):
    server = agent_ws_server_module.AgentWebSocketServer()
    calls = []

    async def fake_reload(config, env, **kwargs):
        calls.append((config, env, kwargs))

    monkeypatch.setattr(server._agent_manager, "reload_agents_config", fake_reload)
    monkeypatch.setattr(
        agent_ws_server_module,
        "encode_agent_response_for_wire",
        lambda resp, response_id: {
            "response_id": response_id,
            "ok": resp.ok,
            "payload": resp.payload,
        },
    )

    request = AgentRequest(
        request_id="reload-1",
        channel_id="cli",
        req_method=ReqMethod.AGENT_RELOAD_CONFIG,
        params={
            "config": {"models": {"defaults": []}},
            "env": {},
            "target_channel_id": "tui",
            "target_session_id": "tui_session_1",
        },
    )

    ws = FakeWebSocket()
    await server._handle_agent_reload_config(ws, request, asyncio.Lock())

    assert calls == [
        (
            {"models": {"defaults": []}},
            {},
            {
                "target_channel_id": "tui",
                "target_session_id": "tui_session_1",
            },
        )
    ]


def test_deep_adapter_reload_session_scope_selects_only_target_session():
    from jiuwenswarm.server.runtime.agent_adapter.interface_deep import (
        JiuWenSwarmDeepAdapter,
    )

    adapter = JiuWenSwarmDeepAdapter()
    session_a = object()
    session_b = object()
    adapter._session_adapters = {
        "tui_session_a": session_a,
        "tui_session_b": session_b,
    }

    assert list(adapter._iter_session_adapters_for_reload("tui_session_b")) == [
        ("tui_session_b", session_b)
    ]
    assert list(adapter._iter_session_adapters_for_reload(None)) == [
        ("tui_session_a", session_a),
        ("tui_session_b", session_b),
    ]
