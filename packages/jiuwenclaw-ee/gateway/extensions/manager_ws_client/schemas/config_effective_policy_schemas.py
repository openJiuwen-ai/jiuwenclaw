from __future__ import annotations

from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, BeforeValidator

from ..infrastructure.utils import normalize_template_ref

TemplateRefField = Annotated[
    dict[str, list[str]],
    BeforeValidator(lambda value: {} if value is None else normalize_template_ref(value)),
]
OptionalTemplateRefField = Annotated[
    dict[str, list[str]] | None,
    BeforeValidator(
        lambda value: None if value is None else normalize_template_ref(value)
    ),
]


class ConfigEffectiveServicePolicyUpdateRequest(BaseModel):
    service_id: str | None = Field(default=None, max_length=512)
    priority: int | None = None
    match_expr: str | None = None
    template_ref: OptionalTemplateRefField = None
    enabled: bool | None = None
    data: dict[str, Any] | None = None


class ConfigEffectiveServicePolicyCreateRequest(BaseModel):
    """创建 Service 层级配置生效策略（WebSocket 同步用）。"""

    model_config = ConfigDict(str_strip_whitespace=True)

    service_id: str = Field(..., max_length=512, min_length=1)
    priority: int
    match_expr: str | None = None
    template_ref: TemplateRefField = Field(default_factory=dict)
    enabled: bool = True
    data: dict[str, Any] | None = None


class ConfigEffectiveGlobalPolicyUpdateRequest(BaseModel):
    priority: int | None = None
    template_ref: OptionalTemplateRefField = None
    enabled: bool | None = None
    data: dict[str, Any] | None = None


class ConfigEffectiveGlobalPolicyCreateRequest(BaseModel):
    """创建全局兜底配置生效策略（WebSocket 同步用，每实例至多一条）。"""

    priority: int = 0
    template_ref: TemplateRefField = Field(default_factory=dict)
    enabled: bool = True
    data: dict[str, Any] | None = None


class ConfigDefaultTemplateMappingUpdateRequest(BaseModel):
    user_id: str | None = Field(default=None, max_length=512)
    group_id: str | None = Field(default=None, max_length=512)
    priority: int | None = None
    template_id: str | None = Field(default=None, max_length=512)
    template_type: str | None = Field(default=None, max_length=512)
    enabled: bool | None = None
    data: dict[str, Any] | None = None


class ConfigEffectiveAgentPolicyUpdateRequest(BaseModel):
    agent_id: str | None = Field(default=None, max_length=512)
    service_policy_id: int | None = None
    priority: int | None = None
    match_expr: str | None = None
    template_ref: OptionalTemplateRefField = None
    send_file_allowed: bool | None = None
    enabled: bool | None = None
    data: dict[str, Any] | None = None


class ConfigEffectiveAgentPolicyCreateRequest(BaseModel):
    """创建 Agent 层级配置生效策略（WebSocket 同步用）。"""

    model_config = ConfigDict(str_strip_whitespace=True)

    agent_id: str = Field(..., max_length=512, min_length=1)
    service_policy_id: int
    priority: int = 0
    match_expr: str | None = None
    template_ref: TemplateRefField = Field(default_factory=dict)
    send_file_allowed: bool = True
    enabled: bool = True
    data: dict[str, Any] | None = None


class ConfigDefaultTemplateMappingCreateRequest(BaseModel):
    """创建用户/群组默认模板映射（WebSocket 同步用）。"""

    model_config = ConfigDict(str_strip_whitespace=True)

    user_id: str | None = Field(default=None, max_length=512)
    group_id: str | None = Field(default=None, max_length=512)
    priority: int = 0
    template_id: str = Field(..., max_length=512, min_length=1)
    template_type: str = Field(..., max_length=512, min_length=1)
    enabled: bool = True
    data: dict[str, Any] | None = None
