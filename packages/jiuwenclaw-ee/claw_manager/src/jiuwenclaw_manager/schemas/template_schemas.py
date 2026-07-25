"""模板 API 请求/响应模型。"""

from __future__ import annotations

from typing import Annotated, Any, Literal
from urllib.parse import urlparse
import re

from pydantic import AfterValidator, BaseModel, BeforeValidator, ConfigDict, Field, model_validator

from jiuwenclaw_manager.schemas.safe_text import SafeTextMixin

ModelTypeLiteral = Literal["default", "video", "audio", "vision"]
ExtensionComponentLiteral = Literal["gateway", "agent_server"]
ExtensionHookTypeLiteral = Literal["pre_request", "post_request", "error", "schedule"]
ImagePullPolicyLiteral = Literal["Always", "IfNotPresent", "Never"]
TemplateIdPath = Annotated[str, Field(min_length=1, max_length=100)]

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


def _validate_http_url(value: str) -> str:
    """校验为合法 http(s) URL（须含主机）。"""
    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("must be a valid http(s) URL")
    return value


ApiBaseUrl = Annotated[
    str,
    Field(min_length=1, max_length=512),
    AfterValidator(_validate_http_url),
]
SkillSourceUrl = Annotated[
    str,
    Field(min_length=1, max_length=2048),
    AfterValidator(_validate_http_url),
]


class ModelTemplateCreateBody(SafeTextMixin):
    model_config = ConfigDict(str_strip_whitespace=True)

    template_name: str = Field(..., min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=512)
    model_type: list[ModelTypeLiteral] = Field(default_factory=list)
    model_tags: list[str] | None = None
    api_base: ApiBaseUrl
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


class ModelTemplateUpdateBody(SafeTextMixin):
    model_config = ConfigDict(str_strip_whitespace=True)

    template_name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=512)
    model_type: list[ModelTypeLiteral] | None = None
    model_tags: list[str] | None = None
    api_base: ApiBaseUrl | None = None
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
    model_type: list[str]
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


class ModelTemplateListQuery(BaseModel):
    """模型模板列表查询参数。"""

    page: int = Field(1, ge=1)
    page_size: int = Field(20, ge=1, le=200)
    enabled: bool | None = None
    model_type: ModelTypeLiteral | None = Field(
        default=None,
        description="按模型类型筛选，如 default / video / audio / vision",
    )
    model_provider: str | None = Field(
        default=None,
        max_length=64,
        description="按 provider 筛选，大小写不敏感",
    )
    search: str | None = Field(
        default=None,
        max_length=256,
        description=(
            "按 template_id、template_name、description、provider、"
            "模型 ID、模型类型、API base 模糊搜索"
        ),
    )
    sort_by: str | None = Field(
        default=None,
        description=(
            "排序字段：template_name、description、model_provider、model_id、"
            "model_type、api_base、updated_at"
        ),
    )
    sort_order: str | None = Field(default=None, description="排序方向：asc、desc")


class EmbeddingTemplateCreateBody(SafeTextMixin):
    model_config = ConfigDict(str_strip_whitespace=True)

    template_name: str = Field(..., min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=512)
    embed_tags: list[str] | None = None
    api_base: ApiBaseUrl
    api_key: str = Field(..., min_length=1)
    model_id: str = Field(..., min_length=1, max_length=128)
    model_provider: str = Field(..., min_length=1, max_length=64)
    parameters: dict[str, Any] | None = None
    client_config: dict[str, Any] | None = Field(
        default_factory=lambda: {
            "timeout": 60,
            "retry_count": 3,
            "verify_ssl": True,
        }
    )
    enabled: bool = True
    data: dict[str, Any] | None = None


class EmbeddingTemplateUpdateBody(SafeTextMixin):
    model_config = ConfigDict(str_strip_whitespace=True)

    template_name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=512)
    embed_tags: list[str] | None = None
    api_base: ApiBaseUrl | None = None
    api_key: str | None = Field(default=None, min_length=1)
    model_id: str | None = Field(default=None, min_length=1, max_length=128)
    model_provider: str | None = Field(default=None, min_length=1, max_length=64)
    parameters: dict[str, Any] | None = None
    client_config: dict[str, Any] | None = None
    enabled: bool | None = None
    data: dict[str, Any] | None = None


class EmbeddingTemplateOut(BaseModel):
    id: int
    template_id: str
    template_name: str
    description: str | None
    embed_tags: list[str] | None
    api_base: str
    api_key: str
    model_id: str
    model_provider: str
    parameters: dict[str, Any] | None
    client_config: dict[str, Any] | None
    enabled: bool
    data: dict[str, Any] | None
    created_at: str | None
    updated_at: str | None


class EmbeddingTemplateListQuery(BaseModel):
    page: int = Field(1, ge=1)
    page_size: int = Field(20, ge=1, le=200)
    enabled: bool | None = None
    model_provider: str | None = Field(default=None, max_length=64)
    search: str | None = Field(default=None, max_length=256)
    sort_by: str | None = Field(
        default=None,
        description="排序字段：template_name、description、model_provider、model_id、api_base、updated_at",
    )
    sort_order: str | None = Field(default=None, description="排序方向：asc、desc")


class HookConfig(BaseModel):
    """扩展模板 hook_config 结构（与设计文档一致）。"""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    handler: str = Field(..., min_length=1, description="钩子实现路径或模块标识")
    params: dict[str, Any] | None = Field(default=None, description="传入钩子函数的静态参数")
    schedule: str | None = Field(
        default=None,
        description="仅 hook_type=schedule 时必填；cron 表达式（5/6/7 段）",
    )
    data: dict[str, Any] | None = Field(default=None, description="单条钩子扩展配置")


class ExtensionConfigTemplateCreateBody(SafeTextMixin):
    model_config = ConfigDict(str_strip_whitespace=True)

    template_name: str = Field(..., min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=512)
    component: ExtensionComponentLiteral
    hook_type: ExtensionHookTypeLiteral
    hook_config: HookConfig
    custom_config: dict[str, Any] | None = None
    enabled: bool = True
    data: dict[str, Any] | None = None


class ExtensionConfigTemplateUpdateBody(SafeTextMixin):
    model_config = ConfigDict(str_strip_whitespace=True)

    template_name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=512)
    component: ExtensionComponentLiteral | None = None
    hook_type: ExtensionHookTypeLiteral | None = None
    hook_config: HookConfig | None = None
    custom_config: dict[str, Any] | None = None
    enabled: bool | None = None
    data: dict[str, Any] | None = None


class ExtensionConfigTemplateListQuery(BaseModel):
    """扩展配置模板列表查询参数。"""

    page: int = Field(1, ge=1)
    page_size: int = Field(20, ge=1, le=200)
    enabled: bool | None = None
    component: ExtensionComponentLiteral | None = Field(
        default=None,
        description="目标组件：gateway / agent_server",
    )
    hook_type: ExtensionHookTypeLiteral | None = Field(
        default=None,
        description="钩子类型：pre_request / post_request / error / schedule",
    )
    search: str | None = Field(
        default=None,
        description="按 template_id、template_name、description、component、hook_type 模糊搜索",
    )
    sort_by: str | None = Field(
        default=None,
        description="排序字段：template_name、description、component、hook_type、updated_at",
    )
    sort_order: str | None = Field(default=None, description="排序方向：asc、desc")


class ExtensionConfigTemplateOut(BaseModel):
    id: int
    template_id: str
    template_name: str
    description: str | None
    component: str
    hook_type: str
    hook_config: HookConfig
    custom_config: dict[str, Any] | None
    enabled: bool
    data: dict[str, Any] | None
    created_at: str | None
    updated_at: str | None


class SkillWhitelistTemplateCreateBody(SafeTextMixin):
    model_config = ConfigDict(str_strip_whitespace=True)

    template_name: str = Field(..., min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=512)
    skill_id: str = Field(..., min_length=1, max_length=512)
    skill_version: str = Field(..., min_length=1, max_length=64)
    skill_source: SkillSourceUrl
    enabled: bool = True
    data: dict[str, Any] | None = None


class SkillWhitelistTemplateUpdateBody(SafeTextMixin):
    model_config = ConfigDict(str_strip_whitespace=True)

    template_name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=512)
    skill_id: str | None = Field(default=None, min_length=1, max_length=512)
    skill_version: str | None = Field(default=None, min_length=1, max_length=64)
    skill_source: SkillSourceUrl | None = None
    enabled: bool | None = None
    data: dict[str, Any] | None = None


class SkillWhitelistTemplateListQuery(BaseModel):
    page: int = Field(1, ge=1)
    page_size: int = Field(20, ge=1, le=200)
    enabled: bool | None = None
    skill_id: str | None = Field(default=None, max_length=512)
    skill_source: str | None = Field(default=None, max_length=2048)
    search: str | None = Field(
        default=None,
        description="按 template_id、template_name、description、skill_source、skill_id、skill_version 模糊搜索",
    )
    sort_by: str | None = Field(
        default=None,
        description="排序字段：template_name、description、skill_source、skill_id、skill_version、updated_at",
    )
    sort_order: str | None = Field(default=None, description="排序方向：asc、desc")


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


# 与库表类型上限一致：integer → 有符号 32 位；autoscale_interval → DECIMAL(10,3)
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


def _normalize_required_nfs_path(value: Any) -> str:
    if value is None:
        return "/"
    text = str(value).strip()
    return text or "/"


def _validate_required_nfs_path(value: str) -> str:
    if not is_valid_unix_abs_path(value):
        raise ValueError(
            "must be an absolute Unix path (e.g. '/', '/data/nfs')"
        )
    return value


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


NfsExportPath = Annotated[
    str,
    BeforeValidator(_normalize_required_nfs_path),
    AfterValidator(_validate_required_nfs_path),
]
OptionalUnixAbsPath = Annotated[
    str | None,
    BeforeValidator(_normalize_optional_unix_path),
    AfterValidator(_validate_optional_unix_path),
]


class ServiceConfigTemplateCreateBody(SafeTextMixin):
    model_config = ConfigDict(str_strip_whitespace=True)

    template_name: str = Field(..., min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=512)
    agent_image: str = Field(..., max_length=512)
    namespace: str = Field(default="default", max_length=128)
    pod_name: str | None = Field(default=None, max_length=128)
    container_name: str = Field(..., max_length=128)
    container_port: int = Field(..., ge=1, le=65535)
    port_name: str = Field(default="http", max_length=64)
    image_pull_policy: ImagePullPolicyLiteral = Field(default="IfNotPresent")
    replicas: int = Field(default=1, ge=1, le=_SERVICE_INT_MAX)
    kubeconfig: str | None = Field(default=None, max_length=512)
    agent_runtime: str | None = Field(default=None, max_length=128)
    readiness_initial_delay: int = Field(default=10, ge=0, le=_SERVICE_INT_MAX)
    readiness_period: int = Field(default=5, ge=1, le=_SERVICE_INT_MAX)
    ready_timeout: int = Field(default=300, ge=1, le=_SERVICE_INT_MAX)
    ready_poll_interval: int = Field(default=5, ge=1, le=_SERVICE_INT_MAX)
    nfs_server: str | None = Field(default=None, max_length=256)
    nfs_path: NfsExportPath = "/"
    nfs_mount_path: OptionalUnixAbsPath = None
    agent_cpu_request: K8sCpuQuantity = None
    agent_memory_request: K8sMemoryQuantity = None
    agent_cpu_limit: K8sCpuQuantity = None
    agent_memory_limit: K8sMemoryQuantity = None
    jiuwenbox_cpu_request: K8sCpuQuantity = None
    jiuwenbox_memory_request: K8sMemoryQuantity = None
    jiuwenbox_cpu_limit: K8sCpuQuantity = None
    jiuwenbox_memory_limit: K8sMemoryQuantity = None
    min_idle_services: int = Field(default=1, ge=0, le=_SERVICE_INT_MAX)
    max_services: int = Field(default=20, ge=1, le=_SERVICE_INT_MAX)
    service_concurrency: int = Field(default=10, ge=1, le=_SERVICE_INT_MAX)
    service_ttl: int = Field(default=180, ge=1, le=_SERVICE_INT_MAX)
    autoscale_interval: float = Field(default=5, gt=0, le=_SERVICE_DECIMAL_MAX)
    message_timeout: int = Field(default=60, ge=1, le=_SERVICE_INT_MAX)
    session_concurrency: int = Field(default=10, ge=1, le=_SERVICE_INT_MAX)
    session_ttl: int = Field(default=60, ge=1, le=_SERVICE_INT_MAX)
    enabled: bool = True
    data: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _validate_pool_range(self) -> ServiceConfigTemplateCreateBody:
        if self.min_idle_services > self.max_services:
            raise ValueError("min_idle_services must be <= max_services")
        return self


class ServiceConfigTemplateUpdateBody(SafeTextMixin):
    model_config = ConfigDict(str_strip_whitespace=True)

    template_name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=512)
    agent_image: str | None = Field(default=None, max_length=512)
    namespace: str | None = Field(default=None, max_length=128)
    pod_name: str | None = Field(default=None, max_length=128)
    container_name: str | None = Field(default=None, max_length=128)
    container_port: int | None = Field(default=None, ge=1, le=65535)
    port_name: str | None = Field(default=None, max_length=64)
    image_pull_policy: ImagePullPolicyLiteral | None = None
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
    def _validate_pool_range(self) -> ServiceConfigTemplateUpdateBody:
        if (
            self.min_idle_services is not None
            and self.max_services is not None
            and self.min_idle_services > self.max_services
        ):
            raise ValueError("min_idle_services must be <= max_services")
        return self


class ServiceConfigTemplateListQuery(BaseModel):
    page: int = Field(1, ge=1)
    page_size: int = Field(20, ge=1, le=200)
    enabled: bool | None = None
    namespace: str | None = Field(default=None, max_length=128)
    search: str | None = Field(default=None, max_length=256)
    sort_by: str | None = Field(
        default=None,
        description="排序字段：template_name、description、agent_image、updated_at",
    )
    sort_order: str | None = Field(default=None, description="排序方向：asc、desc")


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
