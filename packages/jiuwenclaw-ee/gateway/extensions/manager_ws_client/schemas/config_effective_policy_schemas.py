from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ConfigEffectiveServicePolicyUpdateRequest(BaseModel):
    service_id: str | None = Field(default=None, max_length=512)
    priority: int | None = None
    match_expr: str | None = None
    default_model: str | None = Field(default=None, max_length=128)
    video_model: str | None = Field(default=None, max_length=128)
    audio_model: str | None = Field(default=None, max_length=128)
    vision_model: str | None = Field(default=None, max_length=128)
    enabled: bool | None = None
    data: dict[str, Any] | None = None


class ConfigEffectiveServicePolicyCreateRequest(BaseModel):
    """创建 Service 层级配置生效策略（WebSocket 同步用）。"""

    model_config = ConfigDict(str_strip_whitespace=True)

    service_id: str = Field(..., max_length=512, min_length=1)
    priority: int
    match_expr: str | None = None
    default_model: str | None = Field(default=None, max_length=128)
    video_model: str | None = Field(default=None, max_length=128)
    audio_model: str | None = Field(default=None, max_length=128)
    vision_model: str | None = Field(default=None, max_length=128)
    enabled: bool = True
    data: dict[str, Any] | None = None


class ConfigEffectiveGlobalPolicyUpdateRequest(BaseModel):
    channel_ids: list[str] | None = Field(
        default=None,
        description="启用的 channel_template.id 列表；传 [] 表示清空绑定",
    )
    default_model: str | None = Field(default=None, max_length=128)
    video_model: str | None = Field(default=None, max_length=128)
    audio_model: str | None = Field(default=None, max_length=128)
    vision_model: str | None = Field(default=None, max_length=128)
    enabled: bool | None = None
    data: dict[str, Any] | None = None


class ConfigEffectiveGlobalPolicyCreateRequest(BaseModel):
    """创建全局兜底配置生效策略（WebSocket 同步用，每实例至多一条）。"""

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


class ConfigDefaultTemplateMappingUpdateRequest(BaseModel):
    user_id: str | None = Field(default=None, max_length=512)
    group_id: str | None = Field(default=None, max_length=512)
    template_id: str | None = Field(default=None, max_length=512)
    template_type: str | None = Field(default=None, max_length=512)
    enabled: bool | None = None
    data: dict[str, Any] | None = None


class ConfigEffectiveAgentPolicyUpdateRequest(BaseModel):
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


class ConfigEffectiveAgentPolicyCreateRequest(BaseModel):
    """创建 Agent 层级配置生效策略（WebSocket 同步用）。"""

    model_config = ConfigDict(str_strip_whitespace=True)

    agent_id: str = Field(..., max_length=512, min_length=1)
    service_policy_id: int
    priority: int = 0
    match_expr: str | None = None
    default_model: str | None = Field(default=None, max_length=128)
    video_model: str | None = Field(default=None, max_length=128)
    audio_model: str | None = Field(default=None, max_length=128)
    vision_model: str | None = Field(default=None, max_length=128)
    enabled: bool = True
    data: dict[str, Any] | None = None


class ConfigDefaultTemplateMappingCreateRequest(BaseModel):
    """创建用户/群组默认模板映射（WebSocket 同步用）。"""

    model_config = ConfigDict(str_strip_whitespace=True)

    user_id: str | None = Field(default=None, max_length=512)
    group_id: str | None = Field(default=None, max_length=512)
    template_id: str = Field(..., max_length=512, min_length=1)
    template_type: str = Field(..., max_length=512, min_length=1)
    enabled: bool = True
    data: dict[str, Any] | None = None
