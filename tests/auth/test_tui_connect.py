"""测试 tui_connect.py 中的 _handle_connect 认证流程"""
import pytest
from unittest.mock import MagicMock, patch, AsyncMock
import sys

# 在导入任何 jiuwenswarm 模块之前，先 mock 掉循环导入
import jiuwenswarm.gateway
jiuwenswarm.gateway.AgentServerClient = MagicMock()

from jiuwenswarm.gateway.channel_manager.tui.tui_connect import _handle_connect
from jiuwenswarm.gateway.auth.credential_authenticator import AuthResult


class TestHandleConnect:

    @pytest.fixture
    def mock_ws(self):
        """创建一个模拟的 WebSocket 对象"""
        ws = MagicMock()
        ws.path = "/ws?token=test-token-123"
        ws.request_headers = {"Authorization": "Bearer test-token-123"}
        ws.remote_address = ("192.168.1.100", 54321)
        return ws

    @pytest.fixture
    def mock_auth_success(self):
        """创建一个返回成功的认证器"""
        auth = AsyncMock()
        auth.authenticate.return_value = AuthResult(
            success=True, user_id="user-123",
            extensions={"auth_method": "token"},
        )
        return auth

    @pytest.fixture
    def mock_auth_failure(self):
        """创建一个返回失败的认证器"""
        auth = AsyncMock()
        auth.authenticate.return_value = AuthResult(
            success=False, error="Invalid token",
        )
        return auth @pytest.mark.asyncio

    async def test_auth_success_does_not_close(self, mock_ws, mock_auth_success):
        """认证成功时不应关闭连接"""
        with patch("jiuwenswarm.gateway.channel_manager.tui.tui_connect.get_auth_handler",
                   return_value=mock_auth_success):
            result = await _handle_connect(self, mock_ws, "/ws")
            assert result is None # 函数没有返回值
            mock_ws.close.assert_not_called()