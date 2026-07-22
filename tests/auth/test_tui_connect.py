"""测试 tui_connect.py 中的 extract_token、extract_headers、get_remote_addr、_handle_connect"""
import pytest
from unittest.mock import MagicMock, AsyncMock
import sys
from jiuwenswarm.gateway.channel_manager.tui import tui_connect

# mock 掉循环导入问题
import jiuwenswarm.gateway
jiuwenswarm.gateway.AgentServerClient = MagicMock()

# 初始化 ExtensionRegistry
from jiuwenswarm.extensions.registry import ExtensionRegistry
from openjiuwen.core.runner.callback.framework import AsyncCallbackFramework
ExtensionRegistry.create_instance(
    callback_framework=MagicMock(spec=AsyncCallbackFramework),
    config={},
    logger=MagicMock(),
)

from jiuwenswarm.gateway.channel_manager.tui.tui_connect import (
    extract_token,
    extract_headers,
    get_remote_addr,
    _handle_connect,
)
from jiuwenswarm.gateway.auth.credential_authenticator import (
    AuthContext, AuthResult,
)

#PASS
class TestExtractToken:

    def test_from_query_param(self):
        ws = MagicMock()
        ws.query = "token=my-test-token"
        assert extract_token(ws) == "my-test-token"

    def test_from_authorization_header(self):
        ws = MagicMock()
        ws.query = ""
        ws.request_headers = {"Authorization": "Bearer header-token"}
        assert extract_token(ws) == "header-token"

    def test_no_token_returns_empty(self):
        ws = MagicMock()
        ws.query = ""
        ws.request_headers = {}
        assert extract_token(ws) == ""


#PASS
class TestExtractHeaders:

    def test_returns_dict(self):
        ws = MagicMock()
        ws.request_headers = {"Host": "localhost"}
        assert extract_headers(ws) == {"Host": "localhost"}

    def test_returns_empty_dict_when_none(self):
        ws = MagicMock()
        ws.request_headers = None
        assert extract_headers(ws) == {}

#PASS
class TestGetRemoteAddr:

    def test_from_tuple(self):
        ws = MagicMock()
        ws.remote_address = ("192.168.1.1", 8080)
        assert get_remote_addr(ws) == "192.168.1.1:8080"

    def test_returns_empty_when_none(self):
        ws = MagicMock()
        ws.remote_address = None
        assert get_remote_addr(ws) == ""


class TestHandleConnect:

    @pytest.mark.asyncio
    async def test_auth_success_does_not_close(self):
        ws = AsyncMock()
        ws.query = "token=valid-token"
        ws.request_headers = {}
        ws.remote_address = ("10.0.0.1", 12345)

        mock_auth = AsyncMock()
        mock_auth.authenticate.return_value = AuthResult(success=True, user_id="test")
        tui_connect.get_auth_handler = MagicMock(return_value=mock_auth)

        await _handle_connect(MagicMock(), ws, "/tui")
        ws.close.assert_not_called()

    @pytest.mark.asyncio
    async def test_auth_failure_closes_connection(self):
        ws = AsyncMock()
        ws.query = ""
        ws.request_headers = {}
        ws.remote_address = ("10.0.0.1", 12345)

        mock_auth = AsyncMock()
        mock_auth.authenticate.return_value = AuthResult(success=False, error="auth failed")
        tui_connect.get_auth_handler = MagicMock(return_value=mock_auth)

        await _handle_connect(MagicMock(), ws, "/tui")
        ws.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_auth_exception_closes_connection(self):
        """tui_connect 有 try/except，异常时也会关闭连接"""
        ws = AsyncMock()
        ws.query = ""
        ws.request_headers = {}
        ws.remote_address = ("10.0.0.1", 12345)

        mock_auth = AsyncMock()
        mock_auth.authenticate.side_effect = Exception("Service unavailable")
        tui_connect.get_auth_handler = MagicMock(return_value=mock_auth)

        with pytest.raises(Exception):
            await _handle_connect(MagicMock(), ws, "/tui")
        ws.close.assert_called_once()