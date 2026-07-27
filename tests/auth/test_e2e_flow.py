"""
模拟 Web 层调用 AgentOSAuthenticator 的完整流程
包含：Token本地验证、Token远程验证、API-KEY认证、SSH证书认证、公钥认证
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import asyncio
import json
from datetime import timedelta, datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from jose import jwt

from jiuwenswarm.gateway.auth.credential_authenticator import AuthContext, AuthResult
from jiuwenswarm.extensions.auth.agentos_authenticator import AgentOSAuthenticator


SECRET_KEY = "test-gateway-secret-key-2025"
ALGORITHM = "HS256"


def _make_access_token(user_id: str, username: str, role: str,
                       exp_seconds: int = 3600) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "username": username,
        "role": role,
        "type": "access",
        "exp": now + __import__("datetime").timedelta(seconds=exp_seconds),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


async def demo_token_local_verify():
    print("=" * 60)
    print("1. Web Token 本地验证（gateway_secret_key 已配置）")
    print("=" * 60)

    auth = AgentOSAuthenticator(
        auth_service_url="http://fake-agent-os:8000",
        gateway_secret_key=SECRET_KEY,
        jwt_algorithm=ALGORITHM,
    )

    token = _make_access_token(user_id="user-001", username="alice", role="admin")
    context = AuthContext(
        channel_type="web",
        credentials={"token": token},
        headers={"Authorization": f"Bearer {token}"},
        remote_addr="192.168.1.100",
    )

    result = await auth.authenticate(context)
    print(f"  success  = {result.success}")
    print(f"  user_id  = {result.user_id}")
    print(f"  extensions = {result.extensions}")
    print()


async def demo_token_expired():
    print("=" * 60)
    print("2. Web Token 本地验证 —— Token 已过期")
    print("=" * 60)

    auth = AgentOSAuthenticator(
        auth_service_url="http://fake-agent-os:8000",
        gateway_secret_key=SECRET_KEY,
        jwt_algorithm=ALGORITHM,
    )

    token = _make_access_token(user_id="user-002", username="bob", role="user",
                               exp_seconds=-10)
    context = AuthContext(
        channel_type="web",
        credentials={"token": token},
        headers={},
        remote_addr="192.168.1.101",
    )

    result = await auth.authenticate(context)
    print(f"  success = {result.success}")
    print(f"  error   = {result.error}")
    print()


async def demo_token_remote_verify():
    print("=" * 60)
    print("3. Web Token 远程验证（无 gateway_secret_key，走 HTTP）")
    print("=" * 60)

    auth = AgentOSAuthenticator(
        auth_service_url="http://fake-agent-os:8000",
        gateway_secret_key="",
        jwt_algorithm=ALGORITHM,
        timeout=5.0,
    )

    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.json.return_value = {
        "data": {
            "valid": True,
            "user_id": "user-003",
            "username": "carol",
            "role": "viewer",
        }
    }

    with patch.object(auth._auth_client, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = fake_response

        context = AuthContext(
            channel_type="web",
            credentials={"token": "fake-remote-token"},
            headers={"X-Request-Id": "req-999"},
            remote_addr="10.0.0.5",
        )
        result = await auth.authenticate(context)

    print(f"  success   = {result.success}")
    print(f"  user_id   = {result.user_id}")
    print(f"  extensions = {result.extensions}")
    print(f"  HTTP called with URL = {mock_post.call_args.args[0]}")
    print(f"  HTTP called with headers = {mock_post.call_args.kwargs.get('headers')}")
    print()


async def demo_token_remote_401():
    print("=" * 60)
    print("4. Web Token 远程验证 —— 服务端返回 401")
    print("=" * 60)

    auth = AgentOSAuthenticator(
        auth_service_url="http://fake-agent-os:8000",
        gateway_secret_key="",
    )

    fake_response = MagicMock()
    fake_response.status_code = 401

    with patch.object(auth._auth_client, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = fake_response

        context = AuthContext(
            channel_type="web",
            credentials={"token": "expired-remote-token"},
        )
        result = await auth.authenticate(context)

    print(f"  success      = {result.success}")
    print(f"  error        = {result.error}")
    print(f"  error_code   = {result.extensions.get('error_code')}")
    print()


async def demo_api_key():
    print("=" * 60)
    print("5. Web API-KEY 认证")
    print("=" * 60)

    auth = AgentOSAuthenticator(
        auth_service_url="http://fake-agent-os:8000",
        gateway_secret_key=SECRET_KEY,
    )
    auth._api_key_map = {"fake_hmac_hash": "agent-007"}

    with patch.object(auth, "compute_api_key_hmac", return_value="fake_hmac_hash"):
        context = AuthContext(
            channel_type="web",
            credentials={"api_key": "sk-test-api-key-123"},
            headers={"X-Api-Key": "sk-test-api-key-123"},
            remote_addr="10.0.0.50",
        )
        result = await auth.authenticate(context)

    print(f"  success   = {result.success}")
    print(f"  user_id   = {result.user_id}")
    print(f"  extensions = {result.extensions}")
    print()


async def demo_api_key_invalid():
    print("=" * 60)
    print("6. Web API-KEY 认证 —— 无效 Key")
    print("=" * 60)

    auth = AgentOSAuthenticator(
        auth_service_url="http://fake-agent-os:8000",
        gateway_secret_key=SECRET_KEY,
    )
    auth._api_key_map = {}

    context = AuthContext(
        channel_type="web",
        credentials={"api_key": "sk-invalid-key"},
    )
    result = await auth.authenticate(context)

    print(f"  success    = {result.success}")
    print(f"  error      = {result.error}")
    print(f"  error_code = {result.extensions.get('error_code')}")
    print()


async def demo_certificate():
    print("=" * 60)
    print("7. Web SSH 证书认证")
    print("=" * 60)

    auth = AgentOSAuthenticator(
        auth_service_url="http://fake-agent-os:8000",
        gateway_secret_key=SECRET_KEY,
    )

    with patch.object(auth, "_verify_ssh_certificate",
                      return_value=(True, "user-ssh-001")):
        context = AuthContext(
            channel_type="web",
            credentials={"certificate": "ssh-cert-base64-data"},
        )
        result = await auth.authenticate(context)

    print(f"  success  = {result.success}")
    print(f"  user_id  = {result.user_id}")
    print()


async def demo_public_key():
    print("=" * 60)
    print("8. Web 公钥认证")
    print("=" * 60)

    auth = AgentOSAuthenticator(
        auth_service_url="http://fake-agent-os:8000",
        gateway_secret_key=SECRET_KEY,
    )
    auth._public_key_map = {
        "ssh-rsa AAAA...userA": "agent-pubkey-001"
    }

    context = AuthContext(
        channel_type="web",
        credentials={"public_key": "ssh-rsa AAAA...userA"},
    )
    result = await auth.authenticate(context)

    print(f"  success  = {result.success}")
    print(f"  user_id  = {result.user_id}")
    print()


async def demo_no_credentials():
    print("=" * 60)
    print("9. Web 无凭证 —— 认证失败")
    print("=" * 60)

    auth = AgentOSAuthenticator(
        auth_service_url="http://fake-agent-os:8000",
        gateway_secret_key=SECRET_KEY,
    )

    context = AuthContext(
        channel_type="web",
        credentials={},
        headers={},
        remote_addr="192.168.1.200",
    )
    result = await auth.authenticate(context)

    print(f"  success = {result.success}")
    print(f"  error   = {result.error}")
    print()


async def demo_bearer_from_header():
    print("=" * 60)
    print("10. Web Token —— 从 Authorization Header 提取")
    print("=" * 60)

    auth = AgentOSAuthenticator(
        auth_service_url="http://fake-agent-os:8000",
        gateway_secret_key=SECRET_KEY,
        jwt_algorithm=ALGORITHM,
    )

    token = _make_access_token(user_id="user-010", username="dave", role="editor")
    context = AuthContext(
        channel_type="web",
        credentials={"token": "wrong-token-placeholder"},
        headers={"Authorization": f"Bearer {token}"},
    )

    result = await auth.authenticate(context)
    print(f"  success   = {result.success}")
    print(f"  user_id   = {result.user_id}")
    print(f"  username  = {result.extensions.get('username')}")
    print(f"  (token from header overrides credentials['token'])")
    print()


async def main():
    await demo_token_local_verify()
    await demo_token_expired()
    await demo_token_remote_verify()
    await demo_token_remote_401()
    await demo_api_key()
    await demo_api_key_invalid()
    await demo_certificate()
    await demo_public_key()
    await demo_no_credentials()
    await demo_bearer_from_header()
    print("Done.")


if __name__ == "__main__":
    asyncio.run(main())