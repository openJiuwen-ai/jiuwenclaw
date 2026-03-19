# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Message Bus Module

Provides the message routing infrastructure for multi-agent communication.
Supports both P2P and Pub-Sub patterns with group/session-scoped topic isolation.
"""
from __future__ import annotations

import asyncio
import uuid
from typing import TYPE_CHECKING, Any, Optional

from pydantic import BaseModel

from openjiuwen.core.common.logging import multi_agent_logger as logger
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.multi_agent.group_runtime.message_router import MessageRouter
from openjiuwen.core.multi_agent.group_runtime.subscription_manager import SubscriptionManager
from openjiuwen.core.multi_agent.group_runtime.envelope import MessageEnvelope

# Topic suffix constants for dynamic topic naming
_P2P_TOPIC_SUFFIX = "__p2p__"
_PUBSUB_TOPIC_SUFFIX = "__pubsub__"


class MessageBusConfig(BaseModel):
    """Message bus configuration.

    Attributes:
        max_queue_size: Maximum message queue size
        process_timeout: Message processing timeout in seconds
        group_id: Group ID for topic isolation
    """
    max_queue_size: int = 1000
    process_timeout: Optional[float] = 100000
    group_id: Optional[str] = None


class MessageBus:
    """Message bus providing P2P and Pub-Sub routing for agent communication."""

    def __init__(self, config: Optional[MessageBusConfig] = None, runtime=None):
        self._config = config or MessageBusConfig()
        self._group_id = self._config.group_id or "default"

        try:
            from openjiuwen.core.runner.message_queue_inmemory import MessageQueueInMemory
            self._mq: MessageQueueInMemory = MessageQueueInMemory(
                queue_max_size=self._config.max_queue_size,
                timeout=self._config.process_timeout
            )
        except Exception as e:
            error_msg = f"Failed to initialize MessageQueueInMemory: {e}"
            logger.error(f"[{self.__class__.__name__}] {error_msg}", exc_info=True)
            raise build_error(
                StatusCode.MESSAGE_QUEUE_INITIATION_ERROR,
                type="MessageQueueInMemory",
                reason=error_msg
            ) from e

        self._active_subscriptions = {}
        self._subscription_lock = asyncio.Lock()

        self._subscription_manager = SubscriptionManager()
        self._router = MessageRouter(self._subscription_manager, runtime=runtime)
        self._running = False

        logger.info(f"[{self.__class__.__name__}] Initialized with group_id: {self._group_id}")

    # ========== Topic Helpers ==========

    def _get_p2p_topic(self, session_id: Optional[str] = None) -> str:
        """Generate the P2P topic name for a given session.

        Args:
            session_id: Optional session ID for per-session isolation

        Returns:
            Topic name: ``{group_id}_{session_id}__p2p__`` or ``{group_id}__p2p__``
        """
        if session_id:
            return f"{self._group_id}_{session_id}{_P2P_TOPIC_SUFFIX}"
        return f"{self._group_id}{_P2P_TOPIC_SUFFIX}"

    def _get_pubsub_topic(self, session_id: Optional[str] = None) -> str:
        """Generate the Pub-Sub topic name for a given session.

        Args:
            session_id: Optional session ID for per-session isolation

        Returns:
            Topic name: ``{group_id}_{session_id}__pubsub__`` or ``{group_id}__pubsub__``
        """
        if session_id:
            return f"{self._group_id}_{session_id}{_PUBSUB_TOPIC_SUFFIX}"
        return f"{self._group_id}{_PUBSUB_TOPIC_SUFFIX}"

    async def _ensure_subscription(self, topic: str):
        """Lazily create a queue subscription for a topic if it does not exist.

        Uses double-checked locking to avoid duplicate subscriptions under
        concurrent access.

        Args:
            topic: Topic name to subscribe
        """
        if topic in self._active_subscriptions:
            return

        async with self._subscription_lock:
            if topic in self._active_subscriptions:
                return

            subscription = self._mq.subscribe(topic)

            if _P2P_TOPIC_SUFFIX in topic:
                subscription.set_message_handler(self._handle_p2p_message)
            elif _PUBSUB_TOPIC_SUFFIX in topic:
                subscription.set_message_handler(self._handle_pubsub_message)

            subscription.activate()
            self._active_subscriptions[topic] = subscription

    # ========== Lifecycle ==========

    async def cleanup_session(self, session_id: str) -> None:
        """Remove all active queue subscriptions created for a specific session.

        Call this when a session ends to prevent ``_active_subscriptions``
        from growing without bound in long-running, high-concurrency deployments.

        Args:
            session_id: The session ID whose P2P and Pub-Sub topics should be
                cleaned up.
        """
        p2p_topic = self._get_p2p_topic(session_id)
        pubsub_topic = self._get_pubsub_topic(session_id)

        async with self._subscription_lock:
            for topic in (p2p_topic, pubsub_topic):
                subscription = self._active_subscriptions.pop(topic, None)
                if subscription is None:
                    continue
                try:
                    await subscription.deactivate()
                    logger.debug(f"[{self.__class__.__name__}] cleanup_session: deactivated {topic}")
                except Exception as exc:
                    logger.error(
                        f"[{self.__class__.__name__}] cleanup_session: failed to deactivate {topic}: {exc}",
                        exc_info=True,
                    )


    async def start(self) -> None:
        """Start the message bus."""
        if self._running:
            logger.warning("[{self.__class__.__name__}] Already running")
            return

        self._running = True
        try:
            self._mq.start()
        except Exception as e:
            self._running = False
            error_msg = f"Failed to start MessageQueueInMemory: {e}"
            logger.error(f"[{self.__class__.__name__}] {error_msg}", exc_info=True)
            raise build_error(
                StatusCode.MESSAGE_QUEUE_INITIATION_ERROR,
                type="MessageQueueInMemory",
                reason=error_msg
            ) from e

        logger.info(f"[{self.__class__.__name__}:{self._group_id}] Started")

    async def stop(self) -> None:
        """Stop the message bus and clean up all subscriptions."""
        if not self._running:
            return

        logger.info(f"[{self.__class__.__name__}:{self._group_id}] Stopping...")
        self._running = False

        async with self._subscription_lock:
            for topic, subscription in list(self._active_subscriptions.items()):
                try:
                    await subscription.deactivate()
                    logger.debug(f"[{self.__class__.__name__}] Deactivated subscription: {topic}")
                except Exception as e:
                    logger.error(
                        f"[{self.__class__.__name__}] Failed to deactivate subscription '{topic}': {e}",
                        exc_info=True
                    )
            self._active_subscriptions.clear()

        try:
            await self._mq.stop()
        except Exception as e:
            error_msg = f"Failed to stop MessageQueueInMemory: {e}"
            logger.error(f"[{self.__class__.__name__}] {error_msg}", exc_info=True)
            raise build_error(
                StatusCode.MESSAGE_QUEUE_INITIATION_ERROR,
                type="MessageQueueInMemory",
                reason=f"[shutdown phase] {error_msg}"
            ) from e

        logger.info(f"[{self.__class__.__name__}:{self._group_id}] Stopped")

    # ========== Messaging ==========

    async def send(
            self,
            message: Any,
            recipient: str,
            sender: Optional[str] = None,
            session_id: Optional[str] = None,
            timeout: Optional[float] = None
    ) -> Any:
        """Send a P2P message and wait for the response.

        Args:
            message: Message payload
            recipient: Recipient agent ID
            sender: Sender agent ID (optional, for tracing)
            session_id: Session ID for per-session topic isolation (optional)
            timeout: Response timeout in seconds (optional)

        Returns:
            Response from the recipient agent
        """
        try:
            from openjiuwen.core.runner.message_queue_base import InvokeQueueMessage
        except ImportError as e:
            error_msg = f"Failed to import InvokeQueueMessage: {e}"
            logger.error(f"[{self.__class__.__name__}] {error_msg}", exc_info=True)
            raise build_error(
                StatusCode.MESSAGE_QUEUE_INITIATION_ERROR,
                type="InvokeQueueMessage",
                reason=error_msg
            ) from e

        topic = self._get_p2p_topic(session_id)
        await self._ensure_subscription(topic)

        envelope = MessageEnvelope(
            message_id=str(uuid.uuid4()),
            message=message,
            sender=sender,
            recipient=recipient,
            session_id=session_id,
        )

        queue_msg = InvokeQueueMessage()
        queue_msg.message_id = envelope.message_id
        queue_msg.payload = envelope

        try:
            await self._mq.produce_message(topic, queue_msg)
            logger.debug(
                f"[{self.__class__.__name__}] Sent to {topic}: "
                f"{sender} -> {recipient}, session={session_id}"
            )
        except Exception as e:
            error_msg = f"Failed to produce P2P message: {e}"
            logger.error(f"[{self.__class__.__name__}] {error_msg}", exc_info=True)
            raise build_error(
                StatusCode.MESSAGE_QUEUE_TOPIC_MESSAGE_PRODUCTION_ERROR,
                topic=topic,
                message=str(envelope),
                reason=error_msg
            ) from e

        try:
            if timeout:
                response = await asyncio.wait_for(queue_msg.response, timeout=timeout)
            else:
                response = await queue_msg.response
            return response
        except asyncio.TimeoutError:
            error_msg = f"P2P message timeout after {timeout}s: {envelope.message_id} -> {recipient}"
            logger.error(f"[{self.__class__.__name__}] {error_msg}", exc_info=True)
            raise
        except Exception as e:
            error_msg = f"Failed to get P2P message response: {e}"
            logger.error(f"[{self.__class__.__name__}] {error_msg}", exc_info=True)
            raise build_error(
                StatusCode.MESSAGE_QUEUE_MESSAGE_PROCESS_EXECUTION_ERROR,
                reason=error_msg
            ) from e

    async def publish(
            self,
            message: Any,
            topic_id: str,
            sender: Optional[str] = None,
            session_id: Optional[str] = None
    ) -> None:
        """Publish a message to a topic (fire-and-forget).

        Args:
            message: Message payload
            topic_id: Topic ID (e.g., "code_events")
            sender: Sender agent ID (optional, for tracing)
            session_id: Session ID for per-session topic isolation (optional)
        """
        try:
            from openjiuwen.core.runner.message_queue_base import QueueMessage
        except ImportError as e:
            error_msg = f"Failed to import QueueMessage: {e}"
            logger.error(f"[{self.__class__.__name__}] {error_msg}", exc_info=True)
            raise build_error(
                StatusCode.MESSAGE_QUEUE_INITIATION_ERROR,
                type="QueueMessage",
                reason=error_msg
            ) from e

        topic = self._get_pubsub_topic(session_id)
        await self._ensure_subscription(topic)

        envelope = MessageEnvelope(
            message_id=str(uuid.uuid4()),
            message=message,
            sender=sender,
            topic_id=topic_id,
            session_id=session_id,
        )

        queue_msg = QueueMessage()
        queue_msg.message_id = envelope.message_id
        queue_msg.payload = envelope

        try:
            await self._mq.produce_message(topic, queue_msg)
            logger.debug(
                f"[{self.__class__.__name__}] Published to {topic}: "
                f"topic_id={topic_id}, session={session_id}"
            )
        except Exception as e:
            error_msg = f"Failed to produce Pub-Sub message: {e}"
            logger.error(f"[{self.__class__.__name__}] {error_msg}", exc_info=True)
            raise build_error(
                StatusCode.MESSAGE_QUEUE_TOPIC_MESSAGE_PRODUCTION_ERROR,
                topic=topic,
                message=str(envelope),
                reason=error_msg
            ) from e

    # ========== Subscription Management ==========

    async def add_subscription(self, agent_id: str, topic: str) -> None:
        """Add a topic subscription for an agent.

        Args:
            agent_id: Agent ID (e.g., "reviewer")
            topic: Topic pattern (e.g., "code_events", "code_*")
        """
        self._subscription_manager.subscribe(agent_id, topic)

    async def remove_subscription(self, agent_id: str, topic: str) -> None:
        """Remove a topic subscription for an agent.

        Args:
            agent_id: Agent ID
            topic: Topic pattern
        """
        self._subscription_manager.unsubscribe(agent_id, topic)

    def remove_all_subscriptions(self, agent_id: str) -> None:
        """Remove all topic subscriptions for an agent (synchronous).

        Args:
            agent_id: Agent ID
        """
        self._subscription_manager.unsubscribe_all(agent_id)

    def list_subscriptions(self, agent_id=None) -> dict:
        """Query subscription state for debugging and introspection.

        Args:
            agent_id: Optional agent ID to filter by

        Returns:
            Dictionary of subscriptions
        """
        return self._subscription_manager.list_subscriptions(agent_id)

    def get_subscription_count(self) -> int:
        """Return the total number of active topic subscriptions.

        Returns:
            Total subscription count
        """
        return self._subscription_manager.get_subscription_count()

    # ========== Internal Handlers ==========

    async def _handle_p2p_message(self, payload: Any) -> Any:
        """Handle an incoming P2P message from the queue.

        Called by ``SubscriptionInMemory`` when a P2P message arrives.
        The return value is set on ``InvokeQueueMessage.response``.

        Args:
            payload: ``QueueMessage.payload`` (a ``MessageEnvelope``)

        Returns:
            Response from the target agent via Runner.run_agent
        """
        try:
            envelope = self._extract_envelope_from_payload(payload)
            logger.debug(
                f"[{self.__class__.__name__}] Processing P2P message: {envelope.message_id} -> {envelope.recipient}"
            )
            return await self._router.route_p2p_message(envelope)
        except ValueError as e:
            error_msg = f"Invalid P2P message payload: {e}"
            logger.error(f"[{self.__class__.__name__}] {error_msg}", exc_info=True)
            raise build_error(
                StatusCode.MESSAGE_QUEUE_MESSAGE_PROCESS_EXECUTION_ERROR,
                reason=error_msg
            ) from e
        except Exception as e:
            error_msg = f"Error handling P2P message: {e}"
            logger.error(f"[{self.__class__.__name__}] {error_msg}", exc_info=True)
            raise build_error(
                StatusCode.MESSAGE_QUEUE_MESSAGE_PROCESS_EXECUTION_ERROR,
                reason=error_msg
            ) from e

    async def _handle_pubsub_message(self, payload: Any) -> None:
        """Handle an incoming Pub-Sub message from the queue.

        Called by ``SubscriptionInMemory`` when a Pub-Sub message arrives.
        Errors are logged but not re-raised (fire-and-forget semantics).

        Args:
            payload: ``QueueMessage.payload`` (a ``MessageEnvelope``)
        """
        try:
            envelope = self._extract_envelope_from_payload(payload)
            logger.debug(
                f"[{self.__class__.__name__}] Processing Pub-Sub message: "
                f"{envelope.message_id} -> topic:{envelope.topic_id}"
            )
            await self._router.route_pubsub_message(envelope)
        except ValueError as e:
            error_msg = f"Invalid Pub-Sub message payload: {e}"
            logger.error(f"[{self.__class__.__name__}] {error_msg}", exc_info=True)
        except Exception as e:
            error_msg = f"Error handling Pub-Sub message: {e}"
            logger.error(f"[{self.__class__.__name__}] {error_msg}", exc_info=True)

    @staticmethod
    def _extract_envelope_from_payload(payload: Any) -> MessageEnvelope:
        """Extract a MessageEnvelope from a queue message payload.

        Args:
            payload: Expected to be a ``MessageEnvelope`` instance

        Returns:
            MessageEnvelope
        """
        if isinstance(payload, MessageEnvelope):
            return payload
        raise ValueError(
            f"Invalid payload type: {type(payload)}, expected MessageEnvelope"
        )
 
