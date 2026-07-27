"""
模拟从启动层调用 PassthroughAuthenticator 的完整流程
1. 启动 → ExtensionRegistry 初始化（默认注册 PassthroughAuthenticator）
2. 网关 → get_auth_handler() 获取认证器
3. 连接 → Web/TUI Channel 调用 authenticate()
4. 凭证管理 → generate_api_key / generate_user_keypair / generate_ssh_certificate / compute_api_key_hmac
"""
import sys
import asyncio
from pathlib import Path
from datetime import timedelta

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from jiuwenswarm.gateway.auth.credential_authenticator import AuthContext, AuthResult, KeyPair
from jiuwenswarm.gateway.auth.passthrough_authenticator import PassthroughAuthenticator
from jiuwenswarm.extensions.registry import ExtensionRegistry


class FakeCallbackFramework:
    async def trigger(self, *a, **kw):
        pass
    def register_sync(self, *a, **kw):
        pass


def step_1_registry_init():
    print("=" * 60)
    print("1. 启动：ExtensionRegistry 初始化，默认注册 PassthroughAuthenticator")
    print("=" * 60)

    ExtensionRegistry.reset_instance()
    registry = ExtensionRegistry.create_instance(
        callback_framework=FakeCallbackFramework(),
        config={"extensions": {"auth": {"type": "passthrough"}}},
        logger=None,
    )
    auth = registry.get_authenticator()
    print(f"  认证器类型    = {type(auth).__name__}")
    print(f"  是否 Passthrough = {isinstance(auth, PassthroughAuthenticator)}")
    print()
    return registry


def step_2_get_auth_handler(registry):
    print("=" * 60)
    print("2. 网关：通过 registry.get_authenticator() 获取全局认证器")
    print("=" * 60)

    auth = registry.get_authenticator()
    print(f"  认证器实例    = {auth}")
    print(f"  认证器类型    = {type(auth).__name__}")
    print()
    return auth


async def step_3_web_authenticate(auth):
    print("=" * 60)
    print("3. Web Channel：调用 authenticate() —— 任意凭证直接放行")
    print("=" * 60)

    context = AuthContext(
        channel_type="web",
        credentials={"token": "any-token-will-do"},
        headers={"Authorization": "Bearer any-token-will-do"},
        remote_addr="192.168.1.100",
    )
    result = await auth.authenticate(context)
    print(f"  success  = {result.success}")
    print(f"  user_id  = {result.user_id}")
    print()


async def step_4_tui_authenticate(auth):
    print("=" * 60)
    print("4. TUI Channel：调用 authenticate() —— 无凭证也放行")
    print("=" * 60)

    context = AuthContext(
        channel_type="tui",
        credentials={},
        headers={},
        remote_addr="10.0.0.5",
    )
    result = await auth.authenticate(context)
    print(f"  success  = {result.success}")
    print(f"  user_id  = {result.user_id}")
    print()


async def step_5_ssh_authenticate(auth):
    print("=" * 60)
    print("5. SSH Channel：调用 authenticate() —— 公钥也放行")
    print("=" * 60)

    context = AuthContext(
        channel_type="ssh",
        credentials={"public_key": "ssh-rsa AAAA...arbitrary-key"},
        remote_addr="10.0.0.99",
    )
    result = await auth.authenticate(context)
    print(f"  success  = {result.success}")
    print(f"  user_id  = {result.user_id}")
    print()


def step_6_generate_api_key(auth):
    print("=" * 60)
    print("6. 凭证管理：generate_api_key()")
    print("=" * 60)

    api_key = auth.generate_api_key()
    print(f"  api_key      = {api_key}")
    print(f"  prefix       = {api_key[:3]}")
    print(f"  length       = {len(api_key)}")
    print()


def step_7_generate_user_keypair(auth):
    print("=" * 60)
    print("7. 凭证管理：generate_user_keypair()")
    print("=" * 60)

    keypair = auth.generate_user_keypair()
    print(f"  public_key 类型  = OpenSSH")
    print(f"  public_key 前缀  = {keypair.public_key[:7]}...")
    print(f"  private_key 前缀 = {keypair.private_key[:27]}...")
    print(f"  KeyPair 实例     = {isinstance(keypair, KeyPair)}")
    print()


def step_8_compute_api_key_hmac(auth):
    print("=" * 60)
    print("8. 凭证管理：compute_api_key_hmac()")
    print("=" * 60)

    api_key = auth.generate_api_key()
    secret = "my-secret-key"
    hmac_value = auth.compute_api_key_hmac(api_key, secret)
    print(f"  api_key    = {api_key}")
    print(f"  secret_key = {secret}")
    print(f"  hmac       = {hmac_value}")
    print(f"  hmac 长度   = {len(hmac_value)} (SHA-256 hex = 64 chars)")
    print()


def step_9_generate_ssh_certificate(auth):
    print("=" * 60)
    print("9. 凭证管理：generate_ssh_certificate()（需要 CA 私钥）")
    print("=" * 60)

    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.backends import default_backend

    ca_key = rsa.generate_private_key(
        public_exponent=65537, key_size=2048, backend=default_backend()
    )
    ca_pem = ca_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )

    auth_with_ca = PassthroughAuthenticator(ca_private_key_pem=ca_pem)
    keypair = auth_with_ca.generate_user_keypair()

    cert = auth_with_ca.generate_ssh_certificate(
        public_key=keypair.public_key,
        user_id="user-ssh-001",
        validity=timedelta(hours=1),
    )
    print(f"  certificate 前缀 = {cert.certificate[:30]}...")
    print(f"  public_key       = {cert.public_key[:30]}...")
    print(f"  expires_at       = {cert.expires_at}")
    print()


def step_10_ssh_cert_no_ca():
    print("=" * 60)
    print("10. 凭证管理：generate_ssh_certificate() —— 无 CA 私钥抛异常")
    print("=" * 60)

    auth = PassthroughAuthenticator(ca_private_key_pem=None)
    try:
        auth.generate_ssh_certificate("ssh-rsa AAAA...", "user", timedelta(hours=1))
    except Exception as e:
        print(f"  异常类型 = {type(e).__name__}")
        print(f"  异常信息 = {e}")
    print()


def step_11_register_authenticator_replace():
    print("=" * 60)
    print("11. 启动：register_authenticator() 替换默认认证器")
    print("=" * 60)

    ExtensionRegistry.reset_instance()
    registry = ExtensionRegistry.create_instance(
        callback_framework=FakeCallbackFramework(),
        config={},
        logger=None,
    )
    default_auth = registry.get_authenticator()
    print(f"  默认认证器 = {type(default_auth).__name__}")

    new_auth = PassthroughAuthenticator()
    registry.register_authenticator(new_auth)
    replaced_auth = registry.get_authenticator()
    print(f"  替换后     = {type(replaced_auth).__name__}")
    print(f"  是否同一实例 = {replaced_auth is new_auth}")
    print()


async def main():
    registry = step_1_registry_init()
    auth = step_2_get_auth_handler(registry)
    await step_3_web_authenticate(auth)
    await step_4_tui_authenticate(auth)
    await step_5_ssh_authenticate(auth)
    step_6_generate_api_key(auth)
    step_7_generate_user_keypair(auth)
    step_8_compute_api_key_hmac(auth)
    step_9_generate_ssh_certificate(auth)
    step_10_ssh_cert_no_ca()
    step_11_register_authenticator_replace()
    print("Done.")


if __name__ == "__main__":
    asyncio.run(main())