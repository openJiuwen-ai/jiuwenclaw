"""认证服务的请求/响应模型。"""

from __future__ import annotations

from pydantic import BaseModel


class TokenResponse(BaseModel):
    """OAuth2 标准 token 响应（/token、/refresh 返回）。"""

    access_token: str
    token_type: str = "bearer"
    expires_in: int
    refresh_token: str


class UserOut(BaseModel):
    user_id: str
    display_name: str | None = None
    is_admin: bool = False
    status: str | None = None
    groups: list[str] = []


class RefreshBody(BaseModel):
    refresh_token: str


class LogoutBody(BaseModel):
    refresh_token: str
