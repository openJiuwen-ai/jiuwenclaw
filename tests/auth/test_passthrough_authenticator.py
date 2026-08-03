"""测试 passthrough_authenticator.py"""
import pytest

from jiuwenswarm.extensions.agentos.auth.credential_authenticator import (
    AuthContext,
    AuthResult,
    CredentialAuthenticator,
)
from jiuwenswarm.extensions.agentos.auth.passthrough_authenticator import (
    PassthroughAuthenticator,
)


class TestPassthroughAuthenticator:

    def test_subclass_of_credential_authenticator(self):
        """验证 PassthroughAuthenticator 继承自 CredentialAuthenticator"""
        assert issubclass(PassthroughAuthenticator, CredentialAuthenticator)

    def test_can_instantiate(self):
        """验证实现了抽象方法，可以实例化"""
        auth = PassthroughAuthenticator()
        assert isinstance(auth, PassthroughAuthenticator)

    @pytest.mark.asyncio
    async def test_authenticate_returns_auth_result(self):
        """验证 authenticate 返回 AuthResult 类型"""
        auth = PassthroughAuthenticator()
        result = await auth.authenticate(AuthContext())
        assert isinstance(result, AuthResult)

    @pytest.mark.asyncio
    async def test_authenticate_success_true(self):
        """验证 authenticate 返回 success=True"""
        auth = PassthroughAuthenticator()
        result = await auth.authenticate(AuthContext())
        assert result.success is True

    @pytest.mark.asyncio
    async def test_authenticate_user_id_anonymous(self):
        """验证 authenticate 返回 user_id='anonymous'"""
        auth = PassthroughAuthenticator()
        result = await auth.authenticate(AuthContext())
        assert result.user_id == "anonymous"

    @pytest.mark.asyncio
    async def test_authenticate_error_empty(self):
        """验证 authenticate 返回 error=''"""
        auth = PassthroughAuthenticator()
        result = await auth.authenticate(AuthContext())
        assert result.error == ""

    @pytest.mark.asyncio
    async def test_authenticate_extensions_empty(self):
        """验证 authenticate 返回 extensions={}"""
        auth = PassthroughAuthenticator()
        result = await auth.authenticate(AuthContext())
        assert result.extensions == {}

    @pytest.mark.asyncio
    async def test_authenticate_ignores_context(self):
        """验证 authenticate 忽略传入的 context，始终返回相同结果"""
        auth = PassthroughAuthenticator()

        test_contexts = [
            AuthContext(),
            AuthContext(channel_type="web"),
            AuthContext(channel_type="ssh", credentials={"token": "x"}),
            AuthContext(
                channel_type="tui",
                credentials={"api_key": "k"},
                headers={"X-Auth": "v"},
                remote_addr="10.0.0.1",
            ),
        ]

        for ctx in test_contexts:
            result = await auth.authenticate(ctx)
            assert result.success is True
            assert result.user_id == "anonymous"
            assert result.error == ""
            assert result.extensions == {}