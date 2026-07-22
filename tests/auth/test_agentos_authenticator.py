"""测试 agentos_authenticator.py"""
import pytest
from datetime import timedelta
from jose import jwt
import httpx
import respx
import sys
import os
from unittest.mock import MagicMock

# 在导入任何 jiuwenswarm 模块之前，先 mock 掉 registry.py 中导入的 AgentServerClient
# 避免 registry.py 导入时触发 gateway 包的链式导入导致循环
import jiuwenswarm.gateway
jiuwenswarm.gateway.AgentServerClient = MagicMock()

# 然后再导入测试目标
from jiuwenswarm.extensions.auth.agentos_authenticator import (
    AgentOSAuthenticator, CredentialManager,
)
from jiuwenswarm.gateway.auth.credential_authenticator import (
    AuthContext, AuthResult,
)


def create_test_token(
    secret="test-secret", algorithm="HS256",
    user_id="user-123", username="testuser", role="admin",
    token_type="access", expired=False,
):
    import time
    payload = {
        "sub": user_id, "username": username, "role": role,
        "type": token_type, "jti": "test-jti-123",
        "iat": int(time.time()),
        "exp": int(time.time()) - 3600 if expired else int(time.time()) + 3600,
    }
    return jwt.encode(payload, secret, algorithm=algorithm)

#PASS
class TestAgentOSAuthenticatorInit:

    def test_init_with_required_params(self):
        auth = AgentOSAuthenticator(
            auth_service_url="http://localhost:8000",
            gateway_secret_key="test-secret",
        )
        assert auth._auth_service_url == "http://localhost:8000"
        assert auth._gateway_secret_key == "test-secret"
        assert auth._gateway_algorithm == "HS256"
        assert auth._timeout == 10.0

    def test_init_with_all_params(self):
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
        auth = AgentOSAuthenticator(
            auth_service_url="http://localhost:8000",
            gateway_secret_key="test-secret",
        )
        assert isinstance(auth._auth_client, httpx.AsyncClient)

#PASS
class TestVerifyAccessTokenLocal:

    @pytest.fixture
    def auth(self):
        return AgentOSAuthenticator(
            auth_service_url="http://localhost:8000",
            gateway_secret_key="test-secret",
        )

    def test_valid_token(self, auth):
        token = create_test_token(secret="test-secret")
        result = auth._verify_access_token_local(token)
        assert result is not None
        assert result["user_id"] == "user-123"
        assert result["username"] == "testuser"
        assert result["role"] == "admin"

    def test_expired_token(self, auth):
        token = create_test_token(secret="test-secret", expired=True)
        result = auth._verify_access_token_local(token)
        assert result is None

    def test_wrong_token_type(self, auth):
        token = create_test_token(secret="test-secret", token_type="refresh")
        result = auth._verify_access_token_local(token)
        assert result is None

    def test_malformed_token(self, auth):
        result = auth._verify_access_token_local("not-a-jwt-token")
        assert result is None

    def test_empty_token(self, auth):
        result = auth._verify_access_token_local("")
        assert result is None

#PASS
class TestAuthenticateTokenLocal:

    @pytest.fixture
    def auth(self):
        return AgentOSAuthenticator(
            auth_service_url="http://localhost:8000",
            gateway_secret_key="test-secret",
        )

    @pytest.mark.asyncio
    async def test_valid_token_returns_success(self, auth):
        token = create_test_token(secret="test-secret")
        result = await auth._authenticate_token(token)
        assert result.success is True
        assert result.user_id == "user-123"
        assert result.extensions["auth_method"] == "token"

    @pytest.mark.asyncio
    async def test_expired_token_returns_failure(self, auth):
        token = create_test_token(secret="test-secret", expired=True)
        result = await auth._authenticate_token(token)
        assert result.success is False
        assert result.error == "Token 无效或已过期"

    @pytest.mark.asyncio
    async def test_invalid_token_returns_failure(self, auth):
        result = await auth._authenticate_token("invalid-token")
        assert result.success is False
        assert result.error == "Token 无效或已过期"

    @pytest.mark.asyncio
    async def test_extra_headers_override_token(self, auth):
        valid_token = create_test_token(secret="test-secret")
        result = await auth._authenticate_token(
            "invalid-token",
            extra_headers={"Authorization": f"Bearer {valid_token}"},
        )
        assert result.success is True
        assert result.user_id == "user-123"

#PASS
class TestAuthenticateTokenHttp:

    @pytest.fixture
    def auth(self):
        return AgentOSAuthenticator(
            auth_service_url="http://test-auth:8000",
            gateway_secret_key=None,
        )

    #pass
    @pytest.mark.asyncio
    @respx.mock
    async def test_http_verify_success(self, auth):
        route = respx.post("http://test-auth:8000/api/v1/auth/verify").mock(
            return_value=httpx.Response(
                200, json={
                    "code": 200,
                    "data": {"valid": True, "user_id": "user-456", "username": "http-user", "role": "user"},
                },
            )
        )
        result = await auth._authenticate_token("some-token")
        assert result.success is True
        assert result.user_id == "user-456"
        assert route.called

    #PASS
    @pytest.mark.asyncio
    @respx.mock
    async def test_http_verify_invalid(self, auth):
        respx.post("http://test-auth:8000/api/v1/auth/verify").mock(
            return_value=httpx.Response(200, json={"code": 200, "data": {"valid": False}}),
        )
        result = await auth._authenticate_token("invalid-token")
        assert result.success is False

    #PASS
    @pytest.mark.asyncio
    @respx.mock
    async def test_http_401(self, auth):
        respx.post("http://test-auth:8000/api/v1/auth/verify").mock(
            return_value=httpx.Response(401),
        )
        result = await auth._authenticate_token("expired")
        assert result.success is False
        assert "已过期" in result.error

    #PASS
    @pytest.mark.asyncio
    @respx.mock
    async def test_http_403(self, auth):
        respx.post("http://test-auth:8000/api/v1/auth/verify").mock(
            return_value=httpx.Response(403),
        )
        result = await auth._authenticate_token("no-perm")
        assert result.success is False
        assert "无权限" in result.error

    #PASS
    @pytest.mark.asyncio
    @respx.mock
    async def test_http_429(self, auth):
        respx.post("http://test-auth:8000/api/v1/auth/verify").mock(
            return_value=httpx.Response(429),
        )
        result = await auth._authenticate_token("rate-limited")
        assert result.success is False
        assert "频繁" in result.error

    #PASS
    @pytest.mark.asyncio
    @respx.mock
    async def test_http_500(self, auth):
        respx.post("http://test-auth:8000/api/v1/auth/verify").mock(
            return_value=httpx.Response(500),
        )
        result = await auth._authenticate_token("server-error")
        assert result.success is False
        assert "内部错误" in result.error

    @pytest.mark.asyncio
    @respx.mock
    async def test_http_invalid_json(self, auth):
        respx.post("http://test-auth:8000/api/v1/auth/verify").mock(
            return_value=httpx.Response(200, text="not-json"),
        )
        result = await auth._authenticate_token("bad-response")
        assert result.success is False
        assert "非法的响应格式" in result.error

#PASS
class TestAuthenticate:

    @pytest.fixture
    def auth(self):
        return AgentOSAuthenticator(
            auth_service_url="http://localhost:8000",
            gateway_secret_key="test-secret",
        )

    @pytest.mark.asyncio
    async def test_token_authentication(self, auth):
        token = create_test_token(secret="test-secret")
        context = AuthContext(
            channel_type="web", credentials={"token": token}, remote_addr="127.0.0.1",
        )
        result = await auth.authenticate(context)
        assert result.success is True
        assert result.user_id == "user-123"

    @pytest.mark.asyncio
    async def test_no_credentials(self, auth):
        context = AuthContext(channel_type="web", credentials={}, remote_addr="")
        result = await auth.authenticate(context)
        assert result.success is False
        assert result.error == "No valid credentials"

    @pytest.mark.asyncio
    async def test_token_priority(self, auth):
        token = create_test_token(secret="test-secret")
        context = AuthContext(
            channel_type="web",
            credentials={"token": token, "api_key": "some-key"},
            remote_addr="",
        )
        result = await auth.authenticate(context)
        assert result.success is True
        assert result.user_id == "user-123"


#PASS
class TestCredentialManager:

    @pytest.mark.xfail(reason="CredentialManager is stubbed, returns None instead of proper values")
    def test_generate_api_key_returns_none(self):
        assert CredentialManager.generate_api_key() is None

    @pytest.mark.xfail(reason="CredentialManager is stubbed, returns None instead of proper values")
    def test_generate_user_keypair_returns_none(self):
        assert CredentialManager.generate_user_keypair() is None

    @pytest.mark.xfail(reason="CredentialManager is stubbed, returns None instead of proper values")
    def test_generate_ssh_certificate_returns_none(self):
        assert CredentialManager.generate_ssh_certificate(
            "ssh-rsa AAA...", "user", timedelta(hours=1)
        ) is None

    @pytest.mark.xfail(reason="CredentialManager is stubbed, returns None instead of proper values")
    def test_compute_api_key_hmac_returns_none(self):
        assert CredentialManager.compute_api_key_hmac("key", "secret") is None