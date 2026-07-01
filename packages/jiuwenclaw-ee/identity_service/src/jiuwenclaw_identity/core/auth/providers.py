"""可插拔认证 provider（认证可二次开发的扩展点）。

``AuthProvider.authenticate`` 负责"验证凭据 → 返回 user_id"；签发 JWT 的流程与
具体认证方式解耦。默认 ``LocalPasswordProvider`` 查本地 ``auth_identity``。
厂商接入自有库 / LDAP / OIDC：再实现一个 provider 并 ``register_provider`` 即可，
不动 ``IdentityAuthService`` 与表结构。
"""

from __future__ import annotations

from typing import Protocol

from openjiuwen_runtime.foundation.db.handler import DBHandler

from jiuwenclaw_identity.core.auth.password import verify_password
from jiuwenclaw_identity.models.identity_models import AUTH_IDENTITY_TABLE_DEF

_AUTH_IDENTITY_TABLE = AUTH_IDENTITY_TABLE_DEF.table_name


class AuthProvider(Protocol):
    name: str

    async def authenticate(
        self, handler: DBHandler, username: str, password: str
    ) -> str | None:
        """验证成功返回 user_id；失败返回 None。"""
        ...


class LocalPasswordProvider:
    """本地口令认证：按 (provider='local', external_subject=username) 查身份并校验口令。"""

    name = "local"

    async def authenticate(
        self, handler: DBHandler, username: str, password: str
    ) -> str | None:
        rows = await handler.list_records(
            _AUTH_IDENTITY_TABLE,
            {"provider": self.name, "external_subject": username},
            limit=1,
            offset=0,
        )
        if not rows:
            return None
        row = rows[0]
        if not verify_password(password, getattr(row, "credential", None)):
            return None
        return str(getattr(row, "user_id", "") or "") or None


# 注册表：二次开发时往这里加自定义 provider。
_PROVIDERS: dict[str, AuthProvider] = {}


def register_provider(provider: AuthProvider) -> None:
    _PROVIDERS[provider.name] = provider


def get_provider(name: str) -> AuthProvider | None:
    return _PROVIDERS.get(name)


register_provider(LocalPasswordProvider())
