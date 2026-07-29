"""测试 agentos_authenticator.py"""
import pytest
import httpx
import respx
from unittest.mock import MagicMock

# 在导入任何 jiuwenswarm 模块之前，先 mock 掉循环导入链
import jiuwenswarm.gateway
jiuwenswarm.gateway.AgentServerClient = MagicMock()

import jiuwenswarm.gateway.channel_manager.web.web_connect
jiuwenswarm.gateway.channel_manager.web.web_connect.get_auth_handler = MagicMock()

# 然后再导入测试目标
from jiuwenswarm.extensions.agentos.agentos_router.agentos_authenticator import (
    AgentOSAuthenticator,
)
from jiuwenswarm.gateway.auth.credential_authenticator import (
    AuthContext,
    AuthResult,
    CredentialAuthenticator,
)


class TestAgentOSAuthenticatorInit:

    def test_init_with_required_params(self):
        """验证必填参数初始化"""
        auth = AgentOSAuthenticator(
            auth_service_url="http://localhost:8000",
            gateway_secret_key="test-secret",
        )
        assert auth._auth_service_url == "http://localhost:8000"
        assert auth._gateway_secret_key == "test-secret"
        assert auth._gateway_algorithm == "HS256"
        assert auth._timeout == 10.0

    def test_init_with_all_params(self):
        """验证全参数初始化"""
        auth = AgentOSAuthenticator(
            auth_service_url="http://localhost:8000/",
            gateway_secret_key="my-secret",
            jwt_algorithm="HS512",
            timeout=30.0,
        )
        assert auth._auth_service_url == "http://localhost:8000"
        assert auth._gateway_algorithm == "HS512"
        assert auth._timeout == 30.0

    def test_init_creates_async_client(self):
        """验证初始化时创建了 AsyncClient"""
        auth = AgentOSAuthenticator(
            auth_service_url="http://localhost:8000",
            gateway_secret_key="test-secret",
        )
        assert isinstance(auth._auth_client, httpx.AsyncClient)

    def test_is_credential_authenticator(self):
        """验证 AgentOSAuthenticator 是 CredentialAuthenticator 的子类"""
        auth = AgentOSAuthenticator(
            auth_service_url="http://localhost:8000",
            gateway_secret_key="test-secret",
        )
        assert isinstance(auth, CredentialAuthenticator)


class TestAuthenticateTokenHttp:

    @pytest.fixture
    def auth(self):
        return AgentOSAuthenticator(
            auth_service_url="http://test-auth:8000",
            gateway_secret_key="",
        )

    @pytest.mark.asyncio
    @respx.mock
    async def test_http_verify_success(self, auth):
        """验证 HTTP 认证成功"""
        route = respx.post("http://test-auth:8000/api/v1/auth/verify").mock(
            return_value=httpx.Response(
                200,
                json={
                    "code": 200,
                    "data": {
                        "valid": True,
                        "user_id": "user-456",
                        "username": "http-user",
                        "role": "user",
                    },
                },
            )
        )
        result = await auth._authenticate_token("some-token")
        assert result.success is True
        assert result.user_id == "user-456"
        assert route.called

    @pytest.mark.asyncio
    @respx.mock
    async def test_http_verify_invalid(self, auth):
        """验证 HTTP 认证返回无效"""
        respx.post("http://test-auth:8000/api/v1/auth/verify").mock(
            return_value=httpx.Response(
                200, json={"code": 200, "data": {"valid": False, "error": "Token expired"}},
            )
        )
        result = await auth._authenticate_token("invalid-token")
        assert result.success is False
        assert result.error == "Token expired"

    @pytest.mark.asyncio
    @respx.mock
    async def test_http_invalid_json(self, auth):
        """验证 HTTP 返回非法 JSON"""
        respx.post("http://test-auth:8000/api/v1/auth/verify").mock(
            return_value=httpx.Response(200, text="not-json"),
        )
        result = await auth._authenticate_token("bad-response")
        assert result.success is False
        assert "非法的响应格式" in result.error

    @pytest.mark.asyncio
    @respx.mock
    async def test_http_connection_error(self, auth):
        """验证 HTTP 连接错误"""
        respx.post("http://test-auth:8000/api/v1/auth/verify").mock(
            side_effect=httpx.ConnectError("connection refused"),
        )
        result = await auth._authenticate_token("some-token")
        assert result.success is False
        assert "connection refused" in result.error

    @pytest.mark.asyncio
    @respx.mock
    async def test_http_timeout(self, auth):
        """验证 HTTP 超时"""
        respx.post("http://test-auth:8000/api/v1/auth/verify").mock(
            side_effect=httpx.TimeoutException("timeout"),
        )
        result = await auth._authenticate_token("some-token")
        assert result.success is False
        assert "timeout" in result.error

    @pytest.mark.asyncio
    @respx.mock
    async def test_extra_headers_override_token(self, auth):
        """验证 extra_headers 中的 Authorization 覆盖 token 参数"""
        respx.post("http://test-auth:8000/api/v1/auth/verify").mock(
            return_value=httpx.Response(
                200,
                json={
                    "code": 200,
                    "data": {"valid": True, "user_id": "user-override", "username": "override", "role": "user"},
                },
            )
        )
        result = await auth._authenticate_token(
            "original-token",
            extra_headers={"Authorization": "Bearer override-token"},
        )
        assert result.success is True
        assert result.user_id == "user-override"


class TestAuthenticateApiKey:

    @pytest.fixture
    def auth(self):
        a = AgentOSAuthenticator(
            auth_service_url="http://localhost:8000",
            gateway_secret_key="test-secret",
        )
        # 给 auth 对象动态添加 compute_api_key_hmac 方法
        a.compute_api_key_hmac = MagicMock(return_value="mock-hash")
        return a

    def test_no_secret_key(self, auth):
        """验证未配置 gateway_secret_key 时返回错误"""
        auth._gateway_secret_key = ""
        result = auth._authenticate_api_key("some-key")
        assert result.success is False
        assert "未配置" in result.error

    def test_api_key_not_found(self, auth):
        """验证 API Key 未找到"""
        result = auth._authenticate_api_key("unknown-key")
        assert result.success is False
        assert "Invalid" in result.error


class TestAuthenticate:

    @pytest.fixture
    def auth(self):
        return AgentOSAuthenticator(
            auth_service_url="http://localhost:8000",
            gateway_secret_key="test-secret",
        )

    @pytest.mark.asyncio
    async def test_no_credentials(self, auth):
        """验证无凭证时返回失败"""
        context = AuthContext(channel_type="web", credentials={})
        result = await auth.authenticate(context)
        assert result.success is False
        assert result.error == "No valid credentials"

    @pytest.mark.asyncio
    async def test_token_priority(self, auth):
        """验证 token 认证优先级最高"""
        context = AuthContext(
            channel_type="web",
            credentials={
                "token": "some-token",
                "api_key": "some-key",
                "certificate": "some-cert",
                "public_key": "some-pubkey",
            },
        )
        result = await auth.authenticate(context)
        # 没有 gateway_secret_key 时走 HTTP，这里会连接失败
        assert result.success is False
        assert "connection refused" in result.error or "无法连接" in result.error or "ConnectError" in result.error or "connect" in result.error.lower()