# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Trigger Engine — 统一触发器引擎.

将现有 Cron + Heartbeat 统一抽象为 Trigger，并新增 Webhook / Event 触发类型。
每个 Trigger 关联一个 Avatar（数字分身），触发时向对应分身发送 prompt。
"""

from jiuwenavatar.gateway.trigger.models import (
    TriggerType,
    TriggerConfig,
    TriggerStatus,
)
from jiuwenavatar.gateway.trigger.engine import TriggerEngine, get_trigger_engine

__all__ = [
    "TriggerType",
    "TriggerConfig",
    "TriggerStatus",
    "TriggerEngine",
    "get_trigger_engine",
]
