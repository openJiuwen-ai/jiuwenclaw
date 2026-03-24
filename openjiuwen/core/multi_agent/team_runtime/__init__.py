# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Multi-Agent Team Runtime Module

Provides the core multi-agent team_runtime infrastructure.

Public API::

    from openjiuwen.core.multi_agent.team_runtime import (
        MessageEnvelope,
        MessageBus,
        MessageBusConfig,
        TeamRuntime,
        RuntimeConfig,
        CommunicableAgent,
    )
"""


# Lazy imports to avoid circular dependencies
def __getattr__(name):
    if name == "MessageEnvelope":
        from openjiuwen.core.multi_agent.team_runtime.envelope import MessageEnvelope
        return MessageEnvelope
    elif name == "MessageBus":
        from openjiuwen.core.multi_agent.team_runtime.message_bus import MessageBus
        return MessageBus
    elif name == "MessageBusConfig":
        from openjiuwen.core.multi_agent.team_runtime.message_bus import MessageBusConfig
        return MessageBusConfig
    elif name == "TeamRuntime":
        from openjiuwen.core.multi_agent.team_runtime.team_runtime import TeamRuntime
        return TeamRuntime
    elif name == "RuntimeConfig":
        from openjiuwen.core.multi_agent.team_runtime.team_runtime import RuntimeConfig
        return RuntimeConfig
    elif name == "CommunicableAgent":
        from openjiuwen.core.multi_agent.team_runtime.communicable_agent import CommunicableAgent
        return CommunicableAgent
    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")

__all__ = [
    "MessageEnvelope",
    "MessageBus",
    "MessageBusConfig",
    "TeamRuntime",
    "RuntimeConfig",
    "CommunicableAgent",
]
