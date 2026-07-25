from __future__ import annotations

from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field
import re


# cron 字段：数字、字母（JAN/MON）、* / , - ? # L W
_CRON_FIELD_RE = re.compile(r"^[\w*/,\-?#]+$", re.IGNORECASE)
# 常见 cron：5 段（分 时 日 月 周）、6 段（含秒）、7 段（含年）
_CRON_FIELD_COUNTS = frozenset({5, 6, 7})


def is_valid_hook_schedule(value: str) -> bool:
    """校验 hook_config.schedule 为合法 cron 表达式（5/6/7 段）。"""
    text = value.strip()
    if not text:
        return False
    parts = text.split()
    if len(parts) not in _CRON_FIELD_COUNTS:
        return False
    return all(_CRON_FIELD_RE.fullmatch(part) for part in parts)


def normalize_hook_schedule(schedule: str | None, *, required: bool) -> str | None:
    """规范化 schedule；required 时不可为空，有值时须为合法 cron。"""
    text = (schedule or "").strip()
    if not text:
        if required:
            raise ValueError("hook_config.schedule is required when hook_type=schedule")
        return None
    if not is_valid_hook_schedule(text):
        raise ValueError(
            "hook_config.schedule must be a cron expression "
            "(5/6/7 fields, e.g. '0 */5 * * *' or '0 0 */5 * * *')"
        )
    return text


class HookConfig(BaseModel):
    """扩展模板 hook_config 结构（与设计文档一致）。"""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    handler: str = Field(..., min_length=1)
    params: dict[str, Any] | None = None
    schedule: str | None = None
    data: dict[str, Any] | None = None


class ModelTemplateUpdateRequest(BaseModel):
    template_name: str | None = Field(default=None, max_length=128)
    description: str | None = Field(default=None, max_length=512)
    model_type: list[str] | None = None
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


class EmbeddingTemplateUpdateRequest(BaseModel):
    template_name: str | None = Field(
        default=None,
        max_length=128,
        validation_alias=AliasChoices("template_name", "name"),
    )
    description: str | None = Field(default=None, max_length=512)
    embed_tags: list[str] | None = None
    api_base: str | None = Field(default=None, max_length=512)
    api_key: str | None = None
    model_id: str | None = Field(default=None, max_length=128)
    model_provider: str | None = Field(default=None, max_length=64)
    parameters: dict[str, Any] | None = None
    client_config: dict[str, Any] | None = None
    enabled: bool | None = None
    data: dict[str, Any] | None = None


class ExtensionConfigTemplateUpdateRequest(BaseModel):
    template_name: str | None = Field(default=None, max_length=128)
    description: str | None = Field(default=None, max_length=512)
    component: str | None = Field(default=None, max_length=32)
    hook_type: str | None = Field(default=None, max_length=32)
    hook_config: HookConfig | None = None
    custom_config: dict[str, Any] | None = None
    enabled: bool | None = None
    data: dict[str, Any] | None = None


class SkillWhitelistTemplateUpdateRequest(BaseModel):
    template_name: str | None = Field(default=None, max_length=128)
    description: str | None = Field(default=None, max_length=512)
    skill_id: str | None = Field(default=None, max_length=512)
    skill_version: str | None = Field(default=None, max_length=64)
    skill_source: str | None = Field(default=None, max_length=2048)
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
