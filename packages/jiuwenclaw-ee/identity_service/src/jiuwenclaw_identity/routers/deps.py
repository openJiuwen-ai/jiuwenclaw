"""鉴权依赖：OAuth2 Bearer + JWT 验签 → 当前用户 claims；admin 守卫。

``oauth2_scheme`` 让 ``/docs`` 出现 Authorize（密码流，tokenUrl 指向 /token）。
本服务自身验签用本地公钥；claw_manager / 企业版 web 复用同样的验签逻辑（拿公钥）。
"""

from __future__ import annotations

from typing import Annotated, Any

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer

from jiuwenclaw_identity.security.tokens import decode_access_token

# tokenUrl 为相对路径，指向下方 /v1/auth/token；Swagger Authorize 用它走密码流。
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="v1/auth/token")

_CRED_EXC = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="invalid or expired token",
    headers={"WWW-Authenticate": "Bearer"},
)


async def get_current_claims(token: Annotated[str, Depends(oauth2_scheme)]) -> dict[str, Any]:
    try:
        return decode_access_token(token)
    except jwt.PyJWTError as exc:
        raise _CRED_EXC from exc


_Claims = Annotated[dict[str, Any], Depends(get_current_claims)]


async def require_admin(claims: _Claims) -> dict[str, Any]:
    if not bool(claims.get("is_admin")):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="admin required")
    return claims
