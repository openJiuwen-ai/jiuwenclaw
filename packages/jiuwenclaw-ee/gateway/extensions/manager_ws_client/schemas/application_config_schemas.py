# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ChannelConfigCreateRequest(BaseModel):
    """创建渠道配置（WS ``channel_config.create`` 的 ``channel`` 对象）。"""

    model_config = ConfigDict(str_strip_whitespace=True)

    channel_id: str = Field(..., min_length=1)
    channel_name: str = Field(..., min_length=1)
    channel_type: str = Field(..., min_length=1)
    bot_id: str = Field(..., min_length=1)
    config: dict[str, Any] | None = None
    status: Literal["active", "inactive"] = "active"


class LogMaskingRuleCreateRequest(BaseModel):
    """创建日志脱敏规则（WS ``log_masking_rule.create`` 的 ``rule`` 对象）。"""

    model_config = ConfigDict(str_strip_whitespace=True)

    jiuwenclaw_id: str | None = Field(default=None, max_length=64)
    rule_id: str = Field(..., min_length=1, max_length=64)
    rule_name: str = Field(..., min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=512)
    pattern: str = Field(..., min_length=1)
    replacement: str | None = Field(default=None, max_length=64)
    priority: int = 0
    source: str = Field(default="custom", max_length=16)
    enabled: bool = True
    data: dict[str, Any] | None = None


class LogMaskingRuleUpdateRequest(BaseModel):
    """Gateway WS ``log_masking_rule.update`` 的 ``updates`` 对象。"""

    model_config = ConfigDict(str_strip_whitespace=True)

    rule_name: str | None = Field(default=None, max_length=128)
    description: str | None = Field(default=None, max_length=512)
    pattern: str | None = None
    replacement: str | None = Field(default=None, max_length=64)
    priority: int | None = None
    source: str | None = Field(default=None, max_length=16)
    enabled: bool | None = None
    data: dict[str, Any] | None = None
