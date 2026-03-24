# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""CommunicableAgent Mixin Module

Provides communication capabilities (P2P, Pub-Sub, subscribe) to agents.
"""
from __future__ import annotations

from typing import Any, Optional, TYPE_CHECKING

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import multi_agent_logger as logger

if TYPE_CHECKING:
    from openjiuwen.core.multi_agent.team_runtime.team_runtime import TeamRuntime


class CommunicableAgent:
    """Mixin that adds messaging capabilities to an agent.

    Enables agents to send P2P messages, publish to topics, and manage
    subscriptions through a bound TeamRuntime.

    The runtime binding is set automatically by TeamRuntime.register_agent.

    Usage::

        class MyAgent(CommunicableAgent, BaseAgent):
            async def invoke(self, inputs, session=None):
                session_id = session.get_session_id() if session else None
                response = await self.send(
                    message={"task": "review"},
                    recipient="reviewer",
                    session_id=session_id
                )
                await self.publish(
                    message={"event": "completed"},
                    topic_id="code_events",
                    session_id=session_id
                )
                return response
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._runtime: Optional[TeamRuntime] = None
        self._agent_id: Optional[str] = None

    def bind_runtime(
            self,
            runtime: TeamRuntime,
            agent_id: str
    ) -> None:
        """Bind a TeamRuntime to this agent.

        Called automatically by TeamRuntime.register_agent.

        Args:
            runtime: TeamRuntime instance
            agent_id: This agent's ID
        """
        if self.is_bound:
            if self._runtime is runtime and self._agent_id == agent_id:
                # Same runtime and agent_id — idempotent, skip silently
                return
            logger.warning(
                f"[{self.__class__.__name__}] Agent '{self._agent_id}' is already bound to a runtime. "
                "Rebinding may cause unexpected behavior."
            )
        self._runtime = runtime
        self._agent_id = agent_id

    @property
    def is_bound(self) -> bool:
        """Check whether this agent is bound to a runtime.

        Returns:
            True if bind_runtime has been called with valid values
        """
        return self._runtime is not None and self._agent_id is not None

    @property
    def runtime(self) -> TeamRuntime:
        """The bound TeamRuntime."""
        if self._runtime is None:
            raise build_error(
                StatusCode.AGENT_TEAM_EXECUTION_ERROR,
                error_msg="Agent not bound to a TeamRuntime. "
                          "Register the agent with a TeamRuntime first."
            )
        return self._runtime

    @property
    def agent_id(self) -> str:
        """This agent's ID string."""
        if self._agent_id is None:
            raise build_error(
                StatusCode.AGENT_TEAM_EXECUTION_ERROR,
                error_msg="Agent not bound to a TeamRuntime. "
                          "Register the agent with a TeamRuntime first."
            )
        return self._agent_id

    async def send(
            self,
            message: Any,
            recipient: str,
            session_id: Optional[str] = None,
            timeout: Optional[float] = None
    ) -> Any:
        """Send a P2P message to another agent.

        Args:
            message: Message payload
            recipient: Recipient agent ID
            session_id: Session ID
            timeout: Response timeout in seconds (optional)

        Returns:
            Response from the recipient agent
        """
        return await self.runtime.send(
            message=message,
            recipient=recipient,
            sender=self.agent_id,
            session_id=session_id,
            timeout=timeout
        )

    async def publish(
            self,
            message: Any,
            topic_id: str,
            session_id: Optional[str] = None
    ) -> None:
        """Publish a message to a topic.

        Args:
            message: Message payload
            topic_id: Topic ID
            session_id: Session ID
        """
        await self.runtime.publish(
            message=message,
            topic_id=topic_id,
            sender=self.agent_id,
            session_id=session_id
        )

    async def subscribe(self, topic: str) -> None:
        """Subscribe to a topic.

        Args:
            topic: Topic pattern (supports ``*`` and ``?`` wildcards)
        """
        await self.runtime.subscribe(self.agent_id, topic)

    async def unsubscribe(self, topic: str) -> None:
        """Unsubscribe from a topic.

        Args:
            topic: Topic pattern
        """
        await self.runtime.unsubscribe(self.agent_id, topic)
