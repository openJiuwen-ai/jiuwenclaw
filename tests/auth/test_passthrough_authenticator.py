"""测试 passthrough_authenticator.py"""
import pytest
from unittest.mock import MagicMock

# 在导入任何 jiuwenswarm 模块之前，先 mock 掉循环导入链
import jiuwenswarm.gateway
jiuwenswarm.gateway.AgentServerClient = MagicMock()

import jiuwenswarm.gateway.channel_manager.web.web_connect
jiuwenswarm.gateway.channel_manager.web.web_connect.get_auth_handler = MagicMock()

# 然后再导入测试目标
from jiuwenswarm.gateway.auth.passthrough_authenticator import PassthroughAuthenticator
from jiuwenswarm.gateway.auth.credential_authenticator import AuthContext, AuthResult


class TestPassthroughAuthenticator:

    @pytest.fixture
    def auth(self):
        return PassthroughAuthenticator()

    @pytest.mark.asyncio
    async def test_authenticate_always_success(self, auth):
        """验证透传认证总是返回成功"""
        context = AuthContext(
            channel_type="web",
            credentials={"token": "any-token"},
            headers={},
            remote_addr="127.0.0.1",
        )
        result = await auth.authenticate(context)
        assert result.success is True
        assert result.user_id == "anonymous"

    @pytest.mark.asyncio
    async def test_authenticate_with_empty_context(self, auth):
        """验证空上下文也能通过"""
        context = AuthContext()
        result = await auth.authenticate(context)
        assert result.success is True
        assert result.user_id == "anonymous"

    @pytest.mark.asyncio
    async def test_authenticate_with_invalid_token(self, auth):
        """验证无效 token 也能通过（透传模式）"""
        context = AuthContext(
            channel_type="web",
            credentials={"token": "invalid-token"},
        )
        result = await auth.authenticate(context)
        assert result.success is True
        assert result.user_id == "anonymous"

    @pytest.mark.asyncio
    async def test_authenticate_with_no_credentials(self, auth):
        """验证无凭证也能通过（透传模式）"""
        context = AuthContext(channel_type="web", credentials={})
        result = await auth.authenticate(context)
        assert result.success is True
        assert result.user_id == "anonymous"

    @pytest.mark.asyncio
    async def test_authenticate_returns_auth_result(self, auth):
        """验证返回类型是 AuthResult"""
        context = AuthContext(channel_type="web")
        result = await auth.authenticate(context)
        assert isinstance(result, AuthResult)