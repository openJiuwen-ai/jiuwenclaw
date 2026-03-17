# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Group Card Module

This module defines the identity card for agent groups.
"""

from typing import List, Dict

from pydantic import Field

from openjiuwen.core.common import BaseCard
from openjiuwen.core.single_agent.schema.agent_card import AgentCard


class GroupCard(BaseCard):
    """Group Identity Card

    Immutable identity information for an agent group.
    Inherits from BaseCard: id, name, description

    Attributes:
        agent_cards: List of AgentCards for agents in this group
            (metadata only, not instances)
        topic: Group's primary topic/domain
        version: Group version string
        tags: Optional tags for categorization
    """
    agent_cards: List[AgentCard] = Field(
        default_factory=list,
        description="Agent cards for group members"
    )
    topic: str = Field(
        default='',
        description="Group's primary topic or domain"
    )
    version: str = Field(
        default='1.0.0',
        description="Group version"
    )
    tags: List[str] = Field(
        default_factory=list,
        description="Tags for categorization"
    )


class EventDrivenGroupCard(GroupCard):
    """Event-driven group card with subscription information

    Extends GroupCard with subscription mapping for event-driven
    message routing.

    Attributes:
        subscriptions: Mapping of agent_id to list of subscribed topics
    """
    subscriptions: Dict[str, List[str]] = Field(
        default_factory=dict,
        description="Subscription mapping: {agent_id: [topic1, topic2, ...]}"
    )
