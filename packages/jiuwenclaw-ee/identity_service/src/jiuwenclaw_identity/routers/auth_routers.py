"""认证路由：OAuth2 密码流 /token、/me、/me/orgs、/refresh、/logout、/public_key。"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.security import OAuth2PasswordRequestForm
from openjiuwen_runtime.foundation.db.handler import DBHandler

from jiuwenclaw_identity.core.auth import IdentityAuthService
from jiuwenclaw_identity.infrastructure.db import get_db_handler
from jiuwenclaw_identity.routers.deps import get_current_claims
from jiuwenclaw_identity.schemas.auth_schemas import (
    LogoutBody,
    RefreshBody,
    TokenResponse,
    UserOut,
)
from jiuwenclaw_identity.security.jwt_keys import public_pem

_Handler = Annotated[DBHandler, Depends(get_db_handler)]
_Claims = Annotated[dict[str, Any], Depends(get_current_claims)]

auth_router = APIRouter()

# detail 用稳定 code（前端据此映射双语文案，未知回退通用句子）。
_LOGIN_ERR = {
    "bad_credentials": (status.HTTP_401_UNAUTHORIZED, "auth_bad_credentials"),
    "disabled": (status.HTTP_403_FORBIDDEN, "auth_disabled"),
    "invalid_refresh": (status.HTTP_401_UNAUTHORIZED, "auth_invalid_refresh"),
}


def _raise(code: str) -> None:
    http_status, detail = _LOGIN_ERR.get(code, (status.HTTP_400_BAD_REQUEST, code))
    raise HTTPException(
        status_code=http_status, detail=detail, headers={"WWW-Authenticate": "Bearer"}
    )


@auth_router.post("/token", response_model=TokenResponse)
async def token(
    form: Annotated[OAuth2PasswordRequestForm, Depends()],
    handler: _Handler,
):
    """OAuth2 密码流：表单 username/password → 签发 access JWT + refresh。

    （Swagger 右上角 Authorize 即调用此端点。）
    """
    result = await IdentityAuthService(handler).login(form.username, form.password)
    if isinstance(result, str):
        _raise(result)
    return TokenResponse(**{k: result[k] for k in ("access_token", "token_type", "expires_in", "refresh_token")})


@auth_router.get("/me", response_model=UserOut)
async def me(claims: _Claims, handler: _Handler):
    """当前登录用户（来自 JWT claims，并以库中最新状态校正）。"""
    svc = IdentityAuthService(handler)
    user = await svc.get_user(str(claims.get("sub")))
    if user is None or str(getattr(user, "status", "")) != "active":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="user not found or disabled")
    return UserOut(
        user_id=str(getattr(user, "user_id")),
        display_name=getattr(user, "display_name", None),
        is_admin=bool(getattr(user, "is_admin", False)),
        status=getattr(user, "status", None),
        groups=list(claims.get("groups") or []),
    )


@auth_router.get("/me/orgs")
async def my_orgs(claims: _Claims, handler: _Handler):
    """当前用户可选组织（有真实组织则不含『无组织』）。"""
    orgs = await IdentityAuthService(handler).list_my_orgs(str(claims.get("sub")))
    return {"orgs": orgs}


@auth_router.post("/refresh", response_model=TokenResponse)
async def refresh(body: RefreshBody, handler: _Handler):
    result = await IdentityAuthService(handler).refresh(body.refresh_token)
    if isinstance(result, str):
        _raise(result)
    return TokenResponse(**{k: result[k] for k in ("access_token", "token_type", "expires_in", "refresh_token")})


@auth_router.post("/logout")
async def logout(body: LogoutBody, handler: _Handler):
    await IdentityAuthService(handler).logout(body.refresh_token)
    return {"ok": True}


@auth_router.get("/public_key")
async def public_key():
    """RS256 公钥（PEM）。资源服务器拿它本地验签 JWT，无需回调本服务。"""
    return Response(content=public_pem(), media_type="application/x-pem-file")
