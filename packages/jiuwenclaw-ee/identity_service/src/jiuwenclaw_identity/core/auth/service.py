"""认证服务：provider 校验凭据 → 签发 access JWT + refresh token；刷新 / 登出 / 播种。

access = 自包含 JWT（不落库，资源服务器公钥验签）；refresh = 不透明串落 ``auth_session``，
可撤销/轮换。``IdentityAuthService.login`` 成功返回 token 束 + 用户信息。
"""

from __future__ import annotations

import secrets
from datetime import timedelta, timezone
from typing import Any

from openjiuwen_runtime.foundation.db.handler import DBHandler

from jiuwenclaw_identity.core.auth.password import hash_password
from jiuwenclaw_identity.core.auth.providers import get_provider
from jiuwenclaw_identity.infrastructure.config import settings
from jiuwenclaw_identity.infrastructure.logger import get_logger
from jiuwenclaw_identity.infrastructure.utils import utc_now
from jiuwenclaw_identity.models.identity_models import (
    APP_USER_TABLE_DEF,
    AUTH_IDENTITY_TABLE_DEF,
    AUTH_SESSION_TABLE_DEF,
    NO_ORG_GROUP_ID,
    ORG_TABLE_DEF,
    USER_ORG_MEMBERSHIP_TABLE_DEF,
)
from jiuwenclaw_identity.security.tokens import issue_access_token

_log = get_logger(__name__)

_APP_USER = APP_USER_TABLE_DEF.table_name
_AUTH_IDENTITY = AUTH_IDENTITY_TABLE_DEF.table_name
_AUTH_SESSION = AUTH_SESSION_TABLE_DEF.table_name
_ORG = ORG_TABLE_DEF.table_name
_MEMBERSHIP = USER_ORG_MEMBERSHIP_TABLE_DEF.table_name

_DEFAULT_PROVIDER = "local"
_CAP = 1000


def public_user(row: Any) -> dict[str, Any]:
    return {
        "user_id": getattr(row, "user_id", None),
        "display_name": getattr(row, "display_name", None),
        "is_admin": bool(getattr(row, "is_admin", False)),
        "status": getattr(row, "status", None),
    }


class IdentityAuthService:
    def __init__(self, handler: DBHandler) -> None:
        self._h = handler

    async def _load_groups(self, user_id: str) -> list[str]:
        """用户所属真实组织 id（排除无组织保留行）。"""
        rows = await self._h.list_records(_MEMBERSHIP, {"user_id": user_id}, limit=_CAP, offset=0)
        gids = [str(getattr(r, "group_id", "")) for r in rows]
        return [g for g in gids if g and g != NO_ORG_GROUP_ID]

    async def _issue_for(self, user: Any) -> dict[str, Any]:
        user_id = str(getattr(user, "user_id"))
        groups = await self._load_groups(user_id)
        access, expires_in = issue_access_token(
            user_id,
            display_name=getattr(user, "display_name", None),
            is_admin=bool(getattr(user, "is_admin", False)),
            groups=groups,
        )
        now = utc_now()
        refresh = secrets.token_urlsafe(32)
        await self._h.create(
            _AUTH_SESSION,
            {
                "refresh_token": refresh,
                "user_id": user_id,
                "created_at": now,
                "expires_at": now + timedelta(seconds=int(settings.refresh_ttl_seconds)),
            },
        )
        return {
            "access_token": access,
            "token_type": "bearer",
            "expires_in": expires_in,
            "refresh_token": refresh,
            "user": public_user(user),
        }

    async def login(
        self, username: str, password: str, *, provider: str = _DEFAULT_PROVIDER
    ) -> dict[str, Any] | str:
        """成功返回 token 束 + user；失败返回 ``"bad_credentials"`` / ``"disabled"``。"""
        prov = get_provider(provider)
        if prov is None:
            return "bad_credentials"
        user_id = await prov.authenticate(self._h, username.strip(), password)
        if not user_id:
            _log.info("[Auth] login.fail", username=username.strip(), reason="bad_credentials")
            return "bad_credentials"
        user = await self._h.get(_APP_USER, {"user_id": user_id})
        if user is None:
            return "bad_credentials"
        if str(getattr(user, "status", "")) != "active":
            return "disabled"
        _log.info("[Auth] login.ok", user_id=user_id, provider=provider)
        return await self._issue_for(user)

    async def refresh(self, refresh_token: str) -> dict[str, Any] | str:
        """用 refresh 换新 access（并轮换 refresh）。失败返回 ``"invalid_refresh"``。"""
        if not refresh_token:
            return "invalid_refresh"
        sess = await self._h.get(_AUTH_SESSION, {"refresh_token": refresh_token})
        if sess is None:
            return "invalid_refresh"
        expires_at = getattr(sess, "expires_at", None)
        if expires_at is not None:
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if utc_now() > expires_at:
                await self._h.delete(_AUTH_SESSION, {"refresh_token": refresh_token})
                return "invalid_refresh"
        user = await self._h.get(_APP_USER, {"user_id": getattr(sess, "user_id", None)})
        if user is None or str(getattr(user, "status", "")) != "active":
            await self._h.delete(_AUTH_SESSION, {"refresh_token": refresh_token})
            return "invalid_refresh"
        # 轮换：撤销旧 refresh，签发新束
        await self._h.delete(_AUTH_SESSION, {"refresh_token": refresh_token})
        return await self._issue_for(user)

    async def logout(self, refresh_token: str) -> None:
        if refresh_token:
            await self._h.delete(_AUTH_SESSION, {"refresh_token": refresh_token})

    async def get_user(self, user_id: str) -> Any | None:
        return await self._h.get(_APP_USER, {"user_id": user_id})

    async def list_my_orgs(self, user_id: str) -> list[dict[str, Any]]:
        """自己所属真实组织；仅当不属于任何真实组织时,才附"无组织"入口。"""
        gids = await self._load_groups(user_id)
        out: list[dict[str, Any]] = []
        for gid in gids:
            org = await self._h.get(_ORG, {"group_id": gid})
            if org is not None and str(getattr(org, "status", "")) == "active":
                out.append({"group_id": gid, "name": getattr(org, "name", None)})
        if not out:
            none_org = await self._h.get(_ORG, {"group_id": NO_ORG_GROUP_ID})
            if none_org is not None:
                out.append({"group_id": NO_ORG_GROUP_ID, "name": getattr(none_org, "name", None)})
        return out


# ----------------------- 播种 -----------------------
async def _ensure_org(handler: DBHandler, group_id: str, name: str) -> None:
    if await handler.get(_ORG, {"group_id": group_id}) is not None:
        return
    now = utc_now()
    await handler.create(
        _ORG,
        {"group_id": group_id, "name": name, "status": "active", "created_at": now, "updated_at": now},
    )


async def _ensure_local_user(
    handler: DBHandler, *, user_id: str, display_name: str, is_admin: bool,
    username: str, password: str,
) -> None:
    if await handler.get(_APP_USER, {"user_id": user_id}) is not None:
        return
    now = utc_now()
    await handler.create(
        _APP_USER,
        {"user_id": user_id, "display_name": display_name, "is_admin": is_admin,
         "status": "active", "created_at": now, "updated_at": now},
    )
    await handler.create(
        _AUTH_IDENTITY,
        {"user_id": user_id, "provider": _DEFAULT_PROVIDER, "external_subject": username,
         "credential": hash_password(password), "created_at": now, "updated_at": now},
    )
    _log.info("[auth] seeded user", user_id=user_id, is_admin=is_admin)


async def seed_defaults(handler: DBHandler) -> None:
    """幂等播种：无组织保留行 + 引导 admin（可选 user1）。不播种任何业务/演示数据。"""
    await _ensure_org(handler, NO_ORG_GROUP_ID, "无组织")
    if settings.seed_admin:
        await _ensure_local_user(
            handler, user_id="admin", display_name="Administrator", is_admin=True,
            username="admin", password="admin",
        )
    if settings.seed_user1:
        await _ensure_local_user(
            handler, user_id="user1", display_name="User One", is_admin=False,
            username="user1", password="user1",
        )
