# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from typing import Any, Literal

from pydantic import BaseModel, Field

from jiuwenswarm.extensions.dolores.server.runtime.context_engine.base import ContextStats


CONTEXT_COMPRESSION_STATE_TYPE = "context.compression_state"


class ContextCompressionMetric(BaseModel):
    time: str | None = Field(default=None)
    messages: int = Field(default=0)
    tokens: int = Field(default=0)
    context_percent: int | None = Field(default=None)


class ContextCompressionSaved(BaseModel):
    messages: int = Field(default=0)
    tokens: int = Field(default=0)
    percent: float = Field(default=0)


class ContextCompressionUsage(BaseModel):
    calls: int = Field(default=0)
    input_tokens: int = Field(default=0)
    output_tokens: int = Field(default=0)
    total_tokens: int = Field(default=0)
    cache_tokens: int = Field(default=0)
    input_cost: float = Field(default=0)
    output_cost: float = Field(default=0)
    total_cost: float = Field(default=0)
    model_name: str = Field(default="")
    details: list[dict[str, Any]] = Field(default_factory=list)


class ContextCompressionState(BaseModel):
    type: str = Field(default=CONTEXT_COMPRESSION_STATE_TYPE)
    operation_id: str
    status: Literal["started", "completed", "noop", "skipped", "failed"]
    phase: Literal["add_messages", "get_context_window", "active_compress"]
    processor: str = Field(default="")
    model: str = Field(default="")
    before: ContextCompressionMetric
    after: ContextCompressionMetric | None = Field(default=None)
    statistic: ContextStats = Field(default_factory=ContextStats)
    saved: ContextCompressionSaved | None = Field(default=None)
    compression_usage: ContextCompressionUsage | None = Field(default=None)
    duration_ms: int | None = Field(default=None)
    context_max: int | None = Field(default=None)
    summary: str = Field(default="")
    compact_summary: str = Field(default="")
    error: str | None = Field(default=None)
