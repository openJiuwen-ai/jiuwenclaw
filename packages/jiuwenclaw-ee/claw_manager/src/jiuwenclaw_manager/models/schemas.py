from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ApiResponse(BaseModel):
    """与设计文档一致的通用 API 外层结构。"""

    code: int = 200
    message: str = "success"
    data: Any = None


class CreateInstanceBody(BaseModel):
    jiuwenclaw_name: str = Field(..., max_length=128)
    description: str | None = None
    k8s_master_host: str
    k8s_auth_type: str
    k8s_auth_config: dict[str, Any]
    k8s_namespace: str
    resource_quota: dict[str, Any] | None = None
    creator_id: str = Field(default="system", max_length=64)
    group_id: str = Field(default="default", max_length=64)
    space_id: str = Field(default="default", max_length=64)
    # 指向网关侧 agent_client_rest 根地址，如 http://127.0.0.1:18080（与 config.yaml extensions.agent_client_rest 一致）
    management_api_base: str | None = None


class PatchInstanceDataBody(BaseModel):
    """合并写入 instance_info.data（用于补录 management_api_base 等）。"""

    data: dict[str, Any]


class ProvisionLocalInstanceBody(BaseModel):
    """本地拉起 Gateway + AgentServer（需 CLAWMANAGER_ALLOW_LOCAL_PROVISION=true 且配置 RabbitMQ）。"""

    jiuwenclaw_name: str = Field(default="local-instance", max_length=128)
    creator_id: str = Field(default="system", max_length=64)
    description: str | None = None


class InstanceSummary(BaseModel):
    jiuwenclaw_id: str
    jiuwenclaw_name: str
    status: str
    k8s_namespace: str
    group_id: str
    space_id: str
    created_at: str | None = None


class InstanceDetail(InstanceSummary):
    description: str | None
    k8s_master_host: str
    k8s_auth_type: str
    resource_quota: dict[str, Any] | None
    data: dict[str, Any] | None


class ServiceStatusItem(BaseModel):
    service_id: str
    service_type: str
    component_role: str
    status: str
    last_heartbeat: str | None
    endpoint: str | None
    version: str | None


class ServiceStatusList(BaseModel):
    items: list[ServiceStatusItem]


# --- model_template ---

class ModelTemplateCreateBody(BaseModel):
    display_name: str = Field(..., max_length=128)
    description: str | None = Field(default=None, max_length=512)
    model_type: str | list[str]
    model_tags: list[str] | None = None
    api_base: str = Field(..., max_length=512)
    api_key: str
    model_id: str = Field(..., max_length=128)
    model_provider: str = Field(..., max_length=64)
    parameters: dict[str, Any] | None = None
    timeout: int = Field(default=60, ge=1)
    retry_count: int = Field(default=3, ge=0)
    enable_streaming: bool = True
    enable_function_calling: bool = True
    verify_ssl: bool = True
    enabled: bool = True
    data: dict[str, Any] | None = None


class ModelTemplateUpdateBody(BaseModel):
    display_name: str | None = Field(default=None, max_length=128)
    description: str | None = Field(default=None, max_length=512)
    model_type: str | list[str] | None = None
    model_tags: list[str] | None = None
    api_base: str | None = Field(default=None, max_length=512)
    api_key: str | None = None
    model_id: str | None = Field(default=None, max_length=128)
    model_provider: str | None = Field(default=None, max_length=64)
    parameters: dict[str, Any] | None = None
    timeout: int | None = Field(default=None, ge=1)
    retry_count: int | None = Field(default=None, ge=0)
    enable_streaming: bool | None = None
    enable_function_calling: bool | None = None
    verify_ssl: bool | None = None
    enabled: bool | None = None
    data: dict[str, Any] | None = None


class ModelTemplateOut(BaseModel):
    id: int
    jiuwenclaw_id: str
    display_name: str
    description: str | None
    model_type: str | list[str]
    model_tags: list[str] | None
    api_base: str
    api_key: str
    model_id: str
    model_provider: str
    parameters: dict[str, Any] | None
    timeout: int
    retry_count: int
    enable_streaming: bool
    enable_function_calling: bool
    verify_ssl: bool
    enabled: bool
    data: dict[str, Any] | None
    created_at: str | None
    updated_at: str | None


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
