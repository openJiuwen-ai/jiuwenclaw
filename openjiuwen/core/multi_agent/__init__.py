# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Multi-Agent Module

This module provides the new Card + Config pattern for agent groups.

New API (recommended):
    from openjiuwen.core.multi_agent import (
        GroupCard,
        GroupConfig,
        BaseGroup
    )

Legacy API (deprecated, for backward compatibility only):
    from openjiuwen.core.multi_agent.legacy import (
        AgentGroupConfig,
        BaseGroup,
        ControllerGroup,
        GroupCard
    )
"""

from openjiuwen.core.multi_agent.group import (
    BaseGroup
)
from openjiuwen.core.multi_agent.config import GroupConfig
from openjiuwen.core.multi_agent.schema.group_card import (
    GroupCard,
    EventDrivenGroupCard
)

from openjiuwen.core.session.agent_group import (
    Session,
    create_agent_group_session,
)

__all__ = [
    "GroupCard",
    "EventDrivenGroupCard",
    "GroupConfig",
    "Session",
    "BaseGroup",
    "create_agent_group_session"
]
