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
