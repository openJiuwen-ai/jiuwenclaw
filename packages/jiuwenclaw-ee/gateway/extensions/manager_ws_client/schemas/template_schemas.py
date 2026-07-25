from __future__ import annotations

from typing import Annotated, Any

from pydantic import (
    AfterValidator,
    AliasChoices,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    model_validator,
)
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


# 与 Manager / 库表类型上限一致：integer → 有符号 32 位；autoscale_interval → DECIMAL(10,3)
_SERVICE_INT_MAX = 2_147_483_647
_SERVICE_DECIMAL_MAX = 9_999_999.999

# K8s resource quantity：CPU 如 500m / 2 / 0.5；内存如 512Mi / 2Gi / 128M
_K8S_CPU_RE = re.compile(r"^(?:(?:0|[1-9]\d*)(?:\.\d+)?|\.\d+)m?$")
_K8S_MEMORY_RE = re.compile(
    r"^(?:(?:0|[1-9]\d*)(?:\.\d+)?|\.\d+)(?:(?:[KMGTPE]i)|[kMGTPE]|m)?$"
)


def _normalize_resource_quantity(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _validate_k8s_cpu(value: str | None) -> str | None:
    if value is None:
        return None
    if len(value) > 32:
        raise ValueError("at most 32 characters")
    if not _K8S_CPU_RE.fullmatch(value):
        raise ValueError(
            "must be a valid Kubernetes CPU quantity (e.g. '500m', '2', '0.5')"
        )
    return value


def _validate_k8s_memory(value: str | None) -> str | None:
    if value is None:
        return None
    if len(value) > 32:
        raise ValueError("at most 32 characters")
    if not _K8S_MEMORY_RE.fullmatch(value):
        raise ValueError(
            "must be a valid Kubernetes memory quantity (e.g. '512Mi', '2Gi')"
        )
    return value


K8sCpuQuantity = Annotated[
    str | None,
    BeforeValidator(_normalize_resource_quantity),
    AfterValidator(_validate_k8s_cpu),
]
K8sMemoryQuantity = Annotated[
    str | None,
    BeforeValidator(_normalize_resource_quantity),
    AfterValidator(_validate_k8s_memory),
]


def is_valid_unix_abs_path(value: str) -> bool:
    """校验绝对 Unix 路径：以 / 开头，禁止 \\、空段、. 与 ..。"""
    if not value or len(value) > 512:
        return False
    if "\0" in value or "\\" in value:
        return False
    if not value.startswith("/"):
        return False
    if value == "/":
        return True
    core = value.rstrip("/")
    if not core.startswith("/"):
        return False
    for segment in core[1:].split("/"):
        if not segment or segment in (".", ".."):
            return False
    return True


def _normalize_optional_unix_path(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _validate_optional_unix_path(value: str | None) -> str | None:
    if value is None:
        return None
    if not is_valid_unix_abs_path(value):
        raise ValueError(
            "must be an absolute Unix path (e.g. '/mnt/nfs')"
        )
    return value


OptionalUnixAbsPath = Annotated[
    str | None,
    BeforeValidator(_normalize_optional_unix_path),
    AfterValidator(_validate_optional_unix_path),
]


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
    replicas: int | None = Field(default=None, ge=1, le=_SERVICE_INT_MAX)
    kubeconfig: str | None = Field(default=None, max_length=512)
    agent_runtime: str | None = Field(default=None, max_length=128)
    readiness_initial_delay: int | None = Field(default=None, ge=0, le=_SERVICE_INT_MAX)
    readiness_period: int | None = Field(default=None, ge=1, le=_SERVICE_INT_MAX)
    ready_timeout: int | None = Field(default=None, ge=1, le=_SERVICE_INT_MAX)
    ready_poll_interval: int | None = Field(default=None, ge=1, le=_SERVICE_INT_MAX)
    nfs_server: str | None = Field(default=None, max_length=256)
    nfs_path: OptionalUnixAbsPath = None
    nfs_mount_path: OptionalUnixAbsPath = None
    agent_cpu_request: K8sCpuQuantity = None
    agent_memory_request: K8sMemoryQuantity = None
    agent_cpu_limit: K8sCpuQuantity = None
    agent_memory_limit: K8sMemoryQuantity = None
    jiuwenbox_cpu_request: K8sCpuQuantity = None
    jiuwenbox_memory_request: K8sMemoryQuantity = None
    jiuwenbox_cpu_limit: K8sCpuQuantity = None
    jiuwenbox_memory_limit: K8sMemoryQuantity = None
    min_idle_services: int | None = Field(default=None, ge=0, le=_SERVICE_INT_MAX)
    max_services: int | None = Field(default=None, ge=1, le=_SERVICE_INT_MAX)
    service_concurrency: int | None = Field(default=None, ge=1, le=_SERVICE_INT_MAX)
    service_ttl: int | None = Field(default=None, ge=1, le=_SERVICE_INT_MAX)
    autoscale_interval: float | None = Field(
        default=None, gt=0, le=_SERVICE_DECIMAL_MAX
    )
    message_timeout: int | None = Field(default=None, ge=1, le=_SERVICE_INT_MAX)
    session_concurrency: int | None = Field(default=None, ge=1, le=_SERVICE_INT_MAX)
    session_ttl: int | None = Field(default=None, ge=1, le=_SERVICE_INT_MAX)
    enabled: bool | None = None
    data: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _validate_pool_range(self) -> ServiceConfigTemplateUpdateRequest:
        if (
            self.min_idle_services is not None
            and self.max_services is not None
            and self.min_idle_services > self.max_services
        ):
            raise ValueError("min_idle_services must be <= max_services")
        return self
