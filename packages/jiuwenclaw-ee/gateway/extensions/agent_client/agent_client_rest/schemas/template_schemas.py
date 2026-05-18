from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ModelTemplateCreateRequest(BaseModel):
    """创建模型模板（字段与 Claw Manager ``model_template`` 表对齐）。"""

    model_config = ConfigDict(str_strip_whitespace=True)

    display_name: str = Field(..., max_length=128, min_length=1)
    description: str | None = Field(default=None, max_length=512)
    model_type: str | list[str]
    model_tags: list[str] | None = None
    api_base: str = Field(..., max_length=512, min_length=1)
    api_key: str = Field(..., min_length=1)
    model_id: str = Field(..., max_length=128, min_length=1)
    model_provider: str = Field(..., max_length=64, min_length=1)
    parameters: dict[str, Any] | None = None
    timeout: int = Field(default=60, ge=1)
    retry_count: int = Field(default=3, ge=0)
    enable_streaming: bool = True
    enable_function_calling: bool = True
    verify_ssl: bool = True
    enabled: bool = True
    data: dict[str, Any] | None = None


class ModelTemplateUpdateRequest(BaseModel):
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
