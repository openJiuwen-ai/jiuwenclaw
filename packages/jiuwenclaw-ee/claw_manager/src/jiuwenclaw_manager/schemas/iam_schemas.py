"""IAM 管理请求体（claw_manager 侧）：bot / 可见性。

组织 / 用户 / 成员的请求体已迁至认证服务(jiuwenclaw_identity)。
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class BotCreateBody(BaseModel):
    bot_id: str | None = Field(default=None, max_length=64)
    name: str = Field(..., min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=512)


class BotUpdateBody(BaseModel):
    name: str | None = Field(default=None, max_length=128)
    description: str | None = Field(default=None, max_length=512)
    status: str | None = Field(default=None, max_length=16)


class VisibilityItem(BaseModel):
    scope_type: str = Field(..., pattern="^(global|org|user)$")
    scope_id: str | None = Field(default=None, max_length=64)


class SetVisibilityBody(BaseModel):
    """整体覆盖某 bot 的可见性（批量）。"""

    scopes: list[VisibilityItem] = Field(default_factory=list)


class InstanceBindBody(BaseModel):
    """把一批用户/组织绑定或解绑到某实例（jiuwenclaw_id 走路径）。"""

    ids: list[str] = Field(..., min_length=1, max_length=1000)


class InstanceBotVisibilityBody(BaseModel):
    """设置某 bot 在某实例上的可见范围（jiuwenclaw_id / bot_id 走路径）。空 scopes = 移出该实例。"""

    scopes: list[VisibilityItem] = Field(default_factory=list)
