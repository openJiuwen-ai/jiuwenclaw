# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Multi-Agent Group Runtime Module

Provides the core multi-agent group_runtime infrastructure.

Public API::

    from openjiuwen.core.multi_agent.group_runtime import (
        MessageEnvelope,
        MessageBus,
        MessageBusConfig,
        GroupRuntime,
        RuntimeConfig,
        CommunicableAgent,
    )
"""


# Lazy imports to avoid circular dependencies
def __getattr__(name):
    if name == "MessageEnvelope":
        from openjiuwen.core.multi_agent.group_runtime.envelope import MessageEnvelope
        return MessageEnvelope
    elif name == "MessageBus":
        from openjiuwen.core.multi_agent.group_runtime.message_bus import MessageBus
        return MessageBus
    elif name == "MessageBusConfig":
        from openjiuwen.core.multi_agent.group_runtime.message_bus import MessageBusConfig
        return MessageBusConfig
    elif name == "GroupRuntime":
        from openjiuwen.core.multi_agent.group_runtime.group_runtime import GroupRuntime
        return GroupRuntime
    elif name == "RuntimeConfig":
        from openjiuwen.core.multi_agent.group_runtime.group_runtime import RuntimeConfig
        return RuntimeConfig
    elif name == "CommunicableAgent":
        from openjiuwen.core.multi_agent.group_runtime.communicable_agent import CommunicableAgent
        return CommunicableAgent
    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")

__all__ = [
    "MessageEnvelope",
    "MessageBus",
    "MessageBusConfig",
    "GroupRuntime",
    "RuntimeConfig",
    "CommunicableAgent",
]
