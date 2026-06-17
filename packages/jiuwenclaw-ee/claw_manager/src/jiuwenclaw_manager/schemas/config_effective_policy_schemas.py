"""配置生效策略与默认模板映射 API 请求/响应模型。"""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, BeforeValidator

from jiuwenclaw_manager.infrastructure.common import (
    coerce_routing_id,
    coerce_routing_id_optional,
)
from jiuwenclaw_manager.infrastructure.template_ref import (
    normalize_template_ref,
    normalize_template_ref_optional,
)
from jiuwenclaw_manager.schemas.template_slot_schemas import (
    DefaultTemplateMappingTypeLiteral,
    TEMPLATE_REF_SLOTS,
)

TemplateRefField = Annotated[dict[str, list[str]], BeforeValidator(normalize_template_ref)]
OptionalTemplateRefField = Annotated[
    dict[str, list[str]] | None,
    BeforeValidator(normalize_template_ref_optional),
]
RoutingIdField = Annotated[str, BeforeValidator(coerce_routing_id)]
OptionalRoutingIdField = Annotated[str | None, BeforeValidator(coerce_routing_id_optional)]


# --- config_effective_agent_policy ---

class ConfigEffectiveAgentPolicyCreateBody(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    policy_name: str = Field(..., max_length=128, min_length=1)
    policy_desc: str | None = Field(default=None, max_length=512)
    agent_id: RoutingIdField = Field(..., max_length=512)
    service_policy_id: str = Field(..., max_length=100, min_length=1)
    priority: int = Field(default=0)
    match_expr: str | None = None
    template_ref: TemplateRefField = Field(
        default_factory=dict,
        description="槽位名 -> template_id 数组；元素可为 UUID 或 ${user::…}/${group::…} or <template_id>",
    )
    send_file_allowed: bool = False
    enabled: bool = True
    data: dict[str, Any] | None = None


class ConfigEffectiveAgentPolicyUpdateBody(BaseModel):
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


class ConfigEffectiveAgentPolicyOut(BaseModel):
    id: int
    jiuwenclaw_id: str
    policy_id: str
    policy_name: str
    policy_desc: str | None
    agent_id: str
    service_policy_id: str
    priority: int
    match_expr: str | None
    template_ref: dict[str, list[str]]
    send_file_allowed: bool
    enabled: bool
    data: dict[str, Any] | None
    created_at: str | None
    updated_at: str | None


class ConfigEffectiveAgentPolicyListQuery(BaseModel):
    """Agent 层级策略列表查询参数。"""

    page: int = Field(1, ge=1)
    page_size: int = Field(20, ge=1, le=200)
    service_policy_id: str | None = Field(
        default=None,
        description="按服务级策略 policy_id 筛选",
    )
    enabled: bool | None = None
    send_file_allowed: bool | None = None
    search: str | None = Field(
        default=None,
        description="搜索策略 ID、名称、描述、Agent ID、关联服务策略、优先级或匹配表达式",
    )
    sort_by: str | None = Field(
        default=None,
        description=(
            "排序字段：policy_name、policy_desc、service_policy_id、priority、"
            "match_expr、agent_id、updated_at"
        ),
    )
    sort_order: str | None = Field(default=None, description="排序方向：asc、desc")


# --- config_effective_service_policy ---

class ConfigEffectiveServicePolicyCreateBody(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    policy_name: str = Field(..., max_length=128, min_length=1)
    policy_desc: str | None = Field(default=None, max_length=512)
    service_id: RoutingIdField = Field(..., max_length=512)
    priority: int
    match_expr: str | None = None
    template_ref: TemplateRefField = Field(default_factory=dict)
    enabled: bool = True
    data: dict[str, Any] | None = None


class ConfigEffectiveServicePolicyUpdateBody(BaseModel):
    policy_name: str | None = Field(default=None, max_length=128, min_length=1)
    policy_desc: str | None = Field(default=None, max_length=512)
    service_id: OptionalRoutingIdField = Field(default=None, max_length=512)
    priority: int | None = None
    match_expr: str | None = None
    template_ref: OptionalTemplateRefField = None
    enabled: bool | None = None
    data: dict[str, Any] | None = None


class ConfigEffectiveServicePolicyOut(BaseModel):
    id: int
    jiuwenclaw_id: str
    policy_id: str
    policy_name: str
    policy_desc: str | None
    service_id: str
    priority: int
    match_expr: str | None
    template_ref: dict[str, list[str]]
    enabled: bool
    data: dict[str, Any] | None
    created_at: str | None
    updated_at: str | None


class ConfigEffectiveServicePolicyListQuery(BaseModel):
    """服务级策略列表查询参数。"""

    page: int = Field(1, ge=1)
    page_size: int = Field(20, ge=1, le=200)
    enabled: bool | None = None
    search: str | None = Field(
        default=None,
        description="搜索策略 ID、名称、描述、服务 ID、优先级或匹配表达式",
    )
    sort_by: str | None = Field(
        default=None,
        description="排序字段：policy_name、policy_desc、priority、match_expr、service_id、updated_at",
    )
    sort_order: str | None = Field(default=None, description="排序方向：asc、desc")


# --- config_effective_global_policy ---

class ConfigEffectiveGlobalPolicyCreateBody(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    policy_name: str = Field(..., max_length=128, min_length=1)
    policy_desc: str | None = Field(default=None, max_length=512)
    priority: int = 0
    template_ref: TemplateRefField = Field(default_factory=dict)
    enabled: bool = True
    data: dict[str, Any] | None = None


class ConfigEffectiveGlobalPolicyUpdateBody(BaseModel):
    policy_name: str | None = Field(default=None, max_length=128, min_length=1)
    policy_desc: str | None = Field(default=None, max_length=512)
    priority: int | None = None
    template_ref: OptionalTemplateRefField = None
    enabled: bool | None = None
    data: dict[str, Any] | None = None


class ConfigEffectiveGlobalPolicyOut(BaseModel):
    id: int
    jiuwenclaw_id: str
    policy_id: str
    policy_name: str
    policy_desc: str | None
    priority: int
    template_ref: dict[str, list[str]]
    enabled: bool
    data: dict[str, Any] | None
    created_at: str | None
    updated_at: str | None


class ConfigEffectiveGlobalPolicyListQuery(BaseModel):
    """全局兜底策略列表查询参数。"""

    page: int = Field(1, ge=1)
    page_size: int = Field(20, ge=1, le=200)
    enabled: bool | None = None
    search: str | None = Field(default=None, description="搜索策略 ID、名称、描述或优先级")
    sort_by: str | None = Field(
        default=None,
        description="排序字段：policy_name、policy_desc、priority、updated_at",
    )
    sort_order: str | None = Field(default=None, description="排序方向：asc、desc")


# --- config_default_template_mapping ---


class ConfigDefaultTemplateMappingCreateBody(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    policy_name: str = Field(..., max_length=128, min_length=1)
    policy_desc: str | None = Field(default=None, max_length=512)
    user_id: str | None = Field(default=None, max_length=512)
    group_id: str | None = Field(default=None, max_length=512)
    priority: int = 0
    template_id: str = Field(..., max_length=100, min_length=1)
    template_type: DefaultTemplateMappingTypeLiteral
    enabled: bool = True
    data: dict[str, Any] | None = None


class ConfigDefaultTemplateMappingUpdateBody(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    policy_name: str | None = Field(default=None, max_length=128, min_length=1)
    policy_desc: str | None = Field(default=None, max_length=512)
    user_id: str | None = Field(default=None, max_length=512)
    group_id: str | None = Field(default=None, max_length=512)
    priority: int | None = None
    template_id: str | None = Field(default=None, max_length=100, min_length=1)
    template_type: DefaultTemplateMappingTypeLiteral | None = None
    enabled: bool | None = None
    data: dict[str, Any] | None = None


class ConfigDefaultTemplateMappingOut(BaseModel):
    id: int
    jiuwenclaw_id: str
    policy_id: str
    policy_name: str
    policy_desc: str | None
    user_id: str | None
    group_id: str | None
    priority: int
    template_id: str
    template_type: DefaultTemplateMappingTypeLiteral
    enabled: bool
    data: dict[str, Any] | None
    created_at: str | None
    updated_at: str | None


class ConfigDefaultTemplateMappingListQuery(BaseModel):
    """默认模板映射列表查询参数。"""

    page: int = Field(1, ge=1)
    page_size: int = Field(20, ge=1, le=200)
    user_id: str | None = Field(default=None, description="按 user_id 精确筛选")
    group_id: str | None = Field(default=None, description="按 group_id 精确筛选")
    template_type: DefaultTemplateMappingTypeLiteral | None = Field(
        default=None,
        description=f"模板类型：{' / '.join(sorted(TEMPLATE_REF_SLOTS))}",
    )
    template_id: str | None = Field(default=None, description="按 template_id 精确筛选")
    enabled: bool | None = None
    search: str | None = Field(
        default=None,
        description="搜索策略 ID、名称、描述、User ID、Group ID、槽位、模板 ID 或优先级",
    )
    sort_by: str | None = Field(
        default=None,
        description=(
            "排序字段：policy_name、policy_desc、priority、user_id、group_id、"
            "template_type、template_id、updated_at"
        ),
    )
    sort_order: str | None = Field(default=None, description="排序方向：asc、desc")
