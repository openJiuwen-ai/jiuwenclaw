"""测试 agentos_authenticator.py"""
import pytest
import httpx
import respx
# 然后再导入测试目标
from jiuwenswarm.extensions.agentos.agentos_router.agentos_authenticator import (
    AgentOSAuthenticator,
)
from jiuwenswarm.extensions.agentos.auth.credential_authenticator import (
    AuthContext,
    AuthResult,
    CredentialAuthenticator,
)


class TestAgentOSAuthenticatorInit:

    def test_init_with_required_params(self):
        """验证必填参数初始化"""
        auth = AgentOSAuthenticator(auth_service_url="http://localhost:8000")
        assert auth._auth_service_url == "http://localhost:8000"
        assert auth._timeout == 10.0

    def test_init_strips_trailing_slash(self):
        """验证初始化时去除尾部斜杠"""
        auth = AgentOSAuthenticator(auth_service_url="http://localhost:8000/")
        assert auth._auth_service_url == "http://localhost:8000"

    def test_init_with_custom_timeout(self):
        """验证自定义 timeout"""
        auth = AgentOSAuthenticator(auth_service_url="http://localhost:8000", timeout=30.0)
        assert auth._timeout == 30.0

    def test_init_creates_async_client(self):
        """验证初始化时创建了 AsyncClient"""
        auth = AgentOSAuthenticator(auth_service_url="http://localhost:8000")
        assert isinstance(auth._auth_client, httpx.AsyncClient)

    def test_is_credential_authenticator(self):
        """验证 AgentOSAuthenticator 是 CredentialAuthenticator 的子类"""
        auth = AgentOSAuthenticator(auth_service_url="http://localhost:8000")
        assert isinstance(auth, CredentialAuthenticator)


class TestAuthenticateToken:

    @pytest.fixture
    def auth(self):
        return AgentOSAuthenticator(auth_service_url="http://test-auth:8000")

    # ── 空 token 场景 ──

    @pytest.mark.asyncio
    async def test_empty_token(self, auth):
        """验证空 token 返回 MISSING_TOKEN"""
        result = await auth._authenticate_token("")
        assert result.success is False
        assert result.error == "缺少 token"
        assert result.extensions.get("error_code") == "MISSING_TOKEN"

    @pytest.mark.asyncio
    async def test_none_token(self, auth):
        """验证 None token 返回 MISSING_TOKEN"""
        result = await auth._authenticate_token(None)  # type: ignore
        assert result.success is False
        assert result.error == "缺少 token"

    @pytest.mark.asyncio
    async def test_whitespace_token(self, auth):
        """验证空白 token 返回 MISSING_TOKEN"""
        result = await auth._authenticate_token("   ")
        assert result.success is False
        assert result.error == "缺少 token"

    # ── HTTP 成功场景 ──

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
        assert result.extensions["username"] == "http-user"
        assert result.extensions["role"] == "user"
        assert result.extensions["auth_method"] == "token"
        assert route.called

    @pytest.mark.asyncio
    @respx.mock
    async def test_http_verify_success_no_optional_fields(self, auth):
        """验证成功响应中可选字段缺失时的默认值"""
        respx.post("http://test-auth:8000/api/v1/auth/verify").mock(
            return_value=httpx.Response(
                200,
                json={
                    "code": 200,
                    "data": {"valid": True},
                },
            )
        )
        result = await auth._authenticate_token("some-token")
        assert result.success is True
        assert result.user_id == ""
        assert result.extensions["username"] is None
        assert result.extensions["role"] is None

    # ── HTTP 失败场景 ──

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
    async def test_http_verify_invalid_no_error_field(self, auth):
        """验证无效时未提供 error 字段使用默认错误信息"""
        respx.post("http://test-auth:8000/api/v1/auth/verify").mock(
            return_value=httpx.Response(
                200, json={"code": 200, "data": {"valid": False}},
            )
        )
        result = await auth._authenticate_token("invalid-token")
        assert result.success is False
        assert result.error == "Token 无效或已过期"

    # ── HTTP 异常场景 ──

    @pytest.mark.asyncio
    @respx.mock
    async def test_http_invalid_json(self, auth):
        """验证 HTTP 返回非法 JSON"""
        respx.post("http://test-auth:8000/api/v1/auth/verify").mock(
            return_value=httpx.Response(200, text="not-json"),
        )
        result = await auth._authenticate_token("bad-response")
        assert result.success is False
        assert result.extensions.get("error_code") == "INVALID_RESPONSE"

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

    # ── extra_headers 场景 ──

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

    @pytest.mark.asyncio
    @respx.mock
    async def test_extra_headers_without_auth_header(self, auth):
        """验证 extra_headers 中没有 Authorization 时使用原始 token"""
        respx.post("http://test-auth:8000/api/v1/auth/verify").mock(
            return_value=httpx.Response(
                200,
                json={
                    "code": 200,
                    "data": {"valid": True, "user_id": "user-original", "username": "original", "role": "user"},
                },
            )
        )
        result = await auth._authenticate_token(
            "original-token",
            extra_headers={"X-Custom": "value"},
        )
        assert result.success is True
        assert result.user_id == "user-original"

    @pytest.mark.asyncio
    @respx.mock
    async def test_extra_headers_empty_dict(self, auth):
        """验证 extra_headers 为空 dict 时正常使用原始 token"""
        respx.post("http://test-auth:8000/api/v1/auth/verify").mock(
            return_value=httpx.Response(
                200,
                json={
                    "code": 200,
                    "data": {"valid": True, "user_id": "user-123"},
                },
            )
        )
        result = await auth._authenticate_token("some-token", extra_headers={})
        assert result.success is True
        assert result.user_id == "user-123"

    @pytest.mark.asyncio
    @respx.mock
    async def test_extra_headers_passed_to_request(self, auth):
        """验证 extra_headers 被传递到 HTTP 请求中"""
        route = respx.post("http://test-auth:8000/api/v1/auth/verify").mock(
            return_value=httpx.Response(
                200,
                json={
                    "code": 200,
                    "data": {"valid": True, "user_id": "user-789"},
                },
            )
        )
        await auth._authenticate_token(
            "some-token",
            extra_headers={"X-Custom": "custom-value"},
        )
        # 验证请求中包含了 extra_headers
        request = route.calls[0].request
        assert request.headers.get("X-Custom") == "custom-value"
        assert request.headers.get("Authorization") == "Bearer some-token"


class TestAuthenticate:

    @pytest.fixture
    def auth(self):
        return AgentOSAuthenticator(auth_service_url="http://test-auth:8000")

    @pytest.mark.asyncio
    async def test_no_credentials(self, auth):
        """验证无凭证时返回 UNSUPPORTED_CREDENTIAL"""
        context = AuthContext(channel_type="web", credentials={})
        result = await auth.authenticate(context)
        assert result.success is False
        assert result.error == "No valid credentials"
        assert result.extensions.get("error_code") == "UNSUPPORTED_CREDENTIAL"

    @pytest.mark.asyncio
    async def test_none_credentials(self, auth):
        """验证 credentials 为 None 时返回 UNSUPPORTED_CREDENTIAL"""
        context = AuthContext(channel_type="web", credentials=None)  # type: ignore
        result = await auth.authenticate(context)
        assert result.success is False
        assert result.error == "No valid credentials"

    @pytest.mark.asyncio
    @respx.mock
    async def test_token_authentication(self, auth):
        """验证 token 认证路径"""
        respx.post("http://test-auth:8000/api/v1/auth/verify").mock(
            return_value=httpx.Response(
                200,
                json={
                    "code": 200,
                    "data": {"valid": True, "user_id": "user-token", "username": "token-user", "role": "user"},
                },
            )
        )
        context = AuthContext(
            channel_type="web",
            credentials={"token": "valid-token"},
        )
        result = await auth.authenticate(context)
        assert result.success is True
        assert result.user_id == "user-token"

    @pytest.mark.asyncio
    @respx.mock
    async def test_token_empty_in_credentials(self, auth):
        """验证 credentials 中 token 为空字符串"""
        context = AuthContext(
            channel_type="web",
            credentials={"token": ""},
        )
        result = await auth.authenticate(context)
        assert result.success is False
        assert result.error == "缺少 token"

    @pytest.mark.asyncio
    @respx.mock
    async def test_authenticate_passes_headers(self, auth):
        """验证 authenticate 将 context.headers 作为 extra_headers 传入"""
        route = respx.post("http://test-auth:8000/api/v1/auth/verify").mock(
            return_value=httpx.Response(
                200,
                json={
                    "code": 200,
                    "data": {"valid": True, "user_id": "user-header"},
                },
            )
        )
        context = AuthContext(
            channel_type="web",
            credentials={"token": "some-token"},
            headers={"X-Forwarded-For": "10.0.0.1"},
        )
        await auth.authenticate(context)
        request = route.calls[0].request
        assert request.headers.get("X-Forwarded-For") == "10.0.0.1"
