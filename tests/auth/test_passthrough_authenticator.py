"""测试 passthrough_authenticator.py"""
import pytest
from datetime import timedelta
from jiuwenswarm.gateway.auth.passthrough_authenticator import (
    PassthroughAuthenticator,
)
from jiuwenswarm.gateway.auth.credential_authenticator import (
    AuthContext, KeyPair, SSHCertificate, UnsupportedOperationError,
)


class TestPassthroughAuthenticator:

    @pytest.fixture
    def authenticator(self):
        return PassthroughAuthenticator()

    @pytest.mark.asyncio
    async def test_authenticate_always_success(self, authenticator):
        context = AuthContext(
            channel_type="web", credentials={}, remote_addr="127.0.0.1",
        )
        result = await authenticator.authenticate(context)
        assert result.success is True
        assert result.user_id == "anonymous"

    @pytest.mark.asyncio
    async def test_authenticate_ignores_credentials(self, authenticator):
        contexts = [
            AuthContext(channel_type="web", credentials={"token": "any"}, remote_addr=""),
            AuthContext(channel_type="tui", credentials={"token": "any"}, remote_addr=""),
            AuthContext(channel_type="ssh", credentials={"public_key": "any"}, remote_addr=""),
            AuthContext(channel_type="web", credentials={"api_key": "any"}, remote_addr=""),
        ]
        for ctx in contexts:
            result = await authenticator.authenticate(ctx)
            assert result.success is True
            assert result.user_id == "anonymous"

    def test_generate_api_key_format(self, authenticator):
        api_key = authenticator.generate_api_key()
        assert api_key.startswith("ak-")
        assert len(api_key) == 32

    def test_generate_api_key_uniqueness(self, authenticator):
        keys = {authenticator.generate_api_key() for _ in range(100)}
        assert len(keys) == 100

    def test_generate_api_key_contains_only_allowed_chars(self, authenticator):
        import string
        allowed = set(string.ascii_letters + string.digits + "ak-")
        api_key = authenticator.generate_api_key()
        assert all(c in allowed for c in api_key)

    def test_generate_user_keypair_returns_keypair(self, authenticator):
        keypair = authenticator.generate_user_keypair()
        assert isinstance(keypair, KeyPair)
        assert keypair.public_key.startswith("ssh-rsa")
        assert "BEGIN RSA PRIVATE KEY" in keypair.private_key

    def test_generate_user_keypair_public_key_format(self, authenticator):
        keypair = authenticator.generate_user_keypair()
        parts = keypair.public_key.split()
        assert len(parts) >= 2
        assert parts[0] == "ssh-rsa"

    def test_generate_user_keypair_private_key_format(self, authenticator):
        keypair = authenticator.generate_user_keypair()
        assert keypair.private_key.startswith("-----BEGIN RSA PRIVATE KEY-----")
        assert keypair.private_key.endswith("-----END RSA PRIVATE KEY-----\n")

    def test_generate_ssh_certificate_without_ca_raises_error(self, authenticator):
        with pytest.raises(UnsupportedOperationError) as exc_info:
            authenticator.generate_ssh_certificate(
                public_key="ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQ",
                user_id="test-user",
                validity=timedelta(hours=24),
            )
        assert "CA private key not configured" in str(exc_info.value)

    def test_generate_ssh_certificate_with_ca(self):
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.hazmat.backends import default_backend

        ca_private = rsa.generate_private_key(
            public_exponent=65537, key_size=2048, backend=default_backend()
        )
        ca_private_pem = ca_private.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
        user_private = rsa.generate_private_key(
            public_exponent=65537, key_size=2048, backend=default_backend()
        )
        user_public_ssh = user_private.public_key().public_bytes(
            encoding=serialization.Encoding.OpenSSH,
            format=serialization.PublicFormat.OpenSSH,
        ).decode()

        auth = PassthroughAuthenticator(ca_private_key_pem=ca_private_pem)
        cert = auth.generate_ssh_certificate(
            public_key=user_public_ssh,
            user_id="test-user",
            validity=timedelta(hours=24),
        )
        assert isinstance(cert, SSHCertificate)
        assert cert.public_key == user_public_ssh
        assert cert.certificate.startswith("ssh-rsa-cert-v01@openssh.com")
        assert cert.expires_at is not None

    def test_compute_api_key_hmac_returns_hex_string(self, authenticator):
        hmac_value = authenticator.compute_api_key_hmac("test-key", "test-secret")
        assert len(hmac_value) == 64
        assert all(c in "0123456789abcdef" for c in hmac_value)

    def test_compute_api_key_hmac_deterministic(self, authenticator):
        hmac1 = authenticator.compute_api_key_hmac("my-api-key", "my-secret")
        hmac2 = authenticator.compute_api_key_hmac("my-api-key", "my-secret")
        assert hmac1 == hmac2

    def test_compute_api_key_hmac_different_keys_different_output(self, authenticator):
        hmac1 = authenticator.compute_api_key_hmac("key-a", "secret")
        hmac2 = authenticator.compute_api_key_hmac("key-b", "secret")
        assert hmac1 != hmac2

    def test_compute_api_key_hmac_different_secrets_different_output(self, authenticator):
        hmac1 = authenticator.compute_api_key_hmac("same-key", "secret-a")
        hmac2 = authenticator.compute_api_key_hmac("same-key", "secret-b")
        assert hmac1 != hmac2