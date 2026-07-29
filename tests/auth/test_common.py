import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from jiuwenswarm.gateway.auth.credential_authenticator import AuthContext, AuthResult
from jiuwenswarm.gateway.auth.common import (
    extract_token,
    extract_headers,
    get_remote_addr,
    _handle_connect,
)


class FakeWs:
    def __init__(self, path="", headers=None, remote_address=None, request_headers=None):
        self.path = path
        self._request = MagicMock()
        self._request.headers = headers
        if request_headers is not None:
            self.request_headers = request_headers
        else:
            self.request_headers = None
        self.remote_address = remote_address
        self.closed = False
        self.user_id = None

    @property
    def request(self):
        return self._request if self._request.headers is not None else None

    def close(self):
        self.closed = True


class TestExtractToken:

    def test_from_query_param(self):
        ws = FakeWs(path="/ws?token=abc123")
        assert extract_token(ws) == "abc123"

    def test_from_authorization_header(self):
        ws = FakeWs(headers={"Authorization": "Bearer mytoken"})
        assert extract_token(ws) == "mytoken"

    def test_from_x_token_header(self):
        ws = FakeWs(headers={"X-Token": "xtoken999"})
        assert extract_token(ws) == "xtoken999"

    def test_query_param_priority_over_header(self):
        ws = FakeWs(path="/ws?token=query-token", headers={"Authorization": "Bearer header-token"})
        assert extract_token(ws) == "query-token"

    def test_no_token(self):
        ws = FakeWs(path="/ws")
        assert extract_token(ws) is None

    def test_empty_path(self):
        ws = FakeWs()
        assert extract_token(ws) is None

    def test_bearer_without_prefix(self):
        ws = FakeWs(headers={"Authorization": "token-without-bearer"})
        assert extract_token(ws) is None


class TestExtractHeaders:

    def test_from_request_headers(self):
        ws = FakeWs(headers={"Authorization": "Bearer x", "X-Custom": "val"})
        result = extract_headers(ws)
        assert result["Authorization"] == "Bearer x"
        assert result["X-Custom"] == "val"

    def test_from_request_headers_attr(self):
        ws = FakeWs(request_headers={"X-Alt": "alt-val"})
        result = extract_headers(ws)
        assert result["X-Alt"] == "alt-val"

    def test_request_headers_fallback(self):
        ws = FakeWs()
        ws._request.headers = None
        ws.request_headers = {"X-Fallback": "fb"}
        result = extract_headers(ws)
        assert result["X-Fallback"] == "fb"

    def test_no_headers(self):
        ws = FakeWs()
        ws._request.headers = None
        assert extract_headers(ws) == {}


class TestGetRemoteAddr:

    def test_tuple_address(self):
        ws = FakeWs(remote_address=("127.0.0.1", 9000))
        assert get_remote_addr(ws) == "127.0.0.1:9000"

    def test_list_address(self):
        ws = FakeWs(remote_address=["10.0.0.1", 8080])
        assert get_remote_addr(ws) == "10.0.0.1:8080"

    def test_string_address(self):
        ws = FakeWs(remote_address="192.168.1.1")
        assert get_remote_addr(ws) == "192.168.1.1"

    def test_no_address(self):
        ws = FakeWs()
        assert get_remote_addr(ws) == ""


class TestHandleConnect:

    @pytest.mark.asyncio
    async def test_auth_success(self):
        ws = FakeWs(path="/ws?token=valid")
        mock_auth = AsyncMock()
        mock_auth.authenticate.return_value = AuthResult(
            success=True, user_id="user-1",
            extensions={"username": "alice", "role": "admin"},
        )
        with patch("jiuwenswarm.gateway.auth.common.get_auth_handler", return_value=mock_auth):
            result = await _handle_connect(ws, "/ws")

        assert result is True
        assert ws.user_id == "user-1"
        assert ws.auth_username == "alice"
        assert ws.auth_role == "admin"
        assert ws.closed is False

    @pytest.mark.asyncio
    async def test_auth_failure(self):
        ws = FakeWs(path="/ws?token=bad")
        mock_auth = AsyncMock()
        mock_auth.authenticate.return_value = AuthResult(success=False, error="Invalid token")
        with patch("jiuwenswarm.gateway.auth.common.get_auth_handler", return_value=mock_auth):
            result = await _handle_connect(ws, "/ws")

        assert result is False
        assert ws.closed is True

    @pytest.mark.asyncio
    async def test_auth_exception(self):
        ws = FakeWs(path="/ws?token=valid")
        mock_auth = AsyncMock()
        mock_auth.authenticate.side_effect = RuntimeError("auth service down")
        with patch("jiuwenswarm.gateway.auth.common.get_auth_handler", return_value=mock_auth):
            result = await _handle_connect(ws, "/ws")

        assert result is False
        assert ws.closed is True

    @pytest.mark.asyncio
    async def test_auth_success_no_extensions(self):
        ws = FakeWs(path="/ws?token=valid")
        mock_auth = AsyncMock()
        mock_auth.authenticate.return_value = AuthResult(success=True, user_id="user-2")
        with patch("jiuwenswarm.gateway.auth.common.get_auth_handler", return_value=mock_auth):
            result = await _handle_connect(ws, "/ws")

        assert result is True
        assert ws.user_id == "user-2"

    @pytest.mark.asyncio
    async def test_auth_context_built_correctly(self):
        ws = FakeWs(path="/ws?token=mytoken", headers={"X-Custom": "val"}, remote_address=("10.0.0.1", 8080))
        mock_auth = AsyncMock()
        mock_auth.authenticate.return_value = AuthResult(success=True, user_id="user-3")

        with patch("jiuwenswarm.gateway.auth.common.get_auth_handler", return_value=mock_auth):
            await _handle_connect(ws, "/ws")

        ctx = mock_auth.authenticate.call_args[0][0]
        assert isinstance(ctx, AuthContext)
        assert ctx.credentials == {"token": "mytoken"}
        assert ctx.headers.get("X-Custom") == "val"
        assert ctx.remote_addr == "10.0.0.1:8080"