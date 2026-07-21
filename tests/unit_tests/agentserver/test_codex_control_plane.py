"""Process-boundary tests for the Codex subscription control plane."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys

import pytest

from jiuwenswarm.common.e2a.gateway_normalize import e2a_from_agent_fields
from jiuwenswarm.common.schema.agent import AgentRequest
from jiuwenswarm.common.schema.message import ReqMethod
from jiuwenswarm.common.security.ws_origin import is_loopback_websocket_peer
from jiuwenswarm.integrations.ai4research_subscription.constants import (
    CODEX_MODEL_ALIAS,
    CODEX_PROVIDER_NAME,
)
from jiuwenswarm.integrations.ai4research_subscription.consumer_policy import (
    CODEX_CALL_PERMIT_KWARG,
    CodexConsumer,
    consume_codex_call_permit,
    current_codex_consumer,
)
from jiuwenswarm.server import agent_ws_server as agent_ws_server_module


class _PeerWebSocket:
    def __init__(self, host: str | None):
        self.remote_address = (host, 50000) if host is not None else None


def _fake_wire(response, response_id):
    return {
        "response_id": response_id,
        "ok": response.ok,
        "payload": response.payload,
    }


@pytest.fixture
def capture_wire(monkeypatch):
    sent: list[dict] = []

    async def fake_send(_ws, wire):
        sent.append(wire)
        return True

    monkeypatch.setattr(
        agent_ws_server_module, "encode_agent_response_for_wire", _fake_wire
    )
    monkeypatch.setattr(agent_ws_server_module, "send_wire_payload", fake_send)
    return sent


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("127.0.0.1", True),
        ("::1", True),
        ("::ffff:127.0.0.1", True),
        ("203.0.113.9", False),
        ("localhost", False),
        (None, False),
    ],
)
def test_loopback_peer_uses_only_server_observed_ip(host, expected):
    assert is_loopback_websocket_peer(_PeerWebSocket(host)) is expected


def _raw_request(method: ReqMethod) -> str:
    envelope = e2a_from_agent_fields(
        request_id="req-control",
        channel_id="web",
        session_id="sess-control",
        req_method=method,
        params={
            "model_provider": CODEX_PROVIDER_NAME,
            "model": CODEX_MODEL_ALIAS,
        },
    )
    return json.dumps(envelope.to_dict())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method",
    [
        ReqMethod.CODEX_AUTH_STATUS,
        ReqMethod.CODEX_AUTH_START,
        ReqMethod.CODEX_AUTH_CANCEL,
        ReqMethod.CODEX_AUTH_LOGOUT,
        ReqMethod.CODEX_VALIDATE_MODEL,
    ],
)
async def test_remote_peer_cannot_run_codex_control_hooks_or_handlers(
    capture_wire, method
):
    server = agent_ws_server_module.AgentWebSocketServer.__new__(
        agent_ws_server_module.AgentWebSocketServer
    )

    hook_requests: list[AgentRequest] = []

    async def before_request(request):
        hook_requests.append(request)

    async def forbidden_handler(*_args):
        pytest.fail("remote Codex request reached the validation handler")

    server._trigger_before_chat_request_hook = before_request
    server._handle_codex_validate_model = forbidden_handler
    server._handle_codex_auth = forbidden_handler

    await server._handle_message(
        _PeerWebSocket("203.0.113.9"),
        _raw_request(method),
        asyncio.Lock(),
    )

    assert hook_requests == []
    assert capture_wire == [
        {
            "response_id": "req-control",
            "ok": False,
            "payload": {
                "error": "Codex subscription controls require a local Jiuwen Gateway and AgentServer.",
                "code": "local_provider_required",
                "provider": CODEX_PROVIDER_NAME,
            },
        }
    ]


@pytest.mark.asyncio
async def test_local_peer_dispatches_codex_validation(monkeypatch, capture_wire):
    server = agent_ws_server_module.AgentWebSocketServer.__new__(
        agent_ws_server_module.AgentWebSocketServer
    )
    dispatched: list[AgentRequest] = []

    async def before_request(_request):
        return None

    async def validation_handler(_ws, request, _send_lock):
        dispatched.append(request)

    server._trigger_before_chat_request_hook = before_request
    server._handle_codex_validate_model = validation_handler

    await server._handle_message(
        _PeerWebSocket("127.0.0.1"),
        _raw_request(ReqMethod.CODEX_VALIDATE_MODEL),
        asyncio.Lock(),
    )

    assert len(dispatched) == 1
    assert dispatched[0].req_method == ReqMethod.CODEX_VALIDATE_MODEL
    assert capture_wire == []


@pytest.mark.asyncio
async def test_remote_peer_non_codex_method_is_not_blocked(capture_wire):
    server = agent_ws_server_module.AgentWebSocketServer.__new__(
        agent_ws_server_module.AgentWebSocketServer
    )
    dispatched: list[AgentRequest] = []
    hook_requests: list[AgentRequest] = []

    async def before_request(request):
        hook_requests.append(request)

    async def session_handler(_ws, request, _send_lock):
        dispatched.append(request)

    server._trigger_before_chat_request_hook = before_request
    server._handle_session_list = session_handler

    await server._handle_message(
        _PeerWebSocket("203.0.113.9"),
        _raw_request(ReqMethod.SESSION_LIST),
        asyncio.Lock(),
    )

    assert len(hook_requests) == 1
    assert hook_requests[0].req_method == ReqMethod.SESSION_LIST
    assert len(dispatched) == 1
    assert dispatched[0].req_method == ReqMethod.SESSION_LIST
    assert capture_wire == []


@pytest.mark.asyncio
async def test_agentserver_validation_is_strict_and_runs_one_probe(
    monkeypatch, capture_wire
):
    monkeypatch.setenv("JIUWENSWARM_CODEX_SUBSCRIPTION_ENABLED", "1")
    invocations: list[dict] = []

    # The production AgentServer entrypoint performs this registration before
    # constructing AgentWebSocketServer.  This direct unit test mirrors it.
    import jiuwenswarm.integrations.ai4research_subscription.model_client  # noqa: F401

    class FakeModel:
        def __init__(self, *, model_config, model_client_config):
            self._client = object()
            assert model_config.model_name == CODEX_MODEL_ALIAS
            assert model_client_config.client_provider == CODEX_PROVIDER_NAME
            assert model_client_config.api_key == ""
            assert model_client_config.api_base == ""

        async def invoke(self, messages, **kwargs):
            assert current_codex_consumer() is CodexConsumer.UNCLASSIFIED
            permit = kwargs.pop(CODEX_CALL_PERMIT_KWARG)
            assert (
                consume_codex_call_permit(self._client, permit)
                is CodexConsumer.CONFIG_VALIDATION
            )
            invocations.append({"messages": messages, "kwargs": kwargs})
            return type("Result", (), {"content": "hello"})()

    import openjiuwen.core.foundation.llm as llm_module

    monkeypatch.setattr(llm_module, "Model", FakeModel)
    server = agent_ws_server_module.AgentWebSocketServer.__new__(
        agent_ws_server_module.AgentWebSocketServer
    )
    request = AgentRequest(
        request_id="req-validate",
        channel_id="web",
        req_method=ReqMethod.CODEX_VALIDATE_MODEL,
        params={
            "model_provider": CODEX_PROVIDER_NAME,
            "model": CODEX_MODEL_ALIAS,
        },
    )

    await server._handle_codex_validate_model(
        _PeerWebSocket("127.0.0.1"), request, asyncio.Lock()
    )

    assert len(invocations) == 1
    assert capture_wire[-1] == {
        "response_id": "req-validate",
        "ok": True,
        "payload": {
            "validated": True,
            "model_provider": CODEX_PROVIDER_NAME,
            "model": CODEX_MODEL_ALIAS,
            "response": "hello",
        },
    }


@pytest.mark.asyncio
async def test_agentserver_validation_rejects_extra_fields_before_model_creation(
    monkeypatch,
    capture_wire,
):
    import openjiuwen.core.foundation.llm as llm_module

    monkeypatch.setattr(
        llm_module,
        "Model",
        lambda **_kwargs: pytest.fail("invalid request constructed a model client"),
    )
    server = agent_ws_server_module.AgentWebSocketServer.__new__(
        agent_ws_server_module.AgentWebSocketServer
    )
    request = AgentRequest(
        request_id="req-invalid",
        channel_id="web",
        req_method=ReqMethod.CODEX_VALIDATE_MODEL,
        params={
            "model_provider": CODEX_PROVIDER_NAME,
            "model": CODEX_MODEL_ALIAS,
            "api_key": "must-not-cross-boundary",
        },
    )

    await server._handle_codex_validate_model(
        _PeerWebSocket("127.0.0.1"), request, asyncio.Lock()
    )

    assert capture_wire[-1]["ok"] is False
    assert capture_wire[-1]["payload"]["code"] == "invalid_request"


@pytest.mark.asyncio
async def test_disabled_status_does_not_start_auth_controller(
    monkeypatch, capture_wire
):
    monkeypatch.setenv("JIUWENSWARM_CODEX_SUBSCRIPTION_ENABLED", "0")
    import jiuwenswarm.integrations.ai4research_subscription.auth_controller as auth_module

    monkeypatch.setattr(
        auth_module,
        "get_codex_auth_controller",
        lambda: pytest.fail("disabled status started the auth controller"),
    )
    server = agent_ws_server_module.AgentWebSocketServer.__new__(
        agent_ws_server_module.AgentWebSocketServer
    )
    request = AgentRequest(
        request_id="req-status",
        channel_id="web",
        req_method=ReqMethod.CODEX_AUTH_STATUS,
        params={},
    )

    await server._handle_codex_auth(
        _PeerWebSocket("127.0.0.1"), request, asyncio.Lock()
    )

    assert capture_wire[-1] == {
        "response_id": "req-status",
        "ok": True,
        "payload": {
            "provider": CODEX_PROVIDER_NAME,
            "enabled": False,
            "connected": False,
            "state": "disabled",
        },
    }


def test_capability_import_does_not_register_codex_model_client(tmp_path):
    env = os.environ.copy()
    env["JIUWENSWARM_DATA_DIR"] = str(tmp_path / "jiuwen")
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; "
                "import jiuwenswarm.integrations.ai4research_subscription.provider_capabilities; "
                "print('jiuwenswarm.integrations.ai4research_subscription.model_client' in sys.modules)"
            ),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )

    assert completed.stdout.strip().splitlines()[-1] == "False"
