import asyncio

import pytest

from openjiuwen.extensions.external_provider.openai_auth.openai_account_auth import (
    OpenAIAccountAuthError,
)

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


class FakeOpenAIAccountService:
    def __init__(self, *, authenticated=False):
        self.calls = []
        self.authenticated = authenticated

    def status(self):
        self.calls.append(("status", None))
        return {"authenticated": self.authenticated}

    def start_login(self):
        self.calls.append(("start_login", None))
        return {"status": "pending", "login_id": "login-1"}

    def pending_login(self):
        self.calls.append(("pending_login", None))
        return {"status": "pending", "login_id": "login-1"}

    def poll_login(self, login_id):
        self.calls.append(("poll_login", login_id))
        return {"status": "authenticated", "authenticated": True}

    def logout(self):
        self.calls.append(("logout", None))
        return {"logged_out": True}

    def list_models(self):
        self.calls.append(("list_models", None))
        return {
            "models": ["gpt-test"],
            "base_url": "https://example.test/codex",
            "auth": {"authenticated": self.authenticated},
        }


@pytest.mark.parametrize(
    "client_config",
    [
        {
            "client_provider": "OpenAIAccount",
            "model_name": "gpt-test",
            "api_base": "https://example.test/codex",
            "api_key": "",
        },
        {
            "client_provider": "intelli_router",
            "model_name": "router-model",
            "api_base": "",
            "api_key": "",
            "intelli_router_deployments": [],
        },
    ],
)
def test_model_client_validation_uses_provider_specific_core_contract(client_config):
    tui_connect_module._validate_model_client_config(
        client_config,
        model_label=client_config["model_name"],
    )


def test_model_client_validation_still_requires_openai_api_key():
    with pytest.raises(tui_connect_module._ModelOpError, match="api_key is required"):
        tui_connect_module._validate_model_client_config(
            {
                "client_provider": "OpenAI",
                "model_name": "gpt-test",
                "api_base": "https://api.example.test/v1",
                "api_key": "",
            },
            model_label="gpt-test",
        )


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
    assert "openai_account.auth.status" in cli_handlers
    assert "openai_account.auth.start_login" in cli_handlers
    assert "openai_account.auth.pending_login" in cli_handlers
    assert "openai_account.auth.poll_login" in cli_handlers
    assert "openai_account.auth.logout" in cli_handlers
    assert "openai_account.models.list" in cli_handlers
    assert "openai_account.models.use" in cli_handlers

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
async def test_openai_account_handlers_delegate_to_injected_service():
    server = FakeGatewayServer()
    service = FakeOpenAIAccountService()
    register_cli_handlers(
        CliHandlersBindParams(
            channel=server,
            openai_account_service=service,
        )
    )
    handlers = server.local_handlers["/tui"]

    calls = [
        ("openai_account.auth.status", {}),
        ("openai_account.auth.start_login", {}),
        ("openai_account.auth.pending_login", {}),
        ("openai_account.auth.poll_login", {"login_id": "login-1"}),
        ("openai_account.auth.logout", {}),
        ("openai_account.models.list", {}),
    ]
    for index, (method, params) in enumerate(calls):
        await handlers[method](object(), f"req-{index}", params, "sess-1")

    assert service.calls == [
        ("status", None),
        ("start_login", None),
        ("pending_login", None),
        ("poll_login", "login-1"),
        ("logout", None),
        ("list_models", None),
    ]
    assert all(response["ok"] is True for response in server.responses)


@pytest.mark.asyncio
async def test_openai_account_handler_preserves_typed_retryable_error():
    server = FakeGatewayServer()

    class FailingService(FakeOpenAIAccountService):
        def poll_login(self, login_id):
            raise OpenAIAccountAuthError(
                f"temporary poll failure for {login_id}",
                code="openai_account_device_code_poll_network_error",
            )

    register_cli_handlers(
        CliHandlersBindParams(
            channel=server,
            openai_account_service=FailingService(),
        )
    )

    await server.local_handlers["/tui"]["openai_account.auth.poll_login"](
        object(),
        "req-poll",
        {"login_id": "login-1"},
        "sess-1",
    )

    response = server.responses[-1]
    assert response["ok"] is False
    assert response["code"] == "openai_account_device_code_poll_network_error"
    assert response["payload"]["retriable"] is True


@pytest.mark.asyncio
async def test_command_model_rejects_manual_openai_account_creation(monkeypatch):
    server = FakeGatewayServer()

    def fail_if_config_is_written(_mutator, **_kwargs):
        raise AssertionError(
            "manual OpenAIAccount add must be rejected before config write"
        )

    monkeypatch.setattr(tui_connect_module, "update_config", fail_if_config_is_written)
    register_cli_handlers(
        CliHandlersBindParams(
            channel=server,
            agent_client=object(),
            openai_account_service=FakeOpenAIAccountService(authenticated=True),
        )
    )

    await server.local_handlers["/tui"]["command.model"](
        object(),
        "req-add-oauth",
        {
            "action": "add_model",
            "target": "gpt-test",
            "config": {
                "model_name": "gpt-test",
                "model_provider": "OpenAIAccount",
                "api_base": "https://example.test/codex",
                "api_key": "",
            },
        },
        "sess-1",
    )

    response = server.responses[-1]
    assert response["ok"] is False
    assert "/auth login or /auth models" in response["error"]


@pytest.mark.asyncio
async def test_openai_account_model_use_derives_managed_fields_and_applies_once(
    monkeypatch,
):
    server = FakeGatewayServer()
    service = FakeOpenAIAccountService(authenticated=True)
    config = {"models": {"defaults": []}}
    apply_calls = []

    def fake_update_config(mutator, **_kwargs):
        return mutator(config)

    async def on_config_saved(
        updated_keys, *, env_updates, config_payload, reload_options
    ):
        apply_calls.append((updated_keys, env_updates, config_payload, reload_options))
        return True

    monkeypatch.setattr(tui_connect_module, "update_config", fake_update_config)
    monkeypatch.setattr(tui_connect_module, "get_config", lambda: config)
    register_cli_handlers(
        CliHandlersBindParams(
            channel=server,
            agent_client=object(),
            on_config_saved=on_config_saved,
            openai_account_service=service,
        )
    )

    await server.local_handlers["/tui"]["openai_account.models.use"](
        object(),
        "req-use-oauth",
        {"model_id": "gpt-test"},
        "sess-1",
    )

    response = server.responses[-1]
    assert response["ok"] is True
    assert response["payload"] == {
        "type": "switched",
        "current": "gpt-test",
        "requested": "gpt-test",
        "saved": True,
        "applied": True,
    }
    stored = config["models"]["defaults"][0]
    assert stored["model_client_config"]["model_name"] == "gpt-test"
    assert stored["model_client_config"]["client_provider"] == "OpenAIAccount"
    assert stored["model_client_config"]["api_base"] == "https://example.test/codex"
    assert stored["model_client_config"]["api_key"] == ""
    assert len(apply_calls) == 1
    assert apply_calls[0][3]["reason"] == "openai_account_model_use"


@pytest.mark.asyncio
async def test_openai_account_model_use_maps_unexpected_storage_failure(monkeypatch):
    server = FakeGatewayServer()

    def fail_update_config(_mutator, **_kwargs):
        raise RuntimeError("unexpected storage implementation failure")

    monkeypatch.setattr(tui_connect_module, "update_config", fail_update_config)
    register_cli_handlers(
        CliHandlersBindParams(
            channel=server,
            openai_account_service=FakeOpenAIAccountService(authenticated=True),
        )
    )

    await server.local_handlers["/tui"]["openai_account.models.use"](
        object(),
        "req-use-oauth-failure",
        {"model_id": "gpt-test"},
        "sess-1",
    )

    response = server.responses[-1]
    assert response["ok"] is False
    assert response["code"] == "INTERNAL_ERROR"
    assert response["error"] == "OpenAI Account model configuration failed"
    assert "unexpected storage" not in response["error"]


@pytest.mark.asyncio
async def test_command_model_rejects_managed_field_update_for_openai_account(
    monkeypatch,
):
    server = FakeGatewayServer()
    config = {
        "models": {
            "defaults": [
                {
                    "model_client_config": {
                        "model_name": "gpt-test",
                        "client_provider": "OpenAIAccount",
                        "api_base": "https://example.test/codex",
                        "api_key": "",
                    },
                    "model_config_obj": {},
                }
            ]
        }
    }

    def fake_update_config(mutator, **_kwargs):
        return mutator(config)

    monkeypatch.setattr(tui_connect_module, "update_config", fake_update_config)
    register_cli_handlers(
        CliHandlersBindParams(
            channel=server,
            agent_client=object(),
            openai_account_service=FakeOpenAIAccountService(authenticated=True),
        )
    )

    await server.local_handlers["/tui"]["command.model"](
        object(),
        "req-update-oauth",
        {
            "action": "update_model",
            "index": 0,
            "config": {"api_base": "https://attacker.example/v1"},
        },
        "sess-1",
    )

    response = server.responses[-1]
    assert response["ok"] is False
    assert "managed by /auth login and /auth models" in response["error"]
    assert (
        config["models"]["defaults"][0]["model_client_config"]["api_base"]
        == "https://example.test/codex"
    )


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
async def test_command_model_switch_waits_for_single_config_apply(monkeypatch):
    server = FakeGatewayServer()
    direct_reload_calls = []
    apply_calls = []
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
        direct_reload_calls.append((env, label))

    async def on_config_saved(
        updated_keys, *, env_updates, config_payload, reload_options
    ):
        await asyncio.sleep(0)
        apply_calls.append((updated_keys, env_updates, config_payload, reload_options))
        return True

    def fake_update_config(mutator, **kwargs):
        data = {"models": {"defaults": [dict(d) for d in defaults]}}
        return mutator(data)

    monkeypatch.setattr(tui_connect_module, "_send_tui_agent_request", fake_send_tui_agent_request)
    monkeypatch.setattr(tui_connect_module, "update_config", fake_update_config)
    monkeypatch.setattr(
        tui_connect_module,
        "get_config_raw",
        lambda: {"models": {"defaults": defaults}},
    )
    monkeypatch.setattr(tui_connect_module, "get_config", lambda: {"models": {"defaults": defaults}})

    register_cli_handlers(
        CliHandlersBindParams(
            channel=server,
            agent_client=object(),
            message_handler=None,
            on_config_saved=on_config_saved,
            path="/tui",
        )
    )

    await server.local_handlers["/tui"]["command.model"](
        object(),
        "req-switch",
        {"model": "glm"},
        "tui_session_1",
    )
    assert server.responses[-1] == {
        "id": "req-switch",
        "ok": True,
        "payload": {
            "current": "GLM-5",
            "requested": "glm",
            "type": "switched",
            "saved": True,
            "applied": True,
        },
        "error": None,
        "code": None,
    }
    assert direct_reload_calls == []
    assert len(apply_calls) == 1
    assert apply_calls[0][3] == {
        "target_channel_id": "tui",
        "target_session_id": "tui_session_1",
        "reason": "model_switch",
    }


@pytest.mark.asyncio
async def test_command_model_reports_saved_but_not_applied(monkeypatch):
    server = FakeGatewayServer()
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
        }
    ]

    def fake_update_config(mutator, **_kwargs):
        return mutator({"models": {"defaults": defaults}})

    async def on_config_saved(*_args, **_kwargs):
        return False

    monkeypatch.setattr(tui_connect_module, "update_config", fake_update_config)
    monkeypatch.setattr(
        tui_connect_module, "get_config", lambda: {"models": {"defaults": defaults}}
    )
    register_cli_handlers(
        CliHandlersBindParams(
            channel=server,
            agent_client=object(),
            on_config_saved=on_config_saved,
        )
    )

    await server.local_handlers["/tui"]["command.model"](
        object(),
        "req-switch-not-applied",
        {"model": "glm"},
        "sess-1",
    )

    response = server.responses[-1]
    assert response["ok"] is True
    assert response["payload"]["saved"] is True
    assert response["payload"]["applied"] is False
    assert "restart or retry" in response["payload"]["apply_error"]


@pytest.mark.asyncio
async def test_session_list_returns_agent_timeout_before_tui_request_timeout(
    monkeypatch,
):
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
