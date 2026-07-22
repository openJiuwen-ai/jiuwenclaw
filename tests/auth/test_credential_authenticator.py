"""测试 credential_authenticator.py 的数据模型和抽象基类"""
import pytest
from datetime import timedelta, datetime
from jiuwenswarm.gateway.auth.credential_authenticator import (
    AuthContext,
    AuthResult,
    KeyPair,
    SSHCertificate,
    CredentialAuthenticator,
    UnsupportedOperationError,
)


class TestAuthContext:
    """测试 AuthContext 数据类"""

    def test_create_with_all_fields(self):
        context = AuthContext(
            channel_type="web",
            credentials={"token": "test-token"},
            headers={"User-Agent": "test"},
            remote_addr="127.0.0.1",
        )
        assert context.channel_type == "web"
        assert context.credentials == {"token": "test-token"}
        assert context.headers == {"User-Agent": "test"}
        assert context.remote_addr == "127.0.0.1"

    def test_create_with_minimal_fields(self):
        context = AuthContext(
            channel_type="ssh",
            credentials={"public_key": "ssh-rsa AAA..."},
            remote_addr="10.0.0.1",
        )
        assert context.channel_type == "ssh"
        assert context.remote_addr == "10.0.0.1"

    def test_channel_type_values(self):
        for channel in ["web", "tui", "ssh"]:
            context = AuthContext(
                channel_type=channel, credentials={}, remote_addr="",
            )
            assert context.channel_type == channel


class TestAuthResult:
    """测试 AuthResult 数据类"""

    def test_success_result(self):
        result = AuthResult(
            success=True,
            user_id="user-123",
            extensions={"role": "admin", "auth_method": "token"},
 )
        assert result.success is True
        assert result.user_id == "user-123"
        assert result.error == ""
        assert result.extensions == {"role": "admin", "auth_method": "token"}

    def test_failure_result(self):
        result = AuthResult(
            success=False,
            error="Token 无效",
            extensions={"error_code": "TOKEN_INVALID"},
        )
        assert result.success is False
        assert result.user_id == ""
        assert result.error == "Token 无效"
        assert result.extensions == {"error_code": "TOKEN_INVALID"}

    def test_default_values(self):
        result = AuthResult(success=True)
        assert result.user_id == ""
        assert result.error == ""
        assert result.extensions == {}

    def test_extensions_isolation(self):
        result1 = AuthResult(success=True, extensions={"key": "value1"})
        result2 = AuthResult(success=True, extensions={"key": "value2"})
        assert result1.extensions["key"] == "value1"
        assert result2.extensions["key"] == "value2"


class TestKeyPair:
    def test_create_keypair(self):
        kp = KeyPair(
            public_key="ssh-rsa AAAAB3...",
            private_key="-----BEGIN RSA PRIVATE KEY-----\n...",
        )
        assert kp.public_key.startswith("ssh-rsa")
        assert "BEGIN RSA PRIVATE KEY" in kp.private_key


class TestSSHCertificate:
    def test_create_certificate(self):
        expires = datetime(2026, 12, 31, 23, 59, 59)
        cert = SSHCertificate(
            public_key="ssh-rsa AAA...",
            certificate="ssh-rsa-cert-v01@openssh.com AAA...",
            expires_at=expires,
        )
        assert cert.public_key.startswith("ssh-rsa")
        assert cert.expires_at == expires


class TestCredentialAuthenticator:
    def test_cannot_instantiate_abstract(self):
        with pytest.raises(TypeError):
            CredentialAuthenticator()

    def test_concrete_subclass_must_implement_all_methods(self):
        class IncompleteAuthenticator(CredentialAuthenticator):
            pass
        with pytest.raises(TypeError):
            IncompleteAuthenticator()

    def test_concrete_subclass_can_instantiate(self):
        class FullAuthenticator(CredentialAuthenticator):
            async def authenticate(self, context):
                return AuthResult(success=True, user_id="test")
            def generate_api_key(self):
                raise UnsupportedOperationError()
            def generate_user_keypair(self):
                raise UnsupportedOperationError()
            def generate_ssh_certificate(self, public_key, user_id, validity):
                raise UnsupportedOperationError()
            def compute_api_key_hmac(self, api_key, secret_key):
                raise UnsupportedOperationError()
        auth = FullAuthenticator()
        assert isinstance(auth, CredentialAuthenticator)


class TestUnsupportedOperationError:
    def test_exception_can_be_raised(self):
        with pytest.raises(UnsupportedOperationError):
            raise UnsupportedOperationError("not supported")

    def test_exception_message(self):
        try:
            raise UnsupportedOperationError("custom message")
        except UnsupportedOperationError as e:
            assert str(e) == "custom message"
