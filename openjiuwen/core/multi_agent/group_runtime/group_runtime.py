# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""GroupRuntime Module

Self-contained runtime for multi-agent communication.
Manages AgentCard registration, wraps agent providers, and exposes
P2P (send) and Pub-Sub (publish/subscribe) messaging through a MessageBus.
"""
from __future__ import annotations

import asyncio
from typing import Any, Optional, TYPE_CHECKING

from pydantic import BaseModel, Field

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import multi_agent_logger as logger
from openjiuwen.core.multi_agent.group_runtime.message_bus import MessageBus, MessageBusConfig

if TYPE_CHECKING:
    from openjiuwen.core.single_agent.schema.agent_card import AgentCard
    from openjiuwen.core.runner.resources_manager.base import AgentProvider


class RuntimeConfig(BaseModel):
    """Multi-agent runtime configuration.

    Attributes:
        group_id: Group ID for topic isolation. Defaults to ``"default"``.
        message_bus: Message bus configuration
    """
    group_id: str = Field(
        default="default",
        description="Group ID for topic isolation"
    )
    message_bus: Optional[MessageBusConfig] = Field(
        default=None,
        description="Message bus configuration for the runtime"
    )


class GroupRuntime:
    """Self-contained runtime for multi-agent communication.

    Can be used standalone or as the backbone of a BaseGroup subclass.
    Manages agent registration, routes P2P and Pub-Sub messages via MessageBus.

    Usage::

        runtime = GroupRuntime()
        runtime.register_agent(coder_card, lambda: CoderAgent(card=coder_card))
        await runtime.start()
        result = await runtime.send(message, recipient="reviewer", sender="coder")
    """

    def __init__(self, config: Optional[RuntimeConfig] = None):
        self._config = config or RuntimeConfig()
        self._group_id = self._config.group_id

        if self._config.message_bus is None:
            self._config.message_bus = MessageBusConfig()
        self._config.message_bus.group_id = self._group_id

        self._agent_cards: dict[str, AgentCard] = {}
        self._message_bus = MessageBus(config=self._config.message_bus, runtime=self)
        self._active_group_sessions = {}
        self._running = False
        self._start_lock: asyncio.Lock = asyncio.Lock()

        logger.info(f"[{self.__class__.__name__}] Initialized with group_id: {self._group_id}")

    def is_running(self) -> bool:
        """Check if the runtime is currently running.

        Returns:
            True if the runtime is running, False otherwise
        """
        return self._running

    # ========== Lifecycle ==========

    async def start(self) -> None:
        """Start the runtime."""
        if self._running:
            logger.warning(f"[{self.__class__.__name__}] Already running")
            return
        await self._message_bus.start()
        self._running = True
        logger.info(f"[{self.__class__.__name__}] Started")

    async def stop(self) -> None:
        """Stop the runtime."""
        if not self._running:
            return
        logger.info(f"[{self.__class__.__name__}] Stopping...")
        self._running = False
        await self._message_bus.stop()
        logger.info(f"[{self.__class__.__name__}] Stopped")

    async def __aenter__(self) -> GroupRuntime:
        """Start the runtime and return self for use as an async context manager.

        Returns:
            This GroupRuntime instance
        """
        await self.start()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Stop the runtime when exiting an async context manager.

        Args:
            exc_type: Exception type (if any)
            exc_val: Exception value (if any)
            exc_tb: Exception traceback (if any)
        """
        await self.stop()

    async def _ensure_started(self) -> None:
        """Start the runtime on first use if it has not been started explicitly."""
        if self._running:
            return
        async with self._start_lock:
            if not self._running:
                await self.start()

    # ========== Agent Registration ==========

    def register_agent(
            self,
            card: AgentCard,
            provider: AgentProvider,
    ) -> None:
        """Register an agent by Card + Provider.

        Stores the AgentCard locally and registers a wrapped provider to
        Runner's ResourceMgr.  The wrapper automatically calls
        ``CommunicableAgent.bind_runtime()`` when the agent is first created.

        Args:
            card: AgentCard defining agent identity (including id)
            provider: Factory callable for lazy agent creation
        """
        agent_id = card.id
        self._agent_cards[agent_id] = card

        wrapped_provider = self._wrap_provider(provider, agent_id)

        try:
            from openjiuwen.core.runner import Runner
            result = Runner.resource_mgr.add_agent(card, wrapped_provider)
            if result.is_err():
                logger.debug(
                    f"[{self.__class__.__name__}] Agent '{agent_id}' already in ResourceMgr, reusing"
                )
        except ImportError as e:
            error_msg = f"Failed to import Runner module: {e}"
            logger.error(f"[{self.__class__.__name__}] {error_msg}", exc_info=True)
            raise build_error(
                StatusCode.AGENT_GROUP_ADD_RUNTIME_ERROR,
                error_msg=error_msg
            ) from e
        except AttributeError as e:
            error_msg = f"Runner.resource_mgr not available: {e}"
            logger.error(f"[{self.__class__.__name__}] {error_msg}", exc_info=True)
            raise build_error(
                StatusCode.AGENT_GROUP_ADD_RUNTIME_ERROR,
                error_msg=error_msg
            ) from e
        except Exception as e:
            error_msg = f"Unexpected error registering agent '{agent_id}' to ResourceMgr: {e}"
            logger.error(f"[{self.__class__.__name__}] {error_msg}", exc_info=True)
            raise build_error(
                StatusCode.AGENT_GROUP_ADD_RUNTIME_ERROR,
                error_msg=error_msg
            ) from e

        logger.info(f"[{self.__class__.__name__}] Registered agent: {agent_id}")

    def _wrap_provider(
            self,
            provider: AgentProvider,
            agent_id: str
    ) -> AgentProvider:
        """Wrap a provider to auto-bind the runtime after agent creation.

        If the created agent is a CommunicableAgent, bind_runtime() is called
        so it can use send()/publish() immediately.

        Args:
            provider: Original agent provider factory
            agent_id: Agent ID to bind

        Returns:
            Wrapped provider callable
        """
        from openjiuwen.core.multi_agent.group_runtime.communicable_agent import CommunicableAgent

        runtime_ref = self

        def wrapped():
            agent = provider()
            if isinstance(agent, CommunicableAgent):
                agent.bind_runtime(runtime_ref, agent_id)
                logger.debug(
                    f"[{self.__class__.__name__}] Auto-bound runtime to CommunicableAgent '{agent_id}'"
                )
            else:
                logger.warning(
                    f"[{self.__class__.__name__}] Agent '{agent_id}' does not inherit from "
                    "CommunicableAgent. Methods send(), publish(), subscribe(), "
                    "unsubscribe() will not be available on this agent."
                )
            return agent

        return wrapped

    def unregister_agent(self, agent_id: str) -> Optional[AgentCard]:
        """Remove an agent from this runtime.

        Clears the local card registry and all topic subscriptions.
        Does not unregister from ResourceMgr (agent may be shared).

        Args:
            agent_id: Agent ID string

        Returns:
            The removed AgentCard, or None if not found
        """
        removed_card = self._agent_cards.pop(agent_id, None)
        if removed_card:
            self._message_bus.remove_all_subscriptions(agent_id)
            logger.info(f"[{self.__class__.__name__}] Unregistered agent: {agent_id}")
        return removed_card

    def has_agent(self, agent_id: str) -> bool:
        """Check whether an agent is registered in this runtime.

        Args:
            agent_id: Agent ID string

        Returns:
            True if the agent is registered
        """
        return agent_id in self._agent_cards

    def get_agent_card(self, agent_id: str) -> Optional[AgentCard]:
        """Get the AgentCard for a registered agent.

        Args:
            agent_id: Agent ID string

        Returns:
            AgentCard, or None if not found
        """
        return self._agent_cards.get(agent_id)

    def list_agents(self) -> list[str]:
        """List all registered agent IDs.

        Returns:
            List of agent IDs
        """
        return list(self._agent_cards.keys())

    def get_agent_count(self) -> int:
        """Return the number of registered agents.

        Returns:
            Agent count
        """
        return len(self._agent_cards)

    def bind_group_session(self, session) -> None:
        self._active_group_sessions[session.get_session_id()] = session

    def unbind_group_session(self, session_id: str) -> None:
        self._active_group_sessions.pop(session_id, None)

    def get_group_session(self, session_id: Optional[str]):
        if session_id is None:
            return None
        return self._active_group_sessions.get(session_id)

    # ========== Subscription Query ==========

    def list_subscriptions(self, agent_id: Optional[str] = None) -> dict[str, Any]:
        """Query subscription state for debugging and introspection.

        Args:
            agent_id: Optional agent ID to filter by

        Returns:
            Dictionary of subscriptions
        """
        return self._message_bus.list_subscriptions(agent_id)

    def get_subscription_count(self) -> int:
        """Return the total number of active topic subscriptions.

        Returns:
            Total subscription count
        """
        return self._message_bus.get_subscription_count()

    # ========== Message Communication ==========

    async def send(
            self,
            message: Any,
            recipient: str,
            sender: str,
            session_id: Optional[str] = None,
            timeout: Optional[float] = None
    ) -> Any:
        """Send a P2P message to a registered agent.

        Args:
            message: Message payload
            recipient: Recipient agent ID (must be registered)
            sender: Sender agent ID (required for tracing)
            session_id: Session ID for continuity (optional)
            timeout: Response timeout in seconds (optional)

        Returns:
            Response from the recipient agent
        """
        if not sender:
            raise build_error(
                StatusCode.AGENT_GROUP_EXECUTION_ERROR,
                error_msg="sender is required for message tracing"
            )
        if not recipient:
            raise build_error(
                StatusCode.AGENT_GROUP_EXECUTION_ERROR,
                error_msg="recipient is required"
            )
        if recipient not in self._agent_cards:
            raise build_error(
                StatusCode.AGENT_GROUP_EXECUTION_ERROR,
                error_msg=f"Recipient '{recipient}' not registered in runtime"
            )

        await self._ensure_started()

        try:
            return await self._message_bus.send(
                message=message,
                recipient=recipient,
                sender=sender,
                session_id=session_id,
                timeout=timeout
            )
        except asyncio.TimeoutError as e:
            error_msg = f"Message from '{sender}' to '{recipient}' timed out after {timeout}s"
            logger.error(f"[{self.__class__.__name__}] {error_msg}", exc_info=True)
            raise build_error(
                StatusCode.AGENT_GROUP_EXECUTION_ERROR,
                error_msg=error_msg
            ) from e
        except Exception as e:
            error_msg = f"Failed to send message from '{sender}' to '{recipient}': {e}"
            logger.error(f"[{self.__class__.__name__}] {error_msg}", exc_info=True)
            raise build_error(
                StatusCode.AGENT_GROUP_EXECUTION_ERROR,
                error_msg=error_msg
            ) from e

    async def publish(
            self,
            message: Any,
            topic_id: str,
            sender: str,
            session_id: Optional[str] = None
    ) -> None:
        """Publish a message to a topic.

        Args:
            message: Message payload
            topic_id: Topic ID (e.g., "code_events")
            sender: Sender agent ID (required for message tracing)
            session_id: Session ID for session continuity (optional)
        """
        if not sender:
            raise build_error(
                StatusCode.AGENT_GROUP_EXECUTION_ERROR,
                error_msg="sender is required for message tracing"
            )
        if not topic_id:
            raise build_error(
                StatusCode.AGENT_GROUP_EXECUTION_ERROR,
                error_msg="topic_id is required"
            )

        await self._ensure_started()

        try:
            await self._message_bus.publish(
                message=message,
                topic_id=topic_id,
                sender=sender,
                session_id=session_id
            )
        except Exception as e:
            error_msg = f"Failed to publish message from '{sender}' to topic '{topic_id}': {e}"
            logger.error(f"[{self.__class__.__name__}] {error_msg}", exc_info=True)
            raise build_error(
                StatusCode.AGENT_GROUP_EXECUTION_ERROR,
                error_msg=error_msg
            ) from e

    async def subscribe(self, agent_id: str, topic: str) -> None:
        """Subscribe an agent to a topic.

        Args:
            agent_id: Agent ID (e.g., "reviewer")
            topic: Topic pattern (e.g., "code_events", "code_*")
        """
        if not agent_id:
            raise build_error(
                StatusCode.AGENT_GROUP_EXECUTION_ERROR,
                error_msg="agent_id is required for subscription"
            )
        if not topic:
            raise build_error(
                StatusCode.AGENT_GROUP_EXECUTION_ERROR,
                error_msg="topic is required for subscription"
            )
        await self._message_bus.add_subscription(agent_id, topic)
        logger.debug(f"[{self.__class__.__name__}] Subscribed: {agent_id} -> {topic}")

    async def unsubscribe(self, agent_id: str, topic: str) -> None:
        """Unsubscribe an agent from a topic.

        Args:
            agent_id: Agent ID
            topic: Topic pattern
        """
        if not agent_id:
            raise build_error(
                StatusCode.AGENT_GROUP_EXECUTION_ERROR,
                error_msg="agent_id is required for unsubscription"
            )
        if not topic:
            raise build_error(
                StatusCode.AGENT_GROUP_EXECUTION_ERROR,
                error_msg="topic is required for unsubscription"
            )
        await self._message_bus.remove_subscription(agent_id, topic)
        logger.debug(f"[{self.__class__.__name__}] Unsubscribed: {agent_id} -> {topic}")
