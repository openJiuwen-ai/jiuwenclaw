"""测试 web_connect.py 中的 extract_token、extract_headers、get_remote_addr、_handle_connect"""
import pytest
from unittest.mock import MagicMock, AsyncMock

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

from jiuwenswarm.gateway.channel_manager.web.web_connect import WebChannel, WebChannelConfig
from jiuwenswarm.gateway.auth.credential_authenticator import AuthContext, AuthResult


# ── Fixture ──────────────────────────────────────────

@pytest.fixture
def channel():
    config = MagicMock(spec=WebChannelConfig)
    router = MagicMock()
    return WebChannel(config, router)


# ── TestExtractToken ─────────────────────────────────

class TestExtractToken:

    def test_from_query_param(self, channel):
        """从 URL 查询参数 ?token=xxx 提取"""
        ws = MagicMock()
        ws.path = "/ws?token=my-test-token"
        ws.request = None
        ws.request_headers = None
        assert channel.extract_token(ws) == "my-test-token"

    def test_from_authorization_header(self, channel):
        """从 Authorization: Bearer xxx 提取"""
        ws = MagicMock()
        ws.path = "/ws"
        ws.request = None
        ws.request_headers = {"Authorization": "Bearer header-token"}
        assert channel.extract_token(ws) == "header-token"

    def test_from_x_token_header(self, channel):
        """从 X-Token: xxx 提取"""
        ws = MagicMock()
        ws.path = "/ws"
        ws.request = None
        ws.request_headers = {"X-Token": "x-token-value"}
        assert channel.extract_token(ws) == "x-token-value"

    def test_no_token_returns_none(self, channel):
        """没有 token 时返回 None"""
        ws = MagicMock()
        ws.path = "/ws"
        ws.request = None
        ws.request_headers = {}
        assert channel.extract_token(ws) is None

    def test_authorization_without_bearer(self, channel):
        """Authorization 不以 Bearer 开头时不提取"""
        ws = MagicMock()
        ws.path = "/ws"
        ws.request = None
        ws.request_headers = {"Authorization": "Basic xxx"}
        assert channel.extract_token(ws) is None

    def test_from_request_attr_headers(self, channel):
        """兼容 ws.request.headers 属性"""
        ws = MagicMock()
        ws.path = "/ws"
        ws.request = MagicMock()
        ws.request.headers = {"Authorization": "Bearer req-token"}
        ws.request_headers = None
        assert channel.extract_token(ws) == "req-token"


# ── TestExtractHeaders ───────────────────────────────

class TestExtractHeaders:

    def test_returns_dict(self, channel):
        """提取请求头为字典"""
        ws = MagicMock()
        ws.request = None
        ws.request_headers = {"Host": "localhost"}
        assert channel.extract_headers(ws) == {"Host": "localhost"}

    def test_returns_empty_dict_when_none(self, channel):
        """没有请求头时返回空字典"""
        ws = MagicMock()
        ws.request = None
        ws.request_headers = None
        assert channel.extract_headers(ws) == {}

    def test_from_request_attr(self, channel):
        """兼容 ws.request.headers 属性"""
        ws = MagicMock()
        ws.request = MagicMock()
        ws.request.headers = {"X-Custom": "value"}
        ws.request_headers = None
        assert channel.extract_headers(ws) == {"X-Custom": "value"}


# ── TestGetRemoteAddr ────────────────────────────────

class TestGetRemoteAddr:

    def test_from_tuple(self, channel):
        """从 remote_address 元组提取"""
        ws = MagicMock()
        ws.remote_address = ("192.168.1.1", 8080)
        assert channel.get_remote_addr(ws) == "('192.168.1.1', 8080)"

    def test_returns_empty_when_none(self, channel):
        """remote_address 为 None 时返回空字符串"""
        ws = MagicMock()
        ws.remote_address = None
        assert channel.get_remote_addr(ws) == ""


# ── TestHandleConnect ────────────────────────────────
#PASS
class TestHandleConnect:

    @pytest.mark.asyncio
    async def test_auth_success_does_not_close(self, channel):
        """认证成功时不关闭连接"""
        ws = AsyncMock()
        ws.path = "/ws?token=valid-token"
        ws.request = None
        ws.request_headers = {}
        ws.remote_address = ("10.0.0.1", 12345)

        from jiuwenswarm.gateway.channel_manager.web import web_connect
        mock_auth = AsyncMock()
        mock_auth.authenticate.return_value = AuthResult(success=True, user_id="test")
        web_connect.get_auth_handler = MagicMock(return_value=mock_auth)

        await channel._handle_connect(ws, "/ws")
        ws.close.assert_not_called()

    @pytest.mark.asyncio
    async def test_auth_failure_closes_connection(self, channel):
        """认证失败时关闭连接"""
        ws = AsyncMock()
        ws.path = "/ws"
        ws.request = None
        ws.request_headers = {}
        ws.remote_address = ("10.0.0.1", 12345)

        from jiuwenswarm.gateway.channel_manager.web import web_connect
        mock_auth = AsyncMock()
        mock_auth.authenticate.return_value = AuthResult(success=False, error="auth failed")
        web_connect.get_auth_handler = MagicMock(return_value=mock_auth)

        await channel._handle_connect(ws, "/ws")
        ws.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_auth_exception_does_not_close(self, channel):
        """认证抛异常时，ws.close 不会被调用（当前源码无 try/except）"""
        ws = AsyncMock()
        ws.path = "/ws?token=valid-token"
        ws.request = None
        ws.request_headers = {}
        ws.remote_address = ("10.0.0.1", 12345)

        from jiuwenswarm.gateway.channel_manager.web import web_connect
        mock_auth = AsyncMock()
        mock_auth.authenticate.side_effect = Exception("Service unavailable")
        web_connect.get_auth_handler = MagicMock(return_value=mock_auth)

        with pytest.raises(Exception):
            await channel._handle_connect(ws, "/ws")
        ws.close.assert_not_called()