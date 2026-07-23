"""Behavioral tests for the AgentServer read-only Claude status handler.

Executes ``_handle_claude_status`` (not just imports it) and asserts the wire
payload for: kill-switch disabled, each probed state, and a probe failure. The
probe is stubbed so no CLI runs.
"""

from __future__ import annotations

import asyncio
import types

import pytest

from jiuwenswarm.server import agent_ws_server as A
from jiuwenswarm.integrations.ai4research_subscription import claude_process
from jiuwenswarm.integrations.ai4research_subscription.claude_auth_seam import (
    ClaudeProviderStatus as PS,
)
from jiuwenswarm.integrations.ai4research_subscription.claude_consumer_policy import (
    CLAUDE_SUBSCRIPTION_ENABLED_ENV,
)


async def _run(monkeypatch, *, enabled=True, probe_status=None, probe_exc=None):
    if enabled:
        monkeypatch.delenv(CLAUDE_SUBSCRIPTION_ENABLED_ENV, raising=False)
    else:
        monkeypatch.setenv(CLAUDE_SUBSCRIPTION_ENABLED_ENV, "off")

    async def _fake_probe(self, *, timeout=None):
        if probe_exc is not None:
            raise probe_exc
        return probe_status

    monkeypatch.setattr(claude_process.ClaudeProcessRunner, "probe_status", _fake_probe)

    captured = []
    monkeypatch.setattr(A, "encode_agent_response_for_wire", lambda response, response_id=None: response)

    async def _capture(ws, wire):
        captured.append(wire)

    monkeypatch.setattr(A, "send_wire_payload", _capture)

    server = A.AgentWebSocketServer.__new__(A.AgentWebSocketServer)
    request = types.SimpleNamespace(request_id="r1", channel_id="web", metadata={}, params={})
    await server._handle_claude_status(object(), request, asyncio.Lock())
    assert len(captured) == 1
    return captured[0]


@pytest.mark.asyncio
async def test_status_disabled_by_kill_switch(monkeypatch):
    resp = await _run(monkeypatch, enabled=False, probe_status=PS.SUBSCRIPTION_READY)
    assert resp.ok is True
    assert resp.payload["status"] == "disabled"  # probe not consulted


@pytest.mark.parametrize(
    "state,expected",
    [
        (PS.SUBSCRIPTION_READY, "subscription_ready"),
        (PS.LOGIN_REQUIRED, "login_required"),
        (PS.WRONG_AUTH_METHOD, "wrong_auth_method"),
        (PS.MISSING_CLI, "missing_cli"),
        (PS.WRONG_VERSION, "wrong_version"),
        (PS.AUTH_STATUS_UNVERIFIABLE, "auth_status_unverifiable"),
    ],
)
@pytest.mark.asyncio
async def test_status_reports_probe_state(monkeypatch, state, expected):
    resp = await _run(monkeypatch, enabled=True, probe_status=state)
    assert resp.ok is True
    assert resp.payload["status"] == expected


@pytest.mark.asyncio
async def test_status_probe_failure_is_unverifiable(monkeypatch):
    resp = await _run(monkeypatch, enabled=True, probe_exc=RuntimeError("boom"))
    assert resp.ok is True
    assert resp.payload["status"] == "auth_status_unverifiable"
