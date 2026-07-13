import asyncio

import pytest

from jiuwenswarm.gateway.channel_manager.tui import tui_connect as tui_connect_module
from jiuwenswarm.gateway.channel_manager.tui.tui_connect import (
    CliHandlersBindParams,
    CliRouteBindParams,
    build_cli_route_binding,
    register_cli_handlers,
)


class FakeGatewayServer:
    """Fake GatewayServer for testing CLI handler registration."""

    def __init__(self):
        self.local_handlers: dict[str, dict] = {}  # path -> {method: handler}
        self.responses = []
        self.session_owners = {}

    def register_local_handler(self, path, method, handler):
        if path not in self.local_handlers:
            self.local_handlers[path] = {}
        self.local_handlers[path][method] = handler

    def bind_session_owner(self, channel_id, session_id, ws):
        self.session_owners[(channel_id, session_id)] = ws

    def is_session_bound_to_client(self, channel_id, session_id, ws):
        return self.session_owners.get((channel_id, session_id)) is ws

    async def send_response(self, ws, req_id, *, ok, payload=None, error=None, code=None):
        self.responses.append(
            {
                "id": req_id,
                "ok": ok,
                "payload": payload or {},
                "error": error,
                "code": code,
            }
        )


class FakeMessageHandler:
    def __init__(self):
        self.cancelled = []
        self.scheduled = []
        self.reconnected = []

    async def cancel_agent_sessions_on_disconnect(self, session_keys, *, stale_request_keys=None):
        self.cancelled.append((session_keys, stale_request_keys or []))

    async def schedule_cancel_agent_sessions_on_disconnect(self, session_keys, *, stale_request_keys=None):
        self.scheduled.append((session_keys, stale_request_keys or []))

    def cancel_scheduled_disconnect_cancel(self, channel_id, session_id):
        self.reconnected.append((channel_id, session_id))
        return True


@pytest.mark.asyncio
async def test_register_cli_handlers_registers_local_methods():
    server = FakeGatewayServer()

    register_cli_handlers(
        CliHandlersBindParams(
            channel=server,
            agent_client=None,
            message_handler=None,
            on_config_saved=None,
            path="/tui",
        )
    )

    cli_handlers = server.local_handlers["/tui"]
    assert "config.get" in cli_handlers
    assert "config.validate_model" in cli_handlers
    assert "session.list" in cli_handlers
    assert "chat.send" in cli_handlers
    assert "chat.resume" in cli_handlers
    assert "history.get" in cli_handlers
    assert "tui.disconnect" in cli_handlers

    await cli_handlers["chat.send"](object(), "req-1", {}, "sess-1")

    assert server.responses == [
        {
            "id": "req-1",
            "ok": True,
            "payload": {"accepted": True, "session_id": "sess-1"},
            "error": None,
            "code": None,
        }
    ]


@pytest.mark.asyncio
async def test_tui_disconnect_handler_cancels_session_immediately():
    server = FakeGatewayServer()
    handler = FakeMessageHandler()

    register_cli_handlers(
        CliHandlersBindParams(
            channel=server,
            agent_client=None,
            message_handler=handler,
            on_config_saved=None,
            path="/tui",
        )
    )

    ws = object()
    server.bind_session_owner("tui", "sess-exit", ws)
    await server.local_handlers["/tui"]["tui.disconnect"](
        ws,
        "req-exit",
        {"reason": "user_exit"},
        "sess-exit",
    )

    assert handler.cancelled == [([("tui", "sess-exit")], [])]
    assert server.responses[-1] == {
        "id": "req-exit",
        "ok": True,
        "payload": {"accepted": True, "session_id": "sess-exit"},
        "error": None,
        "code": None,
    }


@pytest.mark.asyncio
async def test_tui_disconnect_handler_does_not_cancel_session_owned_by_another_ws():
    server = FakeGatewayServer()
    handler = FakeMessageHandler()
    owner_ws = object()
    exiting_ws = object()
    server.bind_session_owner("tui", "sess-shared", owner_ws)

    register_cli_handlers(
        CliHandlersBindParams(
            channel=server,
            agent_client=None,
            message_handler=handler,
            on_config_saved=None,
            path="/tui",
        )
    )

    await server.local_handlers["/tui"]["tui.disconnect"](
        exiting_ws,
        "req-exit-other",
        {"reason": "user_exit"},
        "sess-shared",
    )

    assert handler.cancelled == []
    assert server.responses[-1] == {
        "id": "req-exit-other",
        "ok": True,
        "payload": {"accepted": True, "session_id": "sess-shared"},
        "error": None,
        "code": None,
    }


def test_build_cli_route_binding_creates_route_and_install_hook():
    binding = build_cli_route_binding(CliRouteBindParams(path="/tui"))
    server = FakeGatewayServer()

    assert binding.path == "/tui"
    assert binding.channel_id == "tui"
    assert "chat.send" in binding.forward_methods
    assert "history.get" in binding.forward_methods
    assert binding.install is not None

    binding.install(server)

    cli_handlers = server.local_handlers["/tui"]
    assert "config.get" in cli_handlers
    assert "config.validate_model" in cli_handlers
    assert "session.list" in cli_handlers
    assert "chat.send" in cli_handlers


@pytest.mark.asyncio
async def test_tui_route_disconnect_schedules_cancel_for_transport_close():
    handler = FakeMessageHandler()
    binding = build_cli_route_binding(CliRouteBindParams(path="/tui", message_handler=handler))

    await binding.disconnect_handler(
        object(),
        [("tui", "sess-drop")],
        [("tui", "req-drop")],
    )

    assert handler.scheduled == [([("tui", "sess-drop")], [("tui", "req-drop")])]
    assert handler.cancelled == []


@pytest.mark.asyncio
async def test_tui_route_disconnect_skips_scheduled_cancel_after_explicit_exit():
    handler = FakeMessageHandler()
    binding = build_cli_route_binding(CliRouteBindParams(path="/tui", message_handler=handler))
    ws = type("FakeWs", (), {})()
    ws._jiuwenswarm_tui_user_exit = True  # pylint: disable=protected-access

    await binding.disconnect_handler(ws, [("tui", "sess-exit")], [])

    assert handler.scheduled == []


def test_tui_session_bind_handler_cancels_pending_disconnect_cancel():
    handler = FakeMessageHandler()
    binding = build_cli_route_binding(CliRouteBindParams(path="/tui", message_handler=handler))

    binding.session_bind_handler("tui", "sess-reconnect")

    assert handler.reconnected == [("tui", "sess-reconnect")]


@pytest.mark.asyncio
async def test_config_validate_model_handler_uses_local_probe(monkeypatch):
    server = FakeGatewayServer()

    register_cli_handlers(
        CliHandlersBindParams(
            channel=server,
            agent_client=None,
            message_handler=None,
            on_config_saved=None,
            path="/tui",
        )
    )

    cli_handlers = server.local_handlers["/tui"]

    class FakeModel:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

        async def invoke(self, *args, **kwargs):
            return {"content": "hello"}

    monkeypatch.setattr("jiuwenswarm.gateway.channel_manager.tui.tui_connect.Model", FakeModel)

    await cli_handlers["config.validate_model"](
        object(),
        "req-validate",
        {
            "model_provider": "openai",
            "model": "gpt-4.1",
            "api_base": "https://api.openai.com/v1",
            "api_key": "secret",
        },
        "sess-1",
    )

    assert server.responses[-1] == {
        "id": "req-validate",
        "ok": True,
        "payload": {
            "provider": "OpenAI",
            "model": "gpt-4.1",
            "response": "hello",
        },
        "error": None,
        "code": None,
    }


@pytest.mark.asyncio
async def test_command_model_switch_sends_scoped_agent_reload(monkeypatch):
    server = FakeGatewayServer()
    sent_envs = []
    defaults = [
        {
            "alias": "glm",
            "model_client_config": {
                "api_key": "key",
                "api_base": "https://example.test/v1",
                "model_name": "GLM-5",
                "client_provider": "openai",
            },
            "model_config_obj": {},
        },
        {
            "alias": "other",
            "model_client_config": {
                "api_key": "key",
                "api_base": "https://example.test/v1",
                "model_name": "other-model",
                "client_provider": "openai",
            },
            "model_config_obj": {},
        },
    ]

    async def fake_send_tui_agent_request(_client, env, *, label):
        sent_envs.append((env, label))

    monkeypatch.setattr(tui_connect_module, "_send_tui_agent_request", fake_send_tui_agent_request)
    monkeypatch.setattr(
        tui_connect_module,
        "get_config_raw",
        lambda: {"models": {"defaults": defaults}},
    )
    monkeypatch.setattr(tui_connect_module, "ensure_defaults_list_in_config", lambda: defaults)
    monkeypatch.setattr(tui_connect_module, "update_default_models_in_config", lambda _defaults: None)
    monkeypatch.setattr(tui_connect_module, "get_config", lambda: {"models": {"defaults": defaults}})

    register_cli_handlers(
        CliHandlersBindParams(
            channel=server,
            agent_client=object(),
            message_handler=None,
            on_config_saved=None,
            path="/tui",
        )
    )

    await server.local_handlers["/tui"]["command.model"](
        object(),
        "req-switch",
        {"model": "glm"},
        "tui_session_1",
    )
    await asyncio.sleep(0)

    assert server.responses[-1] == {
        "id": "req-switch",
        "ok": True,
        "payload": {
            "current": "GLM-5",
            "requested": "glm",
            "type": "switched",
            "applied": True,
        },
        "error": None,
        "code": None,
    }
    assert len(sent_envs) == 1
    env, label = sent_envs[0]
    assert label == "command.model.switch"
    assert env.params["target_channel_id"] == "tui"
    assert env.params["target_session_id"] == "tui_session_1"
    assert env.params["reason"] == "model_switch"


@pytest.mark.asyncio
async def test_session_list_returns_agent_timeout_before_tui_request_timeout(monkeypatch):
    server = FakeGatewayServer()

    class HangingAgentClient:
        async def send_request(self, env):
            await asyncio.Event().wait()

    monkeypatch.setattr(
        "jiuwenswarm.gateway.routing.agent_request_timeout._TUI_DEFAULT_UNARY_TIMEOUT_SECONDS",
        0.01,
        raising=False,
    )
    register_cli_handlers(
        CliHandlersBindParams(
            channel=server,
            agent_client=HangingAgentClient(),
            message_handler=None,
            on_config_saved=None,
            path="/tui",
        )
    )

    await asyncio.wait_for(
        server.local_handlers["/tui"]["session.list"](
            object(),
            "req-session-list",
            {"limit": 10},
            "sess-1",
        ),
        timeout=0.2,
    )

    assert server.responses[-1] == {
        "id": "req-session-list",
        "ok": False,
        "payload": {},
        "error": "AgentServer request timed out",
        "code": "AGENT_SERVER_TIMEOUT",
    }
