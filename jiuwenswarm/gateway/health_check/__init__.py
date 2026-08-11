# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""HealthCheck 模块 — 旧 Heartbeat 探活的迁移目标(方案 §2.3 命名铁律)。

旧 ``gateway/heartbeat/heartbeat.py`` 的探活逻辑迁移到本命名空间,与新 Heartbeat
任务(线程续跑,``gateway/heartbeat/`` 下 models/store/scheduler/controller)严格区分。
旧 ``heartbeat.py`` 保留为 thin shim 再导出本模块符号,保持既有 import 不崩。
"""

from jiuwenswarm.gateway.health_check.health_check import (
    HEALTH_CHECK_CHANNEL_ID,
    HEALTH_CHECK_OK,
    HEALTH_CHECK_PROMPT,
    GatewayHealthCheckService,
    HealthCheckConfig,
    IHealthCheck,
    normalize_active_hours,
)

__all__ = [
    "HEALTH_CHECK_CHANNEL_ID",
    "HEALTH_CHECK_OK",
    "HEALTH_CHECK_PROMPT",
    "GatewayHealthCheckService",
    "HealthCheckConfig",
    "IHealthCheck",
    "normalize_active_hours",
]
