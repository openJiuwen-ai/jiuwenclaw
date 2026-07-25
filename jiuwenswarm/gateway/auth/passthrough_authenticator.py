import secrets
import string
import time
from datetime import timedelta
from datetime import datetime, timezone

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.serialization.ssh import (
            SSHCertificateBuilder, SSHCertificateType
)
from cryptography.hazmat.primitives.serialization import (
        load_ssh_public_key, load_pem_private_key
)

from jiuwenswarm.gateway.auth.credential_authenticator import (
    UnsupportedOperationError,
    KeyPair,
    AuthResult,
    CredentialAuthenticator,
    AuthContext,
    SSHCertificate
)


class PassthroughAuthenticator(CredentialAuthenticator):

    def __init__(self, ca_private_key_pem: bytes | None = None):
        self._ca_private_key = ca_private_key_pem

    async def authenticate(self, context: AuthContext) -> AuthResult:
        """认证直接通过，返回默认用户; 是否需要做基础验证？"""
        return AuthResult(success=True, user_id="anonymous")

    def generate_api_key(self) -> str:
        prefix = "ak-"
        # 随机字符串长度 = 总长度 - 前缀长度
        random_length = 32 - len(prefix)
        # 使用安全的随机字符（字母+数字）
        chars = string.ascii_letters + string.digits
        random_part = ''.join(secrets.choice(chars) for _ in range(random_length))
        return prefix + random_part
        # raise UnsupportedOperationError("generate_api_key not supported in PassthroughAuthenticator")

    def generate_user_keypair(self) -> KeyPair:
        # 生成 RSA 私钥（2048 位）
        private_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=2048,
            backend=default_backend()
        )

        # 序列化私钥为 PEM 格式（未加密）
        private_pem = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption()
        )

        # 序列化公钥为 SSH 格式（可直接用于 authorized_keys）
        public_ssh = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.OpenSSH,
            format=serialization.PublicFormat.OpenSSH
        )
        return KeyPair(public_ssh.decode(), private_pem.decode())



    def generate_ssh_certificate(self, public_key: str, user_id: str, validity: timedelta) -> SSHCertificate:
        if self._ca_private_key is None:
            raise UnsupportedOperationError("generate_ssh_certificate not supported: CA private key not configured")

        ca_key = load_pem_private_key(self._ca_private_key, password=None)
        user_pub_key = load_ssh_public_key(public_key.encode())

        expires_at = datetime.now(timezone.utc) + validity
        builder = SSHCertificateBuilder()
        builder = builder.type(SSHCertificateType.USER)
        builder = builder.serial(int(time.time()))
        builder = builder.valid_after(int(time.time()))
        builder = builder.valid_before(int(expires_at.timestamp()))
        builder = builder.key_id(user_id.encode())
        builder = builder.public_key(user_pub_key)
        builder = builder.valid_principals([user_id.encode()])

        cert = builder.sign(ca_key)
        return SSHCertificate(
            public_key=public_key,
            certificate=cert.public_bytes().decode(),
            expires_at=expires_at,
        )

    def compute_api_key_hmac(self, api_key: str, secret_key: str) -> str:
        """计算 API Key 的 HMAC-SHA256 签名"""
        import hmac
        import hashlib

        return hmac.new(
            secret_key.encode(),
            api_key.encode(),
            hashlib.sha256,
        ).hexdigest()