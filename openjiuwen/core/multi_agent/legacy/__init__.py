# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Legacy Multi-Agent Module

.. deprecated::
    This module is deprecated and will be removed in a future version.
    Please migrate to the new Card + Config pattern from the parent module:

    from openjiuwen.core.multi_agent import (
        GroupCard,
        GroupConfig,
        BaseGroup,
        AgentGroupSession
    )

This module contains the legacy interfaces for backward compatibility.
New code should use the new Card + Config pattern from the parent module.

Usage (deprecated):
    from openjiuwen.core.multi_agent.legacy import (
        AgentGroupConfig,
        BaseGroup,
        ControllerGroup,
        AgentGroupSession,
        GroupCard,
        BaseGroupController,
        DefaultGroupController
    )
"""

import warnings

from openjiuwen.core.multi_agent.legacy.config import AgentGroupConfig
from openjiuwen.core.multi_agent.legacy.agent_group import (
    AgentGroupSession,
    BaseGroup,
    ControllerGroup
)
from openjiuwen.core.multi_agent.legacy.schema.group_card import (
    GroupCard,
    EventDrivenGroupCard
)
from openjiuwen.core.multi_agent.legacy.group_controller import (
    BaseGroupController,
    DefaultGroupController
)

# 发出废弃警告
warnings.warn(
    "openjiuwen.core.multi_agent.legacy module is deprecated and will be "
    "removed in a future version. Please migrate to the new Card + Config "
    "pattern: from openjiuwen.core.multi_agent import GroupCard, GroupConfig, "
    "BaseGroup, AgentGroupSession",
    DeprecationWarning,
    stacklevel=2
)

__all__ = [
    "AgentGroupConfig",
    "AgentGroupSession",
    "BaseGroup",
    "ControllerGroup",
    "GroupCard",
    "EventDrivenGroupCard",
    "BaseGroupController",
    "DefaultGroupController"
]
