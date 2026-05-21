"""模板 API 请求/响应模型：model_template、extension_config_template。"""

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
    verify_ssl: bool = True
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
