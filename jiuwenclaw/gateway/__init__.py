# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Gateway 模块 - 系统枢纽（惰性加载，避免启动时拉入重型依赖）。"""

import sys

from jiuwenclaw._lazy import install_lazy_attrs

# Note: AgentWebSocketServer is re-exported across packages (its home is
# agentserver.agent_ws_server). Kept here for backward compatibility with
# callers that historically did `from jiuwenclaw.gateway import AgentWebSocketServer`.
_LAZY_ATTRS = {
    "AgentServerClient": (".agent_client", "AgentServerClient"),
    "WebSocketAgentServerClient": (".agent_client", "WebSocketAgentServerClient"),
    "AgentWebSocketServer": ("jiuwenclaw.agentserver.agent_ws_server", "AgentWebSocketServer"),
    "ChannelManager": (".channel_manager", "ChannelManager"),
    "HEARTBEAT_CHANNEL_ID": (".heartbeat", "HEARTBEAT_CHANNEL_ID"),
    "GatewayHeartbeatService": (".heartbeat", "GatewayHeartbeatService"),
    "HeartbeatConfig": (".heartbeat", "HeartbeatConfig"),
    "IHeartbeat": (".heartbeat", "IHeartbeat"),
    "MessageHandler": (".message_handler", "MessageHandler"),
}

install_lazy_attrs(sys.modules[__name__], _LAZY_ATTRS)
