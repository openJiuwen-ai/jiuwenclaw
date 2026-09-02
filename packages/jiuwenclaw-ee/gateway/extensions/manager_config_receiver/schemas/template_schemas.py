from __future__ import annotations

import re
from typing import Annotated, Any
from urllib.parse import urlparse

from croniter import croniter
from pydantic import (
    AfterValidator,
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)

from .safe_text import SafeTextMixin


# croniter：5 段标准；6 段末尾为秒；7 段为 分 时 日 月 周 秒 年
_CRON_FIELD_COUNTS = frozenset({5, 6, 7})


def is_valid_hook_schedule(value: str) -> bool:
    """用 croniter 校验 hook_config.schedule（含字段取值范围）。"""
    text = value.strip()
    if not text:
        return False
    if len(text.split()) not in _CRON_FIELD_COUNTS:
        return False
    return croniter.is_valid(text)


def normalize_hook_schedule(schedule: str | None, *, required: bool) -> str | None:
    """规范化 schedule；required 时不可为空，有值时须为合法 cron。"""
    text = (schedule or "").strip()
    if not text:
        if required:
            raise ValueError("hook_config.schedule is required when hook_type=schedule")
        return None
    if not is_valid_hook_schedule(text):
        raise ValueError(
            "hook_config.schedule must be a valid cron expression "
            "(5/6/7 fields via croniter, e.g. '0 */5 * * *' or '0 0 */5 * * *')"
        )
    return text


def _validate_http_url(value: str) -> str:
    """校验为合法 http(s) URL（须含主机）。"""
    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("must be a valid http(s) URL")
    return value


SkillSourceUrl = Annotated[
    str,
    Field(min_length=1, max_length=2048),
    AfterValidator(_validate_http_url),
]


class HookConfig(BaseModel):
    """扩展模板 hook_config 结构（与设计文档一致）。"""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    handler: str = Field(..., min_length=1)
    params: dict[str, Any] | None = None
    schedule: str | None = None
    data: dict[str, Any] | None = None


class ModelTemplateUpdateRequest(SafeTextMixin):
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


class ModelTemplateCreateRequest(ModelTemplateUpdateRequest):
    """同步创建：含 ``template_id`` + 业务字段。"""

    template_id: str = Field(..., min_length=1, max_length=100)
    template_name: str = Field(..., min_length=1, max_length=128)
    api_base: str = Field(..., min_length=1, max_length=512)
    api_key: str = Field(..., min_length=1)
    model_id: str = Field(..., min_length=1, max_length=128)
    model_provider: str = Field(..., min_length=1, max_length=64)


class EmbeddingTemplateUpdateRequest(SafeTextMixin):
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


class EmbeddingTemplateCreateRequest(EmbeddingTemplateUpdateRequest):
    template_id: str = Field(..., min_length=1, max_length=100)
    template_name: str = Field(..., min_length=1, max_length=128)
    api_base: str = Field(..., min_length=1, max_length=512)
    api_key: str = Field(..., min_length=1)
    model_id: str = Field(..., min_length=1, max_length=128)
    model_provider: str = Field(..., min_length=1, max_length=64)


class ExtensionConfigTemplateUpdateRequest(SafeTextMixin):
    template_name: str | None = Field(default=None, max_length=128)
    description: str | None = Field(default=None, max_length=512)
    component: str | None = Field(default=None, max_length=32)
    hook_type: str | None = Field(default=None, max_length=32)
    hook_config: HookConfig | None = None
    custom_config: dict[str, Any] | None = None
    enabled: bool | None = None
    data: dict[str, Any] | None = None


class ExtensionConfigTemplateCreateRequest(ExtensionConfigTemplateUpdateRequest):
    template_id: str = Field(..., min_length=1, max_length=100)
    template_name: str = Field(..., min_length=1, max_length=128)
    component: str = Field(..., min_length=1, max_length=32)
    hook_type: str = Field(..., min_length=1, max_length=32)


class SkillWhitelistTemplateUpdateRequest(SafeTextMixin):
    template_name: str | None = Field(default=None, max_length=128)
    description: str | None = Field(default=None, max_length=512)
    skill_id: str | None = Field(default=None, max_length=512)
    skill_version: str | None = Field(default=None, max_length=64)
    skill_source: SkillSourceUrl | None = None
    enabled: bool | None = None
    data: dict[str, Any] | None = None


class SkillWhitelistTemplateCreateRequest(SkillWhitelistTemplateUpdateRequest):
    template_id: str = Field(..., min_length=1, max_length=100)
    template_name: str = Field(..., min_length=1, max_length=128)
    skill_id: str = Field(..., min_length=1, max_length=512)


_VALID_MCP_TRANSPORTS = frozenset({
    "stdio",
    "sse",
    "http",
    "streamable-http",
    "streamable_http",
})


def validate_mcp_entry(entry: dict[str, Any]) -> dict[str, Any]:
    """校验 MCP 模板 ``mcp_entry``；丢弃条目级 ``enabled``（开关只认模板行）。"""
    if not isinstance(entry, dict):
        raise ValueError("mcp_entry must be a JSON object")
    normalized = dict(entry)
    normalized.pop("enabled", None)
    name = str(normalized.get("name", "")).strip()
    if not name:
        raise ValueError("mcp_entry.name is required")
    transport = str(normalized.get("transport", "")).strip().lower()
    if transport not in _VALID_MCP_TRANSPORTS:
        raise ValueError(
            "mcp_entry.transport must be one of: "
            + ", ".join(sorted(_VALID_MCP_TRANSPORTS))
        )
    if transport == "stdio":
        command = str(normalized.get("command", "")).strip()
        if not command:
            raise ValueError("mcp_entry.command is required for stdio transport")
    else:
        url = str(normalized.get("url", "")).strip()
        if not url:
            raise ValueError("mcp_entry.url is required for remote MCP transport")
    normalized["name"] = name
    normalized["transport"] = transport
    return normalized


class McpTemplateUpdateRequest(SafeTextMixin):
    template_name: str | None = Field(default=None, max_length=128)
    description: str | None = Field(default=None, max_length=512)
    mcp_entry: dict[str, Any] | None = None
    enabled: bool | None = None
    data: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _validate_entry(self) -> McpTemplateUpdateRequest:
        if self.mcp_entry is not None:
            validate_mcp_entry(self.mcp_entry)
        return self


class McpTemplateCreateRequest(McpTemplateUpdateRequest):
    template_id: str = Field(..., min_length=1, max_length=100)
    template_name: str = Field(..., min_length=1, max_length=128)
    mcp_entry: dict[str, Any]


# 与 Manager / 库表类型上限一致：integer → 有符号 32 位；autoscale_interval → DECIMAL(10,3)


class AgentTemplateUpdateRequest(SafeTextMixin):
    template_name: str | None = Field(default=None, max_length=128)
    description: str | None = Field(default=None, max_length=512)
    agent_tags: list[str] | None = None
    template_ref: dict[str, list[str]] | None = None
    enabled: bool | None = None
    data: dict[str, Any] | None = None

    @model_validator(mode="before")
    @classmethod
    def _normalize_template_ref_field(cls, data: Any) -> Any:
        if isinstance(data, dict) and data.get("template_ref") is not None:
            from ..infrastructure.utils import normalize_template_ref

            data = dict(data)
            data["template_ref"] = normalize_template_ref(data["template_ref"])
        return data


class AgentTemplateCreateRequest(AgentTemplateUpdateRequest):
    template_id: str = Field(..., min_length=1, max_length=100)
    template_name: str = Field(..., min_length=1, max_length=128)
    template_ref: dict[str, list[str]] = Field(default_factory=dict)
