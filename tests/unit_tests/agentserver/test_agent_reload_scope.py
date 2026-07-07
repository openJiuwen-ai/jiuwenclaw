import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

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

    async def reload_agent_config(self, *args, **kwargs):
        if args:
            self.reload_calls.append({"args": args, "kwargs": kwargs})
        else:
            self.reload_calls.append(kwargs)


class FailingReloadAgent(FakeAgent):
    async def reload_agent_config(self, *args, **kwargs):
        await super().reload_agent_config(*args, **kwargs)
        raise RuntimeError("reload failed")


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
async def test_reload_agents_config_skips_duplicate_global_reload(monkeypatch):
    manager = agent_manager_module.AgentManager()
    agent = FakeAgent()
    manager.agents = {"web": {"agent": agent}}
    team_updates = []
    monkeypatch.setattr(
        agent_manager_module,
        "get_team_manager",
        lambda channel_id: FakeTeamManager(channel_id, team_updates),
    )

    config = {"models": {"defaults": [{"model_name": "deepseek"}]}}
    await manager.reload_agents_config(config, {})
    await manager.reload_agents_config({"models": {"defaults": [{"model_name": "deepseek"}]}}, {})

    assert len(agent.reload_calls) == 1
    assert team_updates == [("web", config)]


@pytest.mark.asyncio
async def test_reload_agents_config_retries_same_reload_after_team_update_failure(monkeypatch):
    manager = agent_manager_module.AgentManager()
    agent = FakeAgent()
    manager.agents = {"web": {"agent": agent}}

    class FlakyTeamManager:
        def __init__(self):
            self.calls = []

        async def update_evolution_config(self, config):
            self.calls.append(config)
            if len(self.calls) == 1:
                raise RuntimeError("temporary team update failure")

    team_manager = FlakyTeamManager()
    monkeypatch.setattr(
        agent_manager_module,
        "get_team_manager",
        lambda channel_id: team_manager,
    )

    config = {"models": {"defaults": [{"model_name": "deepseek"}]}}
    await manager.reload_agents_config(config, {})
    await manager.reload_agents_config({"models": {"defaults": [{"model_name": "deepseek"}]}}, {})

    assert len(agent.reload_calls) == 2
    assert team_manager.calls == [config, config]


@pytest.mark.asyncio
async def test_reload_agents_config_reloads_when_agent_set_changes(monkeypatch):
    manager = agent_manager_module.AgentManager()
    first_agent = FakeAgent()
    second_agent = FakeAgent()
    manager.agents = {"web": {"first": first_agent}}
    team_updates = []
    monkeypatch.setattr(
        agent_manager_module,
        "get_team_manager",
        lambda channel_id: FakeTeamManager(channel_id, team_updates),
    )

    config = {"models": {"defaults": [{"model_name": "deepseek"}]}}
    await manager.reload_agents_config(config, {})
    manager.agents["web"]["second"] = second_agent
    await manager.reload_agents_config({"models": {"defaults": [{"model_name": "deepseek"}]}}, {})

    assert len(first_agent.reload_calls) == 2
    assert len(second_agent.reload_calls) == 1
    assert team_updates == [("web", config), ("web", config)]


@pytest.mark.asyncio
async def test_reload_agents_config_reloads_when_agent_instance_changes(monkeypatch):
    manager = agent_manager_module.AgentManager()
    old_agent = FakeAgent()
    new_agent = FakeAgent()
    manager.agents = {"web": {"agent": old_agent}}
    team_updates = []
    monkeypatch.setattr(
        agent_manager_module,
        "get_team_manager",
        lambda channel_id: FakeTeamManager(channel_id, team_updates),
    )

    config = {"models": {"defaults": [{"model_name": "deepseek"}]}}
    await manager.reload_agents_config(config, {})
    manager.agents["web"]["agent"] = new_agent
    await manager.reload_agents_config({"models": {"defaults": [{"model_name": "deepseek"}]}}, {})

    assert len(old_agent.reload_calls) == 1
    assert len(new_agent.reload_calls) == 1
    assert team_updates == [("web", config), ("web", config)]


@pytest.mark.asyncio
async def test_reload_agents_config_resolves_config_once_when_config_none(monkeypatch):
    manager = agent_manager_module.AgentManager()
    agent = FakeAgent()
    manager.agents = {"web": {"agent": agent}}
    team_updates = []
    config = {"models": {"defaults": [{"model_name": "from-file"}]}}
    get_config_calls = []
    monkeypatch.setattr(
        agent_manager_module,
        "get_config",
        lambda: get_config_calls.append(True) or config,
    )
    monkeypatch.setattr(
        agent_manager_module,
        "get_team_manager",
        lambda channel_id: FakeTeamManager(channel_id, team_updates),
    )

    await manager.reload_agents_config(None, {})

    assert get_config_calls == [True]
    assert agent.reload_calls == [{"config_base": config, "env_overrides": {}}]
    assert team_updates == [("web", config)]


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


@pytest.mark.asyncio
async def test_deep_adapter_global_reload_marks_sessions_stale_without_fanout(monkeypatch):
    from jiuwenswarm.server.runtime.agent_adapter import interface_deep as interface_module
    from jiuwenswarm.server.runtime.agent_adapter.interface_deep import (
        JiuWenSwarmDeepAdapter,
    )

    parent = JiuWenSwarmDeepAdapter()
    parent._instance = MagicMock()
    session_a = FakeAgent()
    session_b = FakeAgent()
    parent._session_adapters = {
        "session-a": session_a,
        "session-b": session_b,
    }

    async def _async_noop(*args, **kwargs):
        return None

    with (
        patch.object(interface_module, "clear_config_cache", MagicMock()),
        patch.object(interface_module, "clear_memory_manager_cache", MagicMock()),
        patch.object(interface_module.JiuWenSwarmDeepAdapter, "_refresh_multimodal_configs", MagicMock()),
        patch.object(interface_module.JiuWenSwarmDeepAdapter, "_create_model", MagicMock(return_value=object())),
        patch.object(interface_module.JiuWenSwarmDeepAdapter, "_sync_multimodal_tools_for_runtime", MagicMock()),
        patch.object(interface_module.JiuWenSwarmDeepAdapter, "_sync_paid_search_tool_for_runtime", MagicMock()),
        patch.object(interface_module.JiuWenSwarmDeepAdapter, "_sync_symphony_tools_for_runtime", MagicMock()),
        patch.object(interface_module.JiuWenSwarmDeepAdapter, "_sync_skill_retrieval_tools_for_runtime", MagicMock()),
        patch.object(
            interface_module.JiuWenSwarmDeepAdapter,
            "_sync_skill_retrieval_prompt_rail_for_runtime",
            AsyncMock(),
        ),
        patch.object(interface_module.JiuWenSwarmDeepAdapter, "_filesystem_rail_enabled_for_profile", MagicMock(return_value=True)),
        patch.object(interface_module.JiuWenSwarmDeepAdapter, "load_user_rails", AsyncMock()),
        patch.object(interface_module.JiuWenSwarmDeepAdapter, "_get_current_agent_rails", MagicMock(return_value=[])),
        patch.object(interface_module.JiuWenSwarmDeepAdapter, "_make_deep_agent_config", MagicMock(return_value=object())),
        patch.object(interface_module.JiuWenSwarmDeepAdapter, "_sync_active_evolution_review_agent_after_reload", MagicMock()),
        patch.object(interface_module.JiuWenSwarmDeepAdapter, "_sync_mcp_servers_for_runtime", _async_noop),
    ):
        await parent.reload_agent_config(
            {"react": {"agent_name": "main_agent"}, "browser": {"headless": True}},
            {},
        )

    assert session_a.reload_calls == []
    assert session_b.reload_calls == []


@pytest.mark.asyncio
async def test_deep_adapter_existing_session_lazy_reload_once(monkeypatch):
    from jiuwenswarm.server.runtime.agent_adapter.interface_deep import (
        JiuWenSwarmDeepAdapter,
    )

    parent = JiuWenSwarmDeepAdapter()
    parent._instance = MagicMock()
    session = FakeAgent()
    parent._session_adapters = {"session-a": session}
    pending_config = {"react": {"agent_name": "main_agent"}, "browser": {"headless": True}}
    parent._mark_session_adapters_stale_for_reload(pending_config, {"MODEL_NAME": "new-model"})

    first_lookup = await parent._get_or_create_session_adapter("session-a")
    second_lookup = await parent._get_or_create_session_adapter("session-a")

    assert first_lookup is session
    assert second_lookup is session
    assert len(session.reload_calls) == 1
    call = session.reload_calls[0]
    assert call["args"][0] == pending_config
    assert call["args"][1] == {"MODEL_NAME": "new-model"}
    assert call["kwargs"]["target_session_id"] == "session-a"
    assert parent._session_adapter_versions["session-a"] == 1


@pytest.mark.asyncio
async def test_deep_adapter_failed_lazy_reload_is_not_retried_immediately(monkeypatch):
    from jiuwenswarm.server.runtime.agent_adapter.interface_deep import (
        JiuWenSwarmDeepAdapter,
    )

    parent = JiuWenSwarmDeepAdapter()
    parent._instance = MagicMock()
    session = FailingReloadAgent()
    parent._session_adapters = {"session-a": session}
    pending_config = {"react": {"agent_name": "main_agent"}, "browser": {"headless": True}}
    parent._mark_session_adapters_stale_for_reload(pending_config, {})

    await parent._get_or_create_session_adapter("session-a")
    await parent._get_or_create_session_adapter("session-a")

    assert len(session.reload_calls) == 1
    assert parent._session_adapter_versions.get("session-a", 0) == 0


@pytest.mark.asyncio
async def test_deep_adapter_new_session_applies_pending_reload(monkeypatch):
    """A session adapter created after a global reload must reflect the pending config.

    The new adapter is built from ``_session_instance_config`` (which may predate the
    reload), so ``_get_or_create_session_adapter`` must apply the pending
    ``config_base`` once before returning it.
    """
    from jiuwenswarm.server.runtime.agent_adapter import interface_deep as interface_module
    from jiuwenswarm.server.runtime.agent_adapter.interface_deep import (
        JiuWenSwarmDeepAdapter,
    )

    async def _async_noop(*args, **kwargs):
        return None

    parent = JiuWenSwarmDeepAdapter()
    parent._instance = MagicMock()
    # Simulate a prior global reload that left a pending config_base (version=1).
    pending_config = {"react": {"agent_name": "main_agent"}, "browser": {"headless": True}}
    parent._mark_session_adapters_stale_for_reload(pending_config, {"MODEL_NAME": "new-model"})
    pending_config["react"]["agent_name"] = "mutated_after_mark"
    assert parent._session_adapter_config_version == 1

    new_session = FakeAgent()
    new_session.create_instance = _async_noop

    with patch.object(
        interface_module.JiuWenSwarmDeepAdapter,
        "_new_session_scoped_adapter",
        MagicMock(return_value=new_session),
    ):
        adapter = await parent._get_or_create_session_adapter("session-new")

    assert adapter is new_session
    # The pending reload was applied exactly once to the freshly created adapter.
    assert len(new_session.reload_calls) == 1
    call = new_session.reload_calls[0]
    # FakeAgent packs positional args into {"args": ..., "kwargs": ...}.
    args = call.get("args", ())
    assert args[0] == {"react": {"agent_name": "main_agent"}, "browser": {"headless": True}}
    assert args[1] == {"MODEL_NAME": "new-model"}
    assert call["kwargs"]["target_session_id"] == "session-new"
    # Version is caught up so the next lookup does not reload again.
    assert parent._session_adapter_versions["session-new"] == 1


@pytest.mark.asyncio
async def test_deep_adapter_new_session_skips_reload_when_no_pending(monkeypatch):
    """Without a pending global reload, creating a new session adapter does not reload."""
    from jiuwenswarm.server.runtime.agent_adapter import interface_deep as interface_module
    from jiuwenswarm.server.runtime.agent_adapter.interface_deep import (
        JiuWenSwarmDeepAdapter,
    )

    async def _async_noop(*args, **kwargs):
        return None

    parent = JiuWenSwarmDeepAdapter()
    parent._instance = MagicMock()
    # No global reload ever happened: version is 0, pending config_base is None.
    assert parent._session_adapter_config_version == 0
    assert parent._pending_session_reload_config_base is None

    new_session = FakeAgent()
    new_session.create_instance = _async_noop

    with patch.object(
        interface_module.JiuWenSwarmDeepAdapter,
        "_new_session_scoped_adapter",
        MagicMock(return_value=new_session),
    ):
        adapter = await parent._get_or_create_session_adapter("session-fresh")

    assert adapter is new_session
    assert new_session.reload_calls == []
    # No version entry is recorded at version 0 (``_reload_session_adapter_if_stale``
    # short-circuits on ``0 >= 0``); the next global reload bumps the version and the
    # missing entry (defaulting to 0) will correctly trigger a lazy reload then.
    assert "session-fresh" not in parent._session_adapter_versions
