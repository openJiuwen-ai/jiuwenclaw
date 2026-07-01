"""Access JWT 的签发与校验（RS256）。

claims：``sub``(user_id)、``name``、``is_admin``、``groups``(组织 id 列表)、
``iss``/``aud``/``iat``/``exp``/``jti``/``typ=access``。资源服务器用公钥验签即可，
无需回调本服务。
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

import jwt

from jiuwenclaw_identity.infrastructure.config import settings
from jiuwenclaw_identity.infrastructure.utils import utc_now
from jiuwenclaw_identity.security.jwt_keys import private_pem, public_pem

_ALG = "RS256"


def issue_access_token(
    user_id: str,
    *,
    display_name: str | None = None,
    is_admin: bool = False,
    groups: list[str] | None = None,
) -> tuple[str, int]:
    """返回 (access_token, expires_in_seconds)。"""
    now = utc_now()
    ttl = int(settings.access_ttl_seconds)
    payload: dict[str, Any] = {
        "sub": user_id,
        "name": display_name or user_id,
        "is_admin": bool(is_admin),
        "groups": groups or [],
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "iat": now,
        "exp": now + timedelta(seconds=ttl),
        "jti": str(uuid.uuid4()),
        "typ": "access",
    }
    token = jwt.encode(payload, private_pem(), algorithm=_ALG)
    return token, ttl


def decode_access_token(token: str) -> dict[str, Any]:
    """验签 + 校验 iss/aud/exp；失败抛 jwt 异常。返回 claims。"""
    return jwt.decode(
        token,
        public_pem(),
        algorithms=[_ALG],
        audience=settings.jwt_audience,
        issuer=settings.jwt_issuer,
        options={"require": ["exp", "iat", "sub"]},
    )
