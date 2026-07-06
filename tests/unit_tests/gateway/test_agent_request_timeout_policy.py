import asyncio
from types import SimpleNamespace

import pytest

from jiuwenswarm.gateway.routing.agent_request_timeout import (
    AGENT_SERVER_TIMEOUT_CODE,
    AGENT_SERVER_TIMEOUT_ERROR,
    AgentRequestTimeoutError,
    request_timeout_from_envelope,
    resolve_agent_request_timeout_seconds,
    send_agent_request_with_timeout,
)


def test_resolve_timeout_skips_stream_and_non_tui_requests():
    assert resolve_agent_request_timeout_seconds(
        channel_id="tui",
        method="history.get",
        is_stream=True,
    ) is None
    assert resolve_agent_request_timeout_seconds(
        channel_id="web",
        method="history.get",
        is_stream=False,
    ) is None


def test_resolve_timeout_defaults_tui_unary_before_frontend_window():
    assert resolve_agent_request_timeout_seconds(
        channel_id="tui",
        method="history.get",
        is_stream=False,
    ) == 25.0


def test_resolve_timeout_allows_tui_explicit_sixty_second_window():
    assert resolve_agent_request_timeout_seconds(
        channel_id="tui",
        method="permissions.tools.update",
        is_stream=False,
        client_timeout_ms=60_000,
    ) == 55.0


def test_resolve_timeout_clamps_tui_client_timeout_to_safe_upper_bound():
    assert resolve_agent_request_timeout_seconds(
        channel_id="tui",
        method="history.get",
        is_stream=False,
        client_timeout_ms=600_000,
    ) == 55.0


def test_resolve_timeout_extends_known_permissions_methods_for_older_clients():
    assert resolve_agent_request_timeout_seconds(
        channel_id="tui",
        method="permissions.rules.create",
        is_stream=False,
    ) == 55.0


def test_request_timeout_from_envelope_accepts_metadata_fallback():
    env = SimpleNamespace(
        channel="tui",
        method="history.get",
        is_stream=False,
        metadata={"client_timeout_ms": 60_000},
    )

    assert request_timeout_from_envelope(env) == 55.0


@pytest.mark.asyncio
async def test_send_agent_request_with_timeout_raises_stable_timeout_error(monkeypatch):
    class HangingAgentClient:
        async def send_request(self, env):
            await asyncio.Event().wait()

    env = SimpleNamespace(
        request_id="req-timeout-policy",
        channel="tui",
        method="history.get",
        is_stream=False,
        metadata={},
    )

    with pytest.raises(AgentRequestTimeoutError) as exc_info:
        await send_agent_request_with_timeout(
            HangingAgentClient(),
            env,
            label="test.policy",
            timeout_seconds=0.01,
        )

    assert str(exc_info.value) == AGENT_SERVER_TIMEOUT_ERROR
    assert exc_info.value.code == AGENT_SERVER_TIMEOUT_CODE
