# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Agent Team Module

Defines the Card + Config pattern for agent teams.
BaseTeam delegates all agent management to TeamRuntime and
exposes send/publish methods for agent communication.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, AsyncIterator, Optional, Union, List, TYPE_CHECKING

from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.multi_agent.team_runtime.communicable_agent import CommunicableAgent
from openjiuwen.core.multi_agent.team_runtime.message_bus import MessageBusConfig
from openjiuwen.core.multi_agent.team_runtime.team_runtime import TeamRuntime, RuntimeConfig
from openjiuwen.core.session.agent_team import Session
from openjiuwen.core.single_agent.legacy import LegacyBaseAgent as BaseAgent
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.core.multi_agent.config import TeamConfig
from openjiuwen.core.multi_agent.schema.team_card import TeamCard
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.logging import logger

if TYPE_CHECKING:
    from openjiuwen.core.runner.resources_manager.base import AgentProvider


class BaseTeam(ABC):
    """Abstract base class for agent teams.

    - ``card`` is required and defines the team's identity
    - ``config`` is optional and controls runtime behaviour
    - All agent registration is delegated to ``self.runtime``
    - All configuration methods support chaining

    Attributes:
        card: Team card (required, immutable identity)
        config: Team config (optional, mutable runtime settings)
        runtime: TeamRuntime instance (manages agents)
    """

    def __init__(
        self,
        card: TeamCard,
        config: Optional[TeamConfig] = None,
        runtime: Optional[TeamRuntime] = None
    ):
        """Initialize the agent team.

        Args:
            card: TeamCard defining team identity
            config: Optional TeamConfig for runtime settings
            runtime: Optional TeamRuntime (created if not provided)
        """
        self.card = card
        self.config = config if config else self._create_default_config()
        self.team_id = card.name
        self.runtime = runtime or self._create_default_runtime()

    def _create_default_config(self) -> TeamConfig:
        """Create default configuration"""
        return TeamConfig()

    def _create_default_runtime(self) -> TeamRuntime:
        """Create default runtime with team_id"""
        return TeamRuntime(
            config=RuntimeConfig(
                team_id=self.card.id,
                message_bus=MessageBusConfig(
                    max_queue_size=self.config.max_concurrent_messages,
                    process_timeout=self.config.message_timeout
                )
            )
        )

    def configure(self, config: TeamConfig) -> 'BaseTeam':
        """Set configuration

        Args:
            config: TeamConfig configuration object

        Returns:
            self (supports chaining)
        """
        self.config = config
        return self

    def add_agent(
        self,
        card: AgentCard,
        provider: AgentProvider,
    ) -> 'BaseTeam':
        """Register an agent to the team via Card + Provider.

        Delegates to ``self.runtime.register_agent`` and appends the card
        to ``self.card.agent_cards``.

        Args:
            card: AgentCard defining agent identity (including id)
            provider: AgentProvider factory for lazy instance creation

        Returns:
            self (supports chaining)
        """
        agent_id = card.id

        if self.runtime.has_agent(agent_id):
            logger.warning(f"[{self.__class__.__name__}] Agent ID '{agent_id}' "
                           f"already exists in team '{self.team_id}', skipping add")
            return self

        if self.runtime.get_agent_count() >= self.config.max_agents:
            raise build_error(
                StatusCode.AGENT_TEAM_ADD_RUNTIME_ERROR,
                error_msg=f"Agent count exceeds max_agents ({self.config.max_agents})"
            )

        self.runtime.register_agent(card, provider)
        self.card.agent_cards.append(card)

        logger.debug(f"[{self.__class__.__name__}] Added agent '{agent_id}' to team '{self.team_id}'")

        return self

    def remove_agent(
        self,
        agent: Union[str, AgentCard]
    ) -> 'BaseTeam':
        """Remove an agent from the team.

        Removes from the runtime card registry and TeamCard metadata.
        Does not unregister from ResourceMgr (agent may be shared).

        Args:
            agent: Agent ID string or AgentCard instance

        Returns:
            self (supports chaining)
        """
        if isinstance(agent, AgentCard):
            agent_id = agent.id
        else:
            agent_id = agent

        removed_card = self.runtime.unregister_agent(agent_id)
        if removed_card:
            self.card.agent_cards = [
                c for c in self.card.agent_cards
                if c.id != removed_card.id
            ]
            logger.debug(f"[{self.__class__.__name__}] Removed agent '{agent_id}' from team '{self.team_id}'")

        return self

    async def subscribe(self, agent_id: str, topic: str) -> None:
        """Add topic subscription (delegates to runtime)

        Args:
            agent_id: Agent ID string
            topic: Topic pattern string
        """
        await self.runtime.subscribe(agent_id, topic)

    async def unsubscribe(self, agent_id: str, topic: str) -> None:
        """Remove topic subscription (delegates to runtime)

        Args:
            agent_id: Agent ID string
            topic: Topic pattern string
        """
        await self.runtime.unsubscribe(agent_id, topic)

    def get_agent_card(self, agent_id: str) -> Optional[AgentCard]:
        """Get agent card by ID (delegates to runtime)

        Args:
            agent_id: Agent ID

        Returns:
            AgentCard or None if not found
        """
        return self.runtime.get_agent_card(agent_id)

    def get_agent_count(self) -> int:
        """Get the number of agents in the team (delegates to runtime)

        Returns:
            Number of agents
        """
        return self.runtime.get_agent_count()

    def list_agents(self) -> List[str]:
        """List all agent IDs (delegates to runtime)

        Returns:
            List of agent IDs
        """
        return self.runtime.list_agents()

    async def send(
        self,
        message: Any,
        recipient: str,
        sender: str,
        session_id: Optional[str] = None,
        timeout: Optional[float] = None
    ) -> Any:
        """Send a P2P message between agents in this team.

        Args:
            message: Message payload
            recipient: Recipient agent ID (must be registered in team)
            sender: Sender agent ID (must be registered in team)
            session_id: session ID
            timeout: Response timeout in seconds

        Returns:
            Response from recipient agent
        """
        if not self.runtime.has_agent(sender):
            raise build_error(
                StatusCode.AGENT_TEAM_AGENT_NOT_FOUND,
                error_msg=f"Sender '{sender}' not found in team '{self.team_id}'"
            )
        if not self.runtime.has_agent(recipient):
            raise build_error(
                StatusCode.AGENT_TEAM_AGENT_NOT_FOUND,
                error_msg=f"Recipient '{recipient}' not found in team '{self.team_id}'"
            )

        return await self.runtime.send(
            message=message,
            recipient=recipient,
            sender=sender,
            session_id=session_id,
            timeout=timeout
        )

    async def publish(
        self,
        message: Any,
        topic_id: str,
        sender: str,
        session_id: Optional[str] = None
    ) -> None:
        """Publish a message to a topic within this team.

        Args:
            message: Message payload
            topic_id: Topic ID (e.g., "code_events", "task_updates")
            sender: Sender agent ID (must be registered in team)
            session_id: session ID
        """
        if not self.runtime.has_agent(sender):
            raise build_error(
                StatusCode.AGENT_TEAM_AGENT_NOT_FOUND,
                error_msg=f"Sender '{sender}' not found in team '{self.team_id}'"
            )

        await self.runtime.publish(
            message=message,
            topic_id=topic_id,
            sender=sender,
            session_id=session_id
        )

    @abstractmethod
    async def invoke(
        self,
        message,
        session: Optional[Session] = None
    ) -> Any:
        """Execute synchronous operation on the agent team.

        Args:
            message: Message object or dict
            session: Session for agent team instance

        Returns:
            The collective output from the agent team
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
        """Execute streaming operation on the agent team.

        Args:
            message: Message object or dict
            session: Session for agent team instance

        Yields:
            Streaming output from the agent team
        """
        raise NotImplementedError(
            f"stream method must be implemented by {self.__class__.__name__}"
        )
