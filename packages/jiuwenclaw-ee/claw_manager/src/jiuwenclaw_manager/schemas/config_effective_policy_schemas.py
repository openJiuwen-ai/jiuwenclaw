"""配置生效策略与默认模板映射 API 请求/响应模型。"""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import BaseModel, Field, BeforeValidator

from jiuwenclaw_manager.infrastructure.template_ref import (
    coerce_template_ref,
    coerce_template_ref_optional,
)

TemplateRefField = Annotated[dict[str, list[str]], BeforeValidator(coerce_template_ref)]
OptionalTemplateRefField = Annotated[
    dict[str, list[str]] | None,
    BeforeValidator(coerce_template_ref_optional),
]


# --- config_effective_agent_policy ---

class ConfigEffectiveAgentPolicyCreateBody(BaseModel):
    agent_id: str = Field(..., max_length=512)
    service_policy_id: int
    priority: int = Field(default=0)
    match_expr: str | None = None
    template_ref: TemplateRefField = Field(
        default_factory=dict,
        description="槽位名 -> template_id 数组；元素可为 UUID 或 ${user::…}/${group::…} or <template_id>",
    )
    send_file_allowed: bool = True
    enabled: bool = True
    data: dict[str, Any] | None = None


class ConfigEffectiveAgentPolicyUpdateBody(BaseModel):
    agent_id: str | None = Field(default=None, max_length=512)
    service_policy_id: int | None = None
    priority: int | None = None
    match_expr: str | None = None
    template_ref: OptionalTemplateRefField = None
    send_file_allowed: bool | None = None
    enabled: bool | None = None
    data: dict[str, Any] | None = None


class ConfigEffectiveAgentPolicyOut(BaseModel):
    id: int
    agent_id: str
    jiuwenclaw_id: str
    service_policy_id: int
    priority: int
    match_expr: str | None
    template_ref: dict[str, list[str]]
    send_file_allowed: bool
    enabled: bool
    data: dict[str, Any] | None
    created_at: str | None
    updated_at: str | None


# --- config_effective_service_policy ---

class ConfigEffectiveServicePolicyCreateBody(BaseModel):
    service_id: str = Field(..., max_length=512)
    priority: int
    match_expr: str | None = None
    template_ref: TemplateRefField = Field(default_factory=dict)
    enabled: bool = True
    data: dict[str, Any] | None = None


class ConfigEffectiveServicePolicyUpdateBody(BaseModel):
    service_id: str | None = Field(default=None, max_length=512)
    priority: int | None = None
    match_expr: str | None = None
    template_ref: OptionalTemplateRefField = None
    enabled: bool | None = None
    data: dict[str, Any] | None = None


class ConfigEffectiveServicePolicyOut(BaseModel):
    id: int
    service_id: str
    jiuwenclaw_id: str
    priority: int
    match_expr: str | None
    template_ref: dict[str, list[str]]
    enabled: bool
    data: dict[str, Any] | None
    created_at: str | None
    updated_at: str | None


# --- config_effective_global_policy ---

class ConfigEffectiveGlobalPolicyCreateBody(BaseModel):
    priority: int = 0
    template_ref: TemplateRefField = Field(default_factory=dict)
    enabled: bool = True
    data: dict[str, Any] | None = None


class ConfigEffectiveGlobalPolicyUpdateBody(BaseModel):
    priority: int | None = None
    template_ref: OptionalTemplateRefField = None
    enabled: bool | None = None
    data: dict[str, Any] | None = None


class ConfigEffectiveGlobalPolicyOut(BaseModel):
    id: int
    jiuwenclaw_id: str
    priority: int
    template_ref: dict[str, list[str]]
    enabled: bool
    data: dict[str, Any] | None
    created_at: str | None
    updated_at: str | None


# --- config_default_template_mapping ---

class ConfigDefaultTemplateMappingCreateBody(BaseModel):
    user_id: str | None = Field(default=None, max_length=512)
    group_id: str | None = Field(default=None, max_length=512)
    priority: int = 0
    template_id: str = Field(..., max_length=100, min_length=1)
    template_type: str = Field(..., max_length=512)
    enabled: bool = True
    data: dict[str, Any] | None = None


class ConfigDefaultTemplateMappingUpdateBody(BaseModel):
    user_id: str | None = Field(default=None, max_length=512)
    group_id: str | None = Field(default=None, max_length=512)
    priority: int | None = None
    template_id: str | None = Field(default=None, max_length=100, min_length=1)
    template_type: str | None = Field(default=None, max_length=512)
    enabled: bool | None = None
    data: dict[str, Any] | None = None


class ConfigDefaultTemplateMappingOut(BaseModel):
    id: int
    jiuwenclaw_id: str
    user_id: str | None
    group_id: str | None
    priority: int
    template_id: str
    template_type: str
    enabled: bool
    data: dict[str, Any] | None
    created_at: str | None
    updated_at: str | None
