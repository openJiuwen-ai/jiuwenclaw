"""模板 API 请求/响应模型：model_template、extension_config_template、skill_whitelist_template、service_config_template。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ModelTemplateCreateBody(BaseModel):
    template_name: str = Field(..., max_length=128)
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
    verify_ssl: bool = False
    enabled: bool = True
    data: dict[str, Any] | None = None


class ModelTemplateUpdateBody(BaseModel):
    template_name: str | None = Field(default=None, max_length=128)
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
    template_id: str
    template_name: str
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


class ExtensionConfigTemplateCreateBody(BaseModel):
    template_name: str = Field(..., max_length=128)
    description: str | None = Field(default=None, max_length=512)
    component: str = Field(..., max_length=32)
    hook_type: str = Field(..., max_length=32)
    hook_config: dict[str, Any]
    custom_config: dict[str, Any] | None = None
    enabled: bool = True
    data: dict[str, Any] | None = None


class ExtensionConfigTemplateUpdateBody(BaseModel):
    template_name: str | None = Field(default=None, max_length=128)
    description: str | None = Field(default=None, max_length=512)
    component: str | None = Field(default=None, max_length=32)
    hook_type: str | None = Field(default=None, max_length=32)
    hook_config: dict[str, Any] | None = None
    custom_config: dict[str, Any] | None = None
    enabled: bool | None = None
    data: dict[str, Any] | None = None


class ExtensionConfigTemplateListQuery(BaseModel):
    """扩展配置模板列表查询参数。"""

    page: int = Field(1, ge=1)
    page_size: int = Field(20, ge=1, le=200)
    enabled: bool | None = None
    component: str | None = Field(
        default=None,
        description="目标组件：gateway / agent_server",
    )
    hook_type: str | None = Field(
        default=None,
        description="钩子类型：pre_request / post_request / error / schedule",
    )


class ExtensionConfigTemplateOut(BaseModel):
    id: int
    template_id: str
    template_name: str
    description: str | None
    component: str
    hook_type: str
    hook_config: dict[str, Any]
    custom_config: dict[str, Any] | None
    enabled: bool
    data: dict[str, Any] | None
    created_at: str | None
    updated_at: str | None


class SkillWhitelistTemplateCreateBody(BaseModel):
    template_name: str = Field(..., max_length=128)
    description: str | None = Field(default=None, max_length=512)
    skill_id: str = Field(..., max_length=512)
    skill_version: str = Field(..., max_length=64)
    skill_source: str = Field(..., max_length=2048)
    enabled: bool = True
    data: dict[str, Any] | None = None


class SkillWhitelistTemplateUpdateBody(BaseModel):
    template_name: str | None = Field(default=None, max_length=128)
    description: str | None = Field(default=None, max_length=512)
    skill_id: str | None = Field(default=None, max_length=512)
    skill_version: str | None = Field(default=None, max_length=64)
    skill_source: str | None = Field(default=None, max_length=2048)
    enabled: bool | None = None
    data: dict[str, Any] | None = None


class SkillWhitelistTemplateListQuery(BaseModel):
    page: int = Field(1, ge=1)
    page_size: int = Field(20, ge=1, le=200)
    enabled: bool | None = None
    skill_id: str | None = Field(default=None, max_length=512)
    skill_source: str | None = Field(default=None, max_length=2048)


class SkillWhitelistTemplateOut(BaseModel):
    id: int
    template_id: str
    template_name: str
    description: str | None
    skill_id: str
    skill_version: str
    skill_source: str
    enabled: bool
    data: dict[str, Any] | None
    created_at: str | None
    updated_at: str | None


class ServiceConfigTemplateCreateBody(BaseModel):
    template_name: str = Field(..., max_length=128)
    description: str | None = Field(default=None, max_length=512)
    agent_image: str = Field(..., max_length=512)
    namespace: str = Field(..., max_length=128)
    pod_name: str | None = Field(default=None, max_length=128)
    container_name: str = Field(..., max_length=128)
    container_port: int = Field(..., ge=1, le=65535)
    port_name: str = Field(default="http", max_length=64)
    image_pull_policy: str = Field(default="IfNotPresent", max_length=32)
    replicas: int = Field(default=1, ge=1)
    kubeconfig: str | None = Field(default=None, max_length=512)
    agent_runtime: str | None = Field(default=None, max_length=128)
    readiness_initial_delay: int = Field(default=5, ge=0)
    readiness_period: int = Field(default=10, ge=1)
    ready_timeout: int = Field(default=300, ge=1)
    ready_poll_interval: int = Field(default=2, ge=1)
    nfs_server: str | None = Field(default=None, max_length=256)
    nfs_path: str = Field(default="/", max_length=512)
    nfs_mount_path: str | None = Field(default=None, max_length=512)
    agent_cpu_request: str | None = Field(default=None, max_length=32)
    agent_memory_request: str | None = Field(default=None, max_length=32)
    agent_cpu_limit: str | None = Field(default=None, max_length=32)
    agent_memory_limit: str | None = Field(default=None, max_length=32)
    jiuwenbox_cpu_request: str | None = Field(default=None, max_length=32)
    jiuwenbox_memory_request: str | None = Field(default=None, max_length=32)
    jiuwenbox_cpu_limit: str | None = Field(default=None, max_length=32)
    jiuwenbox_memory_limit: str | None = Field(default=None, max_length=32)
    min_idle_services: int = Field(default=1, ge=0)
    max_services: int = Field(default=10, ge=1)
    service_concurrency: int = Field(default=10, ge=1)
    service_ttl: int = Field(default=30, ge=1)
    autoscale_interval: float = Field(default=0.2, gt=0)
    message_timeout: int = Field(default=300, ge=1)
    session_concurrency: int = Field(default=10, ge=1)
    session_ttl: int = Field(default=20, ge=1)
    enabled: bool = True
    data: dict[str, Any] | None = None


class ServiceConfigTemplateUpdateBody(BaseModel):
    template_name: str | None = Field(default=None, max_length=128)
    description: str | None = Field(default=None, max_length=512)
    agent_image: str | None = Field(default=None, max_length=512)
    namespace: str | None = Field(default=None, max_length=128)
    pod_name: str | None = Field(default=None, max_length=128)
    container_name: str | None = Field(default=None, max_length=128)
    container_port: int | None = Field(default=None, ge=1, le=65535)
    port_name: str | None = Field(default=None, max_length=64)
    image_pull_policy: str | None = Field(default=None, max_length=32)
    replicas: int | None = Field(default=None, ge=1)
    kubeconfig: str | None = Field(default=None, max_length=512)
    agent_runtime: str | None = Field(default=None, max_length=128)
    readiness_initial_delay: int | None = Field(default=None, ge=0)
    readiness_period: int | None = Field(default=None, ge=1)
    ready_timeout: int | None = Field(default=None, ge=1)
    ready_poll_interval: int | None = Field(default=None, ge=1)
    nfs_server: str | None = Field(default=None, max_length=256)
    nfs_path: str | None = Field(default=None, max_length=512)
    nfs_mount_path: str | None = Field(default=None, max_length=512)
    agent_cpu_request: str | None = Field(default=None, max_length=32)
    agent_memory_request: str | None = Field(default=None, max_length=32)
    agent_cpu_limit: str | None = Field(default=None, max_length=32)
    agent_memory_limit: str | None = Field(default=None, max_length=32)
    jiuwenbox_cpu_request: str | None = Field(default=None, max_length=32)
    jiuwenbox_memory_request: str | None = Field(default=None, max_length=32)
    jiuwenbox_cpu_limit: str | None = Field(default=None, max_length=32)
    jiuwenbox_memory_limit: str | None = Field(default=None, max_length=32)
    min_idle_services: int | None = Field(default=None, ge=0)
    max_services: int | None = Field(default=None, ge=1)
    service_concurrency: int | None = Field(default=None, ge=1)
    service_ttl: int | None = Field(default=None, ge=1)
    autoscale_interval: float | None = Field(default=None, gt=0)
    message_timeout: int | None = Field(default=None, ge=1)
    session_concurrency: int | None = Field(default=None, ge=1)
    session_ttl: int | None = Field(default=None, ge=1)
    enabled: bool | None = None
    data: dict[str, Any] | None = None


class ServiceConfigTemplateListQuery(BaseModel):
    page: int = Field(1, ge=1)
    page_size: int = Field(20, ge=1, le=200)
    enabled: bool | None = None
    namespace: str | None = Field(default=None, max_length=128)


class ServiceConfigTemplateOut(BaseModel):
    id: int
    template_id: str
    template_name: str
    description: str | None
    agent_image: str
    namespace: str
    pod_name: str | None
    container_name: str
    container_port: int
    port_name: str
    image_pull_policy: str
    replicas: int
    kubeconfig: str | None
    agent_runtime: str | None
    readiness_initial_delay: int
    readiness_period: int
    ready_timeout: int
    ready_poll_interval: int
    nfs_server: str | None
    nfs_path: str
    nfs_mount_path: str | None
    agent_cpu_request: str | None
    agent_memory_request: str | None
    agent_cpu_limit: str | None
    agent_memory_limit: str | None
    jiuwenbox_cpu_request: str | None
    jiuwenbox_memory_request: str | None
    jiuwenbox_cpu_limit: str | None
    jiuwenbox_memory_limit: str | None
    min_idle_services: int
    max_services: int
    service_concurrency: int
    service_ttl: int
    autoscale_interval: float
    message_timeout: int
    session_concurrency: int
    session_ttl: int
    enabled: bool
    data: dict[str, Any] | None
    created_at: str | None
    updated_at: str | None
