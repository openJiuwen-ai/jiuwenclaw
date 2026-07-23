"""测试 credential_authenticator.py 的数据模型和抽象基类"""
import pytest
from datetime import timedelta, datetime
from jiuwenswarm.gateway.auth.credential_authenticator import (
    AuthContext,
    AuthResult,
    KeyPair,
    SSHCertificate,
    TokenAuthenticator,
    SSHAuthenticator,
    CredentialManager,
    UnsupportedOperationError,
)


#PASS
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

    def test_default_values(self):
        context = AuthContext()
        assert context.channel_type == ""
        assert context.credentials == {}
        assert context.headers == {}
        assert context.remote_addr == ""

#PASS
class TestAuthResult:
    """测试 AuthResult 数据类"""

    def test_success_result(self):
        result = AuthResult(
            success=True,
            user_id="user-123",
            extensions={"role": "admin"},
        )
        assert result.success is True
        assert result.user_id == "user-123"
        assert result.error == ""

    def test_failure_result(self):
        result = AuthResult(success=False, error="Token 无效")
        assert result.success is False
        assert result.error == "Token 无效"

    def test_default_values(self):
        result = AuthResult(success=True)
        assert result.user_id == ""
        assert result.error == ""
        assert result.extensions == {}

#PASS
class TestKeyPair:
    def test_create_keypair(self):
        kp = KeyPair(public_key="ssh-rsa AAA...", private_key="key")
        assert kp.public_key == "ssh-rsa AAA..."
        assert kp.private_key == "key"

#PASS
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

#PASS
class TestTokenAuthenticator:
    """测试 TokenAuthenticator 抽象接口"""

    def test_cannot_instantiate_abstract(self):
        with pytest.raises(TypeError):
            TokenAuthenticator()

    def test_concrete_subclass_can_instantiate(self):
        class FullAuthenticator(TokenAuthenticator):
            async def authenticate(self, context):
                return AuthResult(success=True, user_id="test")
            def generate_api_key(self):
                return "ak-test-key"
            def compute_api_key_hmac(self, api_key, secret_key):
                return "hmac-value"
        auth = FullAuthenticator()
        assert isinstance(auth, TokenAuthenticator)

#PASS
class TestSSHAuthenticator:
    """测试 SSHAuthenticator 抽象接口"""

    def test_cannot_instantiate_abstract(self):
        with pytest.raises(TypeError):
            SSHAuthenticator()

    def test_concrete_subclass_can_instantiate(self):
        class FullAuthenticator(SSHAuthenticator):
            async def authenticate(self, context):
                return AuthResult(success=True, user_id="test")
            def generate_user_keypair(self):
                return KeyPair(public_key="ssh-rsa AAA...", private_key="key")
            def generate_ssh_certificate(self, public_key, user_id, validity):
                return SSHCertificate(
                    public_key=public_key,
                    certificate="cert",
                    expires_at=datetime(2026, 12, 31, 23, 59, 59),
                )
        auth = FullAuthenticator()
        assert isinstance(auth, SSHAuthenticator)

#PASS
class TestCredentialManager:
    """测试 CredentialManager 抽象接口"""

    def test_cannot_instantiate_abstract(self):
        with pytest.raises(TypeError):
            CredentialManager()

    def test_concrete_subclass_can_instantiate(self):
        class FullManager(CredentialManager):
            def generate_api_key(self):
                return "ak-test-key"
            def generate_user_keypair(self):
                return KeyPair(public_key="ssh-rsa AAA...", private_key="key")
            def generate_ssh_certificate(self, public_key, user_id, validity):
                return SSHCertificate(
                    public_key=public_key,
                    certificate="cert",
                    expires_at=datetime(2026, 12, 31, 23, 59, 59),
                )
            def compute_api_key_hmac(self, api_key, secret_key):
                return "hmac-value"
        mgr = FullManager()
        assert isinstance(mgr, CredentialManager)

#PASS
class TestUnsupportedOperationError:
    def test_exception_can_be_raised(self):
        with pytest.raises(UnsupportedOperationError):
            raise UnsupportedOperationError("not supported")