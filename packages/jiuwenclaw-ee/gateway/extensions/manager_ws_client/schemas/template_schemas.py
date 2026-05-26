from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ModelTemplateUpdateRequest(BaseModel):
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


class ExtensionConfigTemplateUpdateRequest(BaseModel):
    template_name: str | None = Field(default=None, max_length=128)
    description: str | None = Field(default=None, max_length=512)
    component: str | None = Field(default=None, max_length=32)
    hook_type: str | None = Field(default=None, max_length=32)
    hook_config: dict[str, Any] | None = None
    custom_config: dict[str, Any] | None = None
    enabled: bool | None = None
    data: dict[str, Any] | None = None


class SkillWhitelistTemplateUpdateRequest(BaseModel):
    template_name: str | None = Field(default=None, max_length=128)
    description: str | None = Field(default=None, max_length=512)
    skill_id: str | None = Field(default=None, max_length=512)
    skill_version: str | None = Field(default=None, max_length=64)
    skill_source: str | None = Field(default=None, max_length=512)
    enabled: bool | None = None
    data: dict[str, Any] | None = None


class ServiceConfigTemplateUpdateRequest(BaseModel):
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
    host_path: str | None = Field(default=None, max_length=512)
    host_mount_path: str | None = Field(default=None, max_length=512)
    mode: str | None = Field(default=None, max_length=512)
    node_name: str | None = Field(default=None, max_length=512)
    cpu_request: str | None = Field(default=None, max_length=32)
    memory_request: str | None = Field(default=None, max_length=32)
    cpu_limit: str | None = Field(default=None, max_length=32)
    memory_limit: str | None = Field(default=None, max_length=32)
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
