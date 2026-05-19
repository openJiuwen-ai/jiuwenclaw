"""配置生效策略与默认模板映射 API 请求/响应模型。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


# --- config_effective_agent_policy ---

class ConfigEffectiveAgentPolicyCreateBody(BaseModel):
    agent_id: str = Field(..., max_length=512)
    service_policy_id: int
    priority: int = Field(default=0)
    match_expr: str | None = None
    default_model: str | None = Field(default=None, max_length=128)
    video_model: str | None = Field(default=None, max_length=128)
    audio_model: str | None = Field(default=None, max_length=128)
    vision_model: str | None = Field(default=None, max_length=128)
    enabled: bool = True
    data: dict[str, Any] | None = None


class ConfigEffectiveAgentPolicyUpdateBody(BaseModel):
    agent_id: str | None = Field(default=None, max_length=512)
    service_policy_id: int | None = None
    priority: int | None = None
    match_expr: str | None = None
    default_model: str | None = Field(default=None, max_length=128)
    video_model: str | None = Field(default=None, max_length=128)
    audio_model: str | None = Field(default=None, max_length=128)
    vision_model: str | None = Field(default=None, max_length=128)
    enabled: bool | None = None
    data: dict[str, Any] | None = None


class ConfigEffectiveAgentPolicyOut(BaseModel):
    id: int
    agent_id: str
    jiuwenclaw_id: str
    service_policy_id: int
    priority: int
    match_expr: str | None
    default_model: str | None
    video_model: str | None
    audio_model: str | None
    vision_model: str | None
    enabled: bool
    data: dict[str, Any] | None
    created_at: str | None
    updated_at: str | None


# --- config_effective_service_policy ---

class ConfigEffectiveServicePolicyCreateBody(BaseModel):
    service_id: str = Field(..., max_length=512)
    priority: int
    match_expr: str | None = None
    default_model: str | None = Field(default=None, max_length=128)
    video_model: str | None = Field(default=None, max_length=128)
    audio_model: str | None = Field(default=None, max_length=128)
    vision_model: str | None = Field(default=None, max_length=128)
    enabled: bool = True
    data: dict[str, Any] | None = None


class ConfigEffectiveServicePolicyUpdateBody(BaseModel):
    service_id: str | None = Field(default=None, max_length=512)
    priority: int | None = None
    match_expr: str | None = None
    default_model: str | None = Field(default=None, max_length=128)
    video_model: str | None = Field(default=None, max_length=128)
    audio_model: str | None = Field(default=None, max_length=128)
    vision_model: str | None = Field(default=None, max_length=128)
    enabled: bool | None = None
    data: dict[str, Any] | None = None


class ConfigEffectiveServicePolicyOut(BaseModel):
    id: int
    service_id: str
    jiuwenclaw_id: str
    priority: int
    match_expr: str | None
    default_model: str | None
    video_model: str | None
    audio_model: str | None
    vision_model: str | None
    enabled: bool
    data: dict[str, Any] | None
    created_at: str | None
    updated_at: str | None


# --- config_effective_global_policy ---

class ConfigEffectiveGlobalPolicyCreateBody(BaseModel):
    channel_ids: list[str] = Field(
        default_factory=list,
        description="启用的 channel_template.id 列表；省略或 [] 表示不绑定通道模板",
    )
    default_model: str | None = Field(default=None, max_length=128)
    video_model: str | None = Field(default=None, max_length=128)
    audio_model: str | None = Field(default=None, max_length=128)
    vision_model: str | None = Field(default=None, max_length=128)
    enabled: bool = True
    data: dict[str, Any] | None = None


class ConfigEffectiveGlobalPolicyUpdateBody(BaseModel):
    channel_ids: list[str] | None = None
    default_model: str | None = Field(default=None, max_length=128)
    video_model: str | None = Field(default=None, max_length=128)
    audio_model: str | None = Field(default=None, max_length=128)
    vision_model: str | None = Field(default=None, max_length=128)
    enabled: bool | None = None
    data: dict[str, Any] | None = None


class ConfigEffectiveGlobalPolicyOut(BaseModel):
    id: int
    jiuwenclaw_id: str
    default_model: str | None
    video_model: str | None
    audio_model: str | None
    vision_model: str | None
    channel_ids: list[str]
    enabled: bool
    data: dict[str, Any] | None
    created_at: str | None
    updated_at: str | None


# --- config_default_template_mapping ---

class ConfigDefaultTemplateMappingCreateBody(BaseModel):
    user_id: str | None = Field(default=None, max_length=512)
    group_id: str | None = Field(default=None, max_length=512)
    template_id: str = Field(..., max_length=512)
    template_type: str = Field(..., max_length=512)
    enabled: bool = True
    data: dict[str, Any] | None = None


class ConfigDefaultTemplateMappingUpdateBody(BaseModel):
    user_id: str | None = Field(default=None, max_length=512)
    group_id: str | None = Field(default=None, max_length=512)
    template_id: str | None = Field(default=None, max_length=512)
    template_type: str | None = Field(default=None, max_length=512)
    enabled: bool | None = None
    data: dict[str, Any] | None = None


class ConfigDefaultTemplateMappingOut(BaseModel):
    id: int
    jiuwenclaw_id: str
    user_id: str | None
    group_id: str | None
    template_id: str
    template_type: str
    enabled: bool
    data: dict[str, Any] | None
    created_at: str | None
    updated_at: str | None
