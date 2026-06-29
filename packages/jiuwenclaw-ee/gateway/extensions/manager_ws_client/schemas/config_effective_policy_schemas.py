from __future__ import annotations

from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, BeforeValidator

from ..core.enterprise_config.routing_id import (
    coerce_routing_id,
    coerce_routing_id_optional,
)
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
RoutingIdField = Annotated[str, BeforeValidator(coerce_routing_id)]
OptionalRoutingIdField = Annotated[str | None, BeforeValidator(coerce_routing_id_optional)]


class ConfigEffectiveServicePolicyUpdateRequest(BaseModel):
    policy_name: str | None = Field(default=None, max_length=128, min_length=1)
    policy_desc: str | None = Field(default=None, max_length=512)
    service_id: OptionalRoutingIdField = Field(default=None, max_length=512)
    priority: int | None = None
    match_expr: str | None = None
    template_ref: OptionalTemplateRefField = None
    enabled: bool | None = None
    data: dict[str, Any] | None = None


class ConfigEffectiveServicePolicyCreateRequest(BaseModel):
    """创建 Service 层级配置生效策略（WebSocket 同步用）。"""

    model_config = ConfigDict(str_strip_whitespace=True)

    policy_id: str = Field(..., max_length=100, min_length=1)
    policy_name: str = Field(..., max_length=128, min_length=1)
    policy_desc: str | None = Field(default=None, max_length=512)
    service_id: RoutingIdField = Field(..., max_length=512, min_length=1)
    priority: int
    match_expr: str | None = None
    template_ref: TemplateRefField = Field(default_factory=dict)
    enabled: bool = True
    data: dict[str, Any] | None = None


class ConfigEffectiveGlobalPolicyUpdateRequest(BaseModel):
    policy_name: str | None = Field(default=None, max_length=128, min_length=1)
    policy_desc: str | None = Field(default=None, max_length=512)
    priority: int | None = None
    template_ref: OptionalTemplateRefField = None
    enabled: bool | None = None
    data: dict[str, Any] | None = None


class ConfigEffectiveGlobalPolicyCreateRequest(BaseModel):
    """创建全局兜底配置生效策略（WebSocket 同步用，每实例至多一条）。"""

    model_config = ConfigDict(str_strip_whitespace=True)

    policy_id: str = Field(..., max_length=100, min_length=1)
    policy_name: str = Field(..., max_length=128, min_length=1)
    policy_desc: str | None = Field(default=None, max_length=512)
    priority: int = 0
    template_ref: TemplateRefField = Field(default_factory=dict)
    enabled: bool = True
    data: dict[str, Any] | None = None


class ConfigDefaultTemplateMappingUpdateRequest(BaseModel):
    policy_name: str | None = Field(default=None, max_length=128, min_length=1)
    policy_desc: str | None = Field(default=None, max_length=512)
    scope_type: str | None = Field(default=None, max_length=32)
    scope_id: str | None = Field(default=None, max_length=512)
    priority: int | None = None
    template_id: str | None = Field(default=None, max_length=512)
    template_type: str | None = Field(default=None, max_length=512)
    enabled: bool | None = None
    data: dict[str, Any] | None = None


class ConfigEffectiveAgentPolicyUpdateRequest(BaseModel):
    policy_name: str | None = Field(default=None, max_length=128, min_length=1)
    policy_desc: str | None = Field(default=None, max_length=512)
    agent_id: OptionalRoutingIdField = Field(default=None, max_length=512)
    service_policy_id: str | None = Field(default=None, max_length=100, min_length=1)
    priority: int | None = None
    match_expr: str | None = None
    template_ref: OptionalTemplateRefField = None
    send_file_allowed: bool | None = None
    enabled: bool | None = None
    data: dict[str, Any] | None = None


class ConfigEffectiveAgentPolicyCreateRequest(BaseModel):
    """创建 Agent 层级配置生效策略（WebSocket 同步用）。"""

    model_config = ConfigDict(str_strip_whitespace=True)

    policy_id: str = Field(..., max_length=100, min_length=1)
    policy_name: str = Field(..., max_length=128, min_length=1)
    policy_desc: str | None = Field(default=None, max_length=512)
    agent_id: RoutingIdField = Field(..., max_length=512, min_length=1)
    service_policy_id: str = Field(..., max_length=100, min_length=1)
    priority: int = 0
    match_expr: str | None = None
    template_ref: TemplateRefField = Field(default_factory=dict)
    send_file_allowed: bool = False
    enabled: bool = True
    data: dict[str, Any] | None = None


class ConfigDefaultTemplateMappingCreateRequest(BaseModel):
    """创建作用域默认模板映射（WebSocket 同步用）。"""

    model_config = ConfigDict(str_strip_whitespace=True)

    policy_id: str = Field(..., max_length=100, min_length=1)
    policy_name: str = Field(..., max_length=128, min_length=1)
    policy_desc: str | None = Field(default=None, max_length=512)
    scope_type: str = Field(..., max_length=32, min_length=1)
    scope_id: str = Field(..., max_length=512, min_length=1)
    priority: int = 0
    template_id: str = Field(..., max_length=512, min_length=1)
    template_type: str = Field(..., max_length=512, min_length=1)
    enabled: bool = True
    data: dict[str, Any] | None = None
