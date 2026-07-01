"""鉴权依赖：校验认证服务签发的 RS256 JWT(资源服务器),解析当前用户 + admin 守卫。

不再查库发 token；从 ``Authorization: Bearer <jwt>`` 本地验签，principal 来自 claims
（sub / is_admin / groups / name）。返回对象支持属性访问，下游 ``getattr`` 用法不变。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Annotated, Any

import jwt
from fastapi import Depends, Header, HTTPException

from jiuwenclaw_manager.security.jwt_verify import decode_token


def _extract_bearer(authorization: str | None) -> str:
    if not authorization:
        return ""
    parts = authorization.split(" ", 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip()
    return ""


async def get_current_user(
    authorization: Annotated[str | None, Header()] = None,
) -> Any:
    token = _extract_bearer(authorization)
    if not token:
        raise HTTPException(status_code=401, detail="unauthorized")
    try:
        claims = await decode_token(token)
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail="unauthorized") from exc
    return SimpleNamespace(
        user_id=str(claims.get("sub") or ""),
        is_admin=bool(claims.get("is_admin")),
        groups=list(claims.get("groups") or []),
        display_name=claims.get("name"),
    )


async def require_admin(
    user: Annotated[Any, Depends(get_current_user)],
) -> Any:
    if not bool(getattr(user, "is_admin", False)):
        raise HTTPException(status_code=403, detail="admin required")
    return user


CurrentUser = Annotated[Any, Depends(get_current_user)]
AdminUser = Annotated[Any, Depends(require_admin)]
