# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Trigger data models — 触发器数据模型."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class TriggerType(str, Enum):
    """触发器类型."""

    CRON = "cron"
    HEARTBEAT = "heartbeat"
    WEBHOOK = "webhook"
    EVENT = "event"


class TriggerStatus(str, Enum):
    """触发器状态."""

    ACTIVE = "active"
    PAUSED = "paused"
    ERROR = "error"


class TriggerConfig(BaseModel):
    """触发器配置.

    统一了 Cron / Heartbeat / Webhook / Event 四种触发类型。
    """

    id: str = Field(default_factory=lambda: f"trigger-{uuid.uuid4().hex[:8]}")
    name: str = Field(..., description="触发器名称")
    type: TriggerType = Field(..., description="触发器类型")
    avatar_id: str = Field(..., description="关联的数字分身 ID")
    enabled: bool = Field(True, description="是否启用")
    status: TriggerStatus = Field(TriggerStatus.ACTIVE, description="触发器状态")

    # --- Cron 配置 ---
    cron_expr: str | None = Field(None, description="Cron 表达式 (type=cron 时必填)")
    timezone: str = Field("Asia/Shanghai", description="时区")

    # --- Heartbeat 配置 ---
    interval_seconds: float | None = Field(None, description="心跳间隔秒数 (type=heartbeat 时必填)")
    active_hours: dict | None = Field(None, description="生效时间段 {'start':'HH:MM','end':'HH:MM'}")

    # --- Webhook 配置 ---
    webhook_path: str | None = Field(None, description="Webhook URL 路径 (type=webhook 时必填)")
    webhook_secret: str | None = Field(None, description="Webhook 签名密钥")

    # --- Event 配置 ---
    event_source: str | None = Field(None, description="事件来源 (type=event 时必填)")
    event_type: str | None = Field(None, description="事件类型 (type=event 时必填)")

    # --- 触发行为 ---
    trigger_prompt: str = Field(..., description="触发后发送给分身的 prompt")
    target_channel: str = Field("web", description="推送目标渠道 (web/feishu/dingtalk 等)")
    generate_report: bool = Field(True, description="是否生成执行报告")

    # --- 时间戳 ---
    created_at: str = Field(
        default_factory=lambda: datetime.now().isoformat(),
        description="创建时间",
    )
    updated_at: str = Field(
        default_factory=lambda: datetime.now().isoformat(),
        description="更新时间",
    )

    # --- 最后执行信息 ---
    last_triggered_at: str | None = Field(None, description="最后触发时间")
    last_error: str | None = Field(None, description="最后错误信息")

    # --- 扩展 ---
    extra: dict[str, Any] = Field(default_factory=dict, description="扩展配置")
