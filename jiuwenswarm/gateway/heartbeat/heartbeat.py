# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""旧 Heartbeat 探活模块 — 已迁移到 ``gateway/health_check/``(方案 §2.3 命名铁律)。

旧 ``HEARTBEAT.md`` 驱动的全局周期探活本质是健康检查,已整体改名到
``health_check.*`` 命名空间。本文件保留为 thin shim,再导出 health_check 模块符号,
保持既有 ``from jiuwenswarm.gateway.heartbeat.heartbeat import GatewayHeartbeatService``
等 import 不崩;新代码应直接 ``from jiuwenswarm.gateway.health_check import ...``。

新 Heartbeat 任务(线程续跑)与本模块无关,见 ``gateway/heartbeat/{models,store,
scheduler,controller,session_resolver}.py``。

注意:这里 ``EventType.HEARTBEAT_RELAY`` 等 RPC 名仍用旧值,待 IM 渠道全量切换后删除。
"""

from __future__ import annotations

from jiuwenswarm.gateway.health_check.health_check import (
    HEALTH_CHECK_CHANNEL_ID as _HCC_CHANNEL,
    HEALTH_CHECK_OK as _HCC_OK,
    HEALTH_CHECK_PROMPT as _HCC_PROMPT,
    GatewayHealthCheckService,
    HealthCheckConfig,
    IHealthCheck,
    normalize_active_hours,
)

# 旧名兼容:对外仍暴露 heartbeat.* 命名的常量/类,内部指向 health_check 实现。
# 探活 channel 仍用旧 __heartbeat__ 值,保持 AgentServer 侧识别不变(仅模块/类改名)。
HEARTBEAT_CHANNEL_ID = "__heartbeat__"
HEARTBEAT_OK = "HEARTBEAT_OK"
HEARTBEAT_PROMPT = _HCC_PROMPT

# HeartbeatConfig:旧名,指向 HealthCheckConfig(同实现,旧字段语义不变)。
HeartbeatConfig = HealthCheckConfig
IHeartbeat = IHealthCheck

# 旧名 GatewayHeartbeatService 指向 GatewayHealthCheckService。
GatewayHeartbeatService = GatewayHealthCheckService

__all__ = [
    "HEARTBEAT_CHANNEL_ID",
    "HEARTBEAT_OK",
    "HEARTBEAT_PROMPT",
    "HeartbeatConfig",
    "IHeartbeat",
    "GatewayHeartbeatService",
    "normalize_active_hours",
    # 新名也导出,便于渐进迁移调用方。
    "HealthCheckConfig",
    "IHealthCheck",
    "GatewayHealthCheckService",
]
