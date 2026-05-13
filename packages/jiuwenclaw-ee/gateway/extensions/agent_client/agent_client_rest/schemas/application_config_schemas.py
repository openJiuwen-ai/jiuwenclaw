from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ModelConfigCreateRequest(BaseModel):
    """创建模型配置；四类主字段在去首尾空白后不得为空。"""

    model_config = ConfigDict(str_strip_whitespace=True)

    model_name: str = Field(..., min_length=1)
    model_type: str = Field(..., min_length=1)
    api_endpoint: str = Field(..., min_length=1)
    api_key_ref: str = Field(..., min_length=1)
    parameters: dict[str, Any] | None = None
    rate_limit: dict[str, Any] | None = None
    enabled: bool = True
    data: dict[str, Any] | None = None


class ModelConfigUpdateRequest(BaseModel):
    model_name: str | None = None
    model_type: str | None = None
    api_endpoint: str | None = None
    api_key_ref: str | None = None
    parameters: dict[str, Any] | None = None
    rate_limit: dict[str, Any] | None = None
    enabled: bool | None = None
    data: dict[str, Any] | None = None


class ChannelConfigCreateRequest(BaseModel):
    """创建渠道配置；主字段去首尾空白后非空，``status`` 仅能为 active / inactive。"""

    model_config = ConfigDict(str_strip_whitespace=True)

    channel_id: str = Field(..., min_length=1)
    channel_name: str = Field(..., min_length=1)
    channel_type: str = Field(..., min_length=1)
    bot_id: str = Field(..., min_length=1)
    config: dict[str, Any] | None = None
    status: Literal["active", "inactive"] = "active"


class ChannelConfigDeactivateRequest(BaseModel):
    graceful: bool = True
    timeout: int = 30
