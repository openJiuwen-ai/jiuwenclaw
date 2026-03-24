# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Team Card Module

This module defines the identity card for agent teams.
"""

from typing import List, Dict

from pydantic import Field

from openjiuwen.core.common import BaseCard
from openjiuwen.core.single_agent.schema.agent_card import AgentCard


class TeamCard(BaseCard):
    """Team Identity Card

    Immutable identity information for an agent team.
    Inherits from BaseCard: id, name, description

    Attributes:
        agent_cards: List of AgentCards for agents in this team
            (metadata only, not instances)
        topic: Team's primary topic/domain
        version: Team version string
        tags: Optional tags for categorization
    """
    agent_cards: List[AgentCard] = Field(
        default_factory=list,
        description="Agent cards for team members"
    )
    topic: str = Field(
        default='',
        description="Team's primary topic or domain"
    )
    version: str = Field(
        default='1.0.0',
        description="Team version"
    )
    tags: List[str] = Field(
        default_factory=list,
        description="Tags for categorization"
    )


class EventDrivenTeamCard(TeamCard):
    """Event-driven team card with subscription information

    Extends TeamCard with subscription mapping for event-driven
    message routing.

    Attributes:
        subscriptions: Mapping of agent_id to list of subscribed topics
    """
    subscriptions: Dict[str, List[str]] = Field(
        default_factory=dict,
        description="Subscription mapping: {agent_id: [topic1, topic2, ...]}"
    )
