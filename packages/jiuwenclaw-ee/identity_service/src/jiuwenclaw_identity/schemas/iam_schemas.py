"""身份目录管理请求体（组织 / 用户 / 成员）。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class OrgCreateBody(BaseModel):
    group_id: str | None = Field(default=None, max_length=64)
    name: str = Field(..., min_length=1, max_length=128)


class OrgUpdateBody(BaseModel):
    name: str | None = Field(default=None, max_length=128)
    status: str | None = Field(default=None, max_length=16)


class UserCreateBody(BaseModel):
    user_id: str | None = Field(default=None, max_length=64)
    display_name: str = Field(..., min_length=1, max_length=128)
    is_admin: bool = False
    username: str = Field(..., min_length=1, max_length=256)
    password: str = Field(..., min_length=1, max_length=256)


class UserUpdateBody(BaseModel):
    display_name: str | None = Field(default=None, max_length=128)
    is_admin: bool | None = None
    status: str | None = Field(default=None, max_length=16)
    password: str | None = Field(default=None, max_length=256)


class SetMembershipBody(BaseModel):
    """整体覆盖某用户的组织绑定（批量）。"""

    group_ids: list[str] = Field(default_factory=list)


class AddMembersBody(BaseModel):
    """从组织侧批量加入用户（幂等）。"""

    user_ids: list[str] = Field(default_factory=list)
