# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Agent Group Module

This module defines the new Card + Config pattern for agent groups.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, AsyncIterator, Optional, Union, List

from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.session.agent_group import Session
from openjiuwen.core.single_agent.legacy import LegacyBaseAgent as BaseAgent
from openjiuwen.core.multi_agent.config import GroupConfig
from openjiuwen.core.multi_agent.schema.group_card import GroupCard
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.logging import logger


class BaseGroup(ABC):
    """Abstract base class for agent groups.

    Design principles (aligned with BaseAgent):
    - Card is required (defines what the Group is)
    - Config is optional (defines how the Group runs)
    - All configuration methods support chaining

    Attributes:
        card: Group card (required, immutable identity)
        config: Group config (optional, mutable runtime settings)
        agents: Dictionary of agents {agent_name: agent_instance}
    """

    def __init__(
        self,
        card: GroupCard,
        config: Optional[GroupConfig] = None
    ):
        """Initialize the agent group.

        Args:
            card: GroupCard defining group identity
            config: Optional GroupConfig for runtime settings
        """
        self.card = card
        self.config = config if config else self._create_default_config()
        self.group_id = card.name
        self.agents: Dict[str, BaseAgent] = {}

    def _create_default_config(self) -> GroupConfig:
        """Create default configuration"""
        return GroupConfig()

    def configure(self, config: GroupConfig) -> 'BaseGroup':
        """Set configuration

        Args:
            config: GroupConfig configuration object

        Returns:
            self (supports chaining)
        """
        self.config = config
        return self

    def add_agent(
        self,
        agent: 'BaseAgent',
        agent_id: Optional[str] = None
    ) -> 'BaseGroup':
        """Register agent to group

        Args:
            agent: Agent instance (must have card.name)
            agent_id: Optional custom ID (defaults to agent.card.name)

        Returns:
            self (supports chaining)

        Raises:
            BaseError: If agent ID already exists or max reached

        Example:
            # New pattern (recommended)
            group.add_agent(agent1).add_agent(agent2)

            # With custom ID
            group.add_agent(agent1, agent_id="custom_id")
        """
        if agent_id is None:
            if hasattr(agent, 'card') and hasattr(agent.card, 'name'):
                agent_id = agent.card.name
            else:
                raise build_error(
                    StatusCode.AGENT_GROUP_ADD_RUNTIME_ERROR,
                    error_msg="Agent must have card.name or provide agent_id"
                )

        if agent_id in self.agents:
            raise build_error(
                StatusCode.AGENT_GROUP_ADD_RUNTIME_ERROR,
                error_msg=f"Agent ID '{agent_id}' already exists"
            )

        if self.get_agent_count() >= self.config.max_agents:
            raise build_error(
                StatusCode.AGENT_GROUP_ADD_RUNTIME_ERROR,
                error_msg=f"Agent count exceeds max_agents "
                           f"({self.config.max_agents})"
            )

        self.agents[agent_id] = agent

        if hasattr(agent, 'card'):
            self.card.agent_cards.append(agent.card)

        if hasattr(agent, 'controller') and agent.controller is not None:
            if hasattr(agent.controller, 'set_group'):
                agent.controller.set_group(self)
                logger.debug(
                    f"BaseGroup: Auto-injected group reference to "
                    f"agent '{agent_id}' controller"
                )

        return self

    def remove_agent(
        self,
        agent_id: Union[str, 'BaseAgent']
    ) -> 'BaseGroup':
        """Remove agent from group

        Args:
            agent_id: Agent ID string or agent instance

        Returns:
            self (supports chaining)
        """
        if isinstance(agent_id, BaseAgent):
            if hasattr(agent_id, 'card') and hasattr(agent_id.card, 'name'):
                agent_id = agent_id.card.name
            else:
                logger.warning("Cannot determine agent ID from instance")
                return self

        if agent_id in self.agents:
            agent = self.agents.pop(agent_id)
            if hasattr(agent, 'card'):
                self.card.agent_cards = [
                    c for c in self.card.agent_cards
                    if c.name != agent_id
                ]
            logger.debug(f"BaseGroup: Removed agent '{agent_id}'")

        return self

    def get_agent(self, agent_id: str) -> Optional['BaseAgent']:
        """Get agent by ID

        Args:
            agent_id: Agent ID

        Returns:
            Agent instance or None if not found
        """
        return self.agents.get(agent_id)

    def get_agent_count(self) -> int:
        """Get the number of agents in the group

        Returns:
            Number of agents
        """
        return len(self.agents)

    def list_agents(self) -> List[str]:
        """List all agent IDs

        Returns:
            List of agent IDs
        """
        return list(self.agents.keys())

    @abstractmethod
    async def invoke(
        self,
        message,
        session: Optional[Session] = None
    ) -> Any:
        """Execute synchronous operation on the agent group.

        Args:
            message: Message object or dict
            session: Session for agent group instance

        Returns:
            The collective output from the agent group
        """
        raise NotImplementedError(
            f"invoke method must be implemented by {self.__class__.__name__}"
        )

    @abstractmethod
    async def stream(
        self,
        message,
        session: Optional[Session] = None
    ) -> AsyncIterator[Any]:
        """Execute streaming operation on the agent group.

        Args:
            message: Message object or dict
            session: Session for agent group instance

        Yields:
            Streaming output from the agent group
        """
        raise NotImplementedError(
            f"stream method must be implemented by {self.__class__.__name__}"
        )


