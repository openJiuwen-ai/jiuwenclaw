"""Behavioral tests for the AgentServer Claude validation handler.

The dashboard/TUI "Test" button forwards Claude validation to AgentServer's
``_handle_claude_validate_model``. These execute that handler (not just import
it) and assert the wire response for: success, disabled provider, invalid params,
empty response, a typed Claude error (e.g. wrong auth method), and an unexpected
error. The Claude model client is stubbed via a fake ``Model`` so no CLI runs.
"""

from __future__ import annotations

import types

import pytest

import openjiuwen.core.foundation.llm as _llm_pkg
from jiuwenswarm.server import agent_ws_server as A

# Importing the client registers ``llm_AI4RnDClaude`` in the client registry, so
# ``ModelClientConfig`` accepts the provider - exactly as app_agentserver does.
from jiuwenswarm.integrations.ai4research_subscription.claude_model_client import (  # noqa: F401
    ClaudeSubscriptionModelClient,
)
from jiuwenswarm.integrations.ai4research_subscription.claude_constants import (
    CLAUDE_MODEL_ALIAS,
    CLAUDE_PROVIDER_NAME,
)
from jiuwenswarm.integrations.ai4research_subscription.claude_consumer_policy import (
    CLAUDE_SUBSCRIPTION_ENABLED_ENV,
)
from jiuwenswarm.integrations.ai4research_subscription.errors import ClaudeProviderError

_VALID_PARAMS = {"model_provider": CLAUDE_PROVIDER_NAME, "model": CLAUDE_MODEL_ALIAS}


async def _run_handler(monkeypatch, *, params, invoke_result="ok", invoke_exc=None, enabled=True):
    # Default-enabled: only an explicit kill-switch value disables the provider.
    if enabled:
        monkeypatch.delenv(CLAUDE_SUBSCRIPTION_ENABLED_ENV, raising=False)
    else:
        monkeypatch.setenv(CLAUDE_SUBSCRIPTION_ENABLED_ENV, "off")

    class _FakeModel:
        def __init__(self, *a, **k):
            pass

        async def invoke(self, messages, **kwargs):
            if invoke_exc is not None:
                raise invoke_exc
            return types.SimpleNamespace(content=invoke_result)

    monkeypatch.setattr(_llm_pkg, "Model", _FakeModel, raising=False)

    captured = []
    monkeypatch.setattr(A, "encode_agent_response_for_wire", lambda response, response_id=None: response)

    async def _capture(ws, wire):
        captured.append(wire)

    monkeypatch.setattr(A, "send_wire_payload", _capture)

    server = A.AgentWebSocketServer.__new__(A.AgentWebSocketServer)
    request = types.SimpleNamespace(request_id="r1", channel_id="web", metadata={}, params=params)
    import asyncio

    await server._handle_claude_validate_model(object(), request, asyncio.Lock())
    assert len(captured) == 1
    return captured[0]


@pytest.mark.asyncio
async def test_validate_success(monkeypatch):
    resp = await _run_handler(monkeypatch, params=dict(_VALID_PARAMS), invoke_result="hello")
    assert resp.ok is True
    assert resp.payload["validated"] is True
    assert resp.payload["model_provider"] == CLAUDE_PROVIDER_NAME
    assert resp.payload["model"] == CLAUDE_MODEL_ALIAS


@pytest.mark.asyncio
async def test_validate_disabled_provider(monkeypatch):
    resp = await _run_handler(monkeypatch, params=dict(_VALID_PARAMS), enabled=False)
    assert resp.ok is False
    assert resp.payload["code"] == "provider_disabled"


@pytest.mark.asyncio
async def test_validate_rejects_extra_params(monkeypatch):
    resp = await _run_handler(monkeypatch, params={**_VALID_PARAMS, "api_key": "sk"})
    assert resp.ok is False
    assert resp.payload["code"] == "invalid_request"


@pytest.mark.asyncio
async def test_validate_rejects_wrong_provider(monkeypatch):
    resp = await _run_handler(monkeypatch, params={"model_provider": "SomethingElse", "model": CLAUDE_MODEL_ALIAS})
    assert resp.ok is False
    assert resp.payload["code"] == "invalid_request"


@pytest.mark.asyncio
async def test_validate_empty_response_fails(monkeypatch):
    resp = await _run_handler(monkeypatch, params=dict(_VALID_PARAMS), invoke_result="   ")
    assert resp.ok is False
    assert resp.payload["code"] == "invalid_output"


@pytest.mark.asyncio
async def test_validate_surfaces_typed_claude_error(monkeypatch):
    resp = await _run_handler(
        monkeypatch,
        params=dict(_VALID_PARAMS),
        invoke_exc=ClaudeProviderError("auth_wrong_method", "not a subscription"),
    )
    assert resp.ok is False
    assert resp.payload["code"] == "auth_wrong_method"


@pytest.mark.asyncio
async def test_validate_wraps_unexpected_error(monkeypatch):
    resp = await _run_handler(
        monkeypatch, params=dict(_VALID_PARAMS), invoke_exc=RuntimeError("boom")
    )
    assert resp.ok is False
    assert resp.payload["code"] == "provider_failed"
