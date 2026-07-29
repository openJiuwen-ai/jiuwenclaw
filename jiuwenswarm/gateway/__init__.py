# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Gateway 模块 - 系统枢纽."""

from jiuwenswarm.gateway.routing.agent_client import AgentServerClient, WebSocketAgentServerClient
from jiuwenswarm.gateway.channel_manager import ChannelManager
from jiuwenswarm.gateway.heartbeat import (
    HEARTBEAT_CHANNEL_ID,
    GatewayHeartbeatService,
    HeartbeatConfig,
    IHeartbeat,
)
from jiuwenswarm.gateway.health_check import (
    HEALTH_CHECK_CHANNEL_ID,
    GatewayHealthCheckService,
    HealthCheckConfig,
    IHealthCheck,
)
from jiuwenswarm.gateway.message_handler import MessageHandler

__all__ = [
    "AgentServerClient",
    "WebSocketAgentServerClient",
    "ChannelManager",
    "GatewayHeartbeatService",
    "HEARTBEAT_CHANNEL_ID",
    "HeartbeatConfig",
    "IHeartbeat",
    # 新 health_check 命名(旧探活迁移目标,方案 §2.3)
    "GatewayHealthCheckService",
    "HEALTH_CHECK_CHANNEL_ID",
    "HealthCheckConfig",
    "IHealthCheck",
    "MessageHandler",
]
