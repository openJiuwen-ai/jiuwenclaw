"""应用配置 API 请求/响应：日志脱敏规则。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class LogMaskingRuleCreateBody(BaseModel):
    rule_name: str = Field(..., max_length=128)
    description: str | None = Field(default=None, max_length=512)
    pattern: str = Field(..., min_length=1)
    replacement: str | None = Field(default=None, max_length=64)
    priority: int = 0
    enabled: bool = True
    data: dict[str, Any] | None = None


class LogMaskingRuleUpdateBody(BaseModel):
    rule_name: str | None = Field(default=None, max_length=128)
    description: str | None = Field(default=None, max_length=512)
    pattern: str | None = None
    replacement: str | None = Field(default=None, max_length=64)
    priority: int | None = None
    enabled: bool | None = None
    data: dict[str, Any] | None = None


class LogMaskingRuleListQuery(BaseModel):
    enabled: bool | None = None
    source: str | None = Field(
        default=None,
        description="按来源筛选：builtin、custom",
    )
    search: str | None = Field(
        default=None,
        description="搜索规则 ID、名称、描述、匹配正则表达式、替换文本、优先级、来源",
    )
    sort_by: str | None = Field(
        default=None,
        description=(
            "排序字段：rule_name、description、pattern、replacement、priority、updated_at"
        ),
    )
    sort_order: str | None = Field(default=None, description="排序方向：asc、desc")


class LogMaskingRuleOut(BaseModel):
    id: int
    jiuwenclaw_id: str
    rule_id: str
    rule_name: str
    description: str | None
    pattern: str
    replacement: str
    priority: int
    source: str
    enabled: bool
    data: dict[str, Any] | None
    created_at: str | None
    updated_at: str | None
