# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Multi-Agent Module

Provides the Card + Config pattern for agent groups.

Public API::

    from openjiuwen.core.multi_agent import BaseGroup, GroupCard, GroupConfig

Legacy API (deprecated)::

    from openjiuwen.core.multi_agent.legacy import AgentGroupConfig, ControllerGroup
"""

from openjiuwen.core.session.agent_group import Session, create_agent_group_session


# Lazy imports to avoid circular dependencies
def __getattr__(name):
    if name == "BaseGroup":
        from openjiuwen.core.multi_agent.group import BaseGroup
        return BaseGroup
    elif name == "GroupConfig":
        from openjiuwen.core.multi_agent.config import GroupConfig
        return GroupConfig
    elif name == "GroupCard":
        from openjiuwen.core.multi_agent.schema.group_card import GroupCard
        return GroupCard
    elif name == "EventDrivenGroupCard":
        from openjiuwen.core.multi_agent.schema.group_card import EventDrivenGroupCard
        return EventDrivenGroupCard
    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")


__all__ = [
    "GroupCard",
    "EventDrivenGroupCard",
    "GroupConfig",
    "Session",
    "BaseGroup",
    "create_agent_group_session"
]
