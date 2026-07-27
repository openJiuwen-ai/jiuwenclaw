from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jiuwenswarm.gateway.auth.credential_authenticator import AuthContext, AuthResult
from jiuwenswarm.gateway.channel_manager.tui.tui_connect import _handle_connect


class FakeWebSocket:
    def __init__(self):
        self.closed = False

    async def close(self):
        self.closed = True


_MOCK_MODULE = "jiuwenswarm.gateway.channel_manager.tui.tui_connect"


@pytest.mark.asyncio
async def test_handle_connect_success():
    ws = FakeWebSocket()
    mock_authenticator = AsyncMock()
    mock_authenticator.authenticate.return_value = AuthResult(
        success=True, user_id="user-1"
    )

    with (
        patch(f"{_MOCK_MODULE}.extract_token", return_value="valid-token"),
        patch(f"{_MOCK_MODULE}.extract_headers", return_value={"Authorization": "Bearer valid-token"}),
        patch(f"{_MOCK_MODULE}.get_remote_addr", return_value="127.0.0.1:9000"),
        patch(f"{_MOCK_MODULE}.get_auth_handler", return_value=mock_authenticator),
    ):
        await _handle_connect(None, ws, "/tui")

    assert not ws.closed
    mock_authenticator.authenticate.assert_awaited_once()
    ctx = mock_authenticator.authenticate.call_args[0][0]
    assert isinstance(ctx, AuthContext)
    assert ctx.channel_type == "tui"
    assert ctx.credentials == {"token": "valid-token"}
    assert ctx.headers == {"Authorization": "Bearer valid-token"}
    assert ctx.remote_addr == "127.0.0.1:9000"


@pytest.mark.asyncio
async def test_handle_connect_auth_failure_closes_ws():
    ws = FakeWebSocket()
    mock_authenticator = AsyncMock()
    mock_authenticator.authenticate.return_value = AuthResult(
        success=False, error="Invalid token"
    )

    with (
        patch(f"{_MOCK_MODULE}.extract_token", return_value="bad-token"),
        patch(f"{_MOCK_MODULE}.extract_headers", return_value={}),
        patch(f"{_MOCK_MODULE}.get_remote_addr", return_value="10.0.0.1:1234"),
        patch(f"{_MOCK_MODULE}.get_auth_handler", return_value=mock_authenticator),
    ):
        await _handle_connect(None, ws, "/tui")

    assert ws.closed


@pytest.mark.asyncio
async def test_handle_connect_exception_closes_ws_and_reraises():
    ws = FakeWebSocket()
    mock_authenticator = AsyncMock()
    mock_authenticator.authenticate.side_effect = RuntimeError("auth service down")

    with (
        patch(f"{_MOCK_MODULE}.extract_token", return_value="token"),
        patch(f"{_MOCK_MODULE}.extract_headers", return_value={}),
        patch(f"{_MOCK_MODULE}.get_remote_addr", return_value=""),
        patch(f"{_MOCK_MODULE}.get_auth_handler", return_value=mock_authenticator),
    ):
        with pytest.raises(RuntimeError, match="auth service down"):
            await _handle_connect(None, ws, "/tui")

    assert ws.closed


@pytest.mark.asyncio
async def test_handle_connect_builds_auth_context_correctly():
    ws = FakeWebSocket()
    captured_context = None

    async def _capture_authenticate(context):
        nonlocal captured_context
        captured_context = context
        return AuthResult(success=True, user_id="user-ctx")

    mock_authenticator = MagicMock()
    mock_authenticator.authenticate = _capture_authenticate

    with (
        patch(f"{_MOCK_MODULE}.extract_token", return_value="ctx-token"),
        patch(f"{_MOCK_MODULE}.extract_headers", return_value={"X-Token": "ctx-token", "X-Custom": "value"}),
        patch(f"{_MOCK_MODULE}.get_remote_addr", return_value="192.168.1.1:8080"),
        patch(f"{_MOCK_MODULE}.get_auth_handler", return_value=mock_authenticator),
    ):
        await _handle_connect(None, ws, "/tui")

    assert captured_context is not None
    assert captured_context.channel_type == "tui"
    assert captured_context.credentials == {"token": "ctx-token"}
    assert captured_context.headers == {"X-Token": "ctx-token", "X-Custom": "value"}
    assert captured_context.remote_addr == "192.168.1.1:8080"
