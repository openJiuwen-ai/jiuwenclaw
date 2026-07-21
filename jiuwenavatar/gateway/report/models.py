# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Report data models — 任务与报告数据模型."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class MissionStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Mission(BaseModel):
    """一次任务执行记录."""

    id: str = Field(default_factory=lambda: f"mission-{uuid.uuid4().hex[:8]}")
    avatar_id: str = Field(..., description="执行分身 ID")
    trigger_id: str | None = Field(None, description="触发该任务的触发器 ID")
    status: MissionStatus = Field(MissionStatus.PENDING, description="任务状态")
    started_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    completed_at: str | None = Field(None)
    prompt: str = Field(..., description="触发时的 prompt")
    result_summary: str | None = Field(None, description="执行结果摘要")
    run_id: str | None = Field(None, description="触发器派发运行 ID")
    session_id: str | None = Field(None, description="触发器派发会话 ID")
    service_id: str | None = Field(None, description="RuntimeManagement 路由键")
    agent_id: str | None = Field(None, description="Pod 内租户隔离键")
    group_id: str | None = Field(None, description="租户/组织 ID")
    owner_user_id: str | None = Field(None, description="分身创建者用户 ID")
    cancel_requested_at: str | None = Field(None, description="取消请求时间")


class ReportSection(BaseModel):
    """报告章节."""

    name: str
    content: str = ""
    items: list[dict[str, Any]] | None = None


class MissionReport(BaseModel):
    """任务执行报告."""

    id: str = Field(default_factory=lambda: f"report-{uuid.uuid4().hex[:8]}")
    mission_id: str = Field(..., description="关联的任务 ID")
    avatar_id: str = Field(..., description="分身 ID")
    group_id: str | None = Field(None, description="租户/组织 ID")
    owner_user_id: str | None = Field(None, description="分身创建者用户 ID")
    avatar_persona: str = Field("", description="Persona 类型")
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())

    title: str = Field("执行报告", description="报告标题")
    summary: str = Field("", description="AI 生成的摘要")
    sections: list[ReportSection] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)
    notified_channels: list[str] = Field(default_factory=list)
