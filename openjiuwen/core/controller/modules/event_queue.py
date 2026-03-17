# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Event Queue Module

This module implements the event queue, responsible for event publishing and subscription:
EventQueue: Event queue that handles event publishing and subscription

Workflow:
- Event Subscription: Subscribe to specific types of events via the subscribe method
- Event Publishing: Publish events to the message queue via the publish_event method
- Event Processing: Subscribed events will be handled by the corresponding methods of EventHandler
- Event Unsubscription: Unsubscribe from events via the unsubscribe method

Supported Event Types:
- INPUT: User input event
- TASK_INTERACTION: Task interaction event (user interaction required during task execution)
- TASK_COMPLETION: Task completion event
- TASK_FAILED: Task failure event
"""

from typing import Callable, Awaitable, Optional

from openjiuwen.core.controller.config import ControllerConfig
from openjiuwen.core.controller.schema.event import Event, EventType
from openjiuwen.core.controller.modules.event_handler import EventHandler, EventHandlerInput
from openjiuwen.core.session.agent import Session
from openjiuwen.core.common.exception.errors import build_error, BaseError
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.logging import logger


class EventQueue:
    """Event Queue

    Responsible for publishing and subscribing to events, and
    dispatching them to the appropriate methods of event handlers.

    Built on top of a message queue, it supports:
    - Publishing events (publish_event)
    - Subscribing to events (subscribe)
    - Unsubscribing from events (unsubscribe)
    - Handling multiple event types

    Event topic format: {agent_id}_{session_id}_{event_type}

    Workflow:
    - MessageQueue starts a background consumer task
    - When publish_event is called, the message is placed into the queue
    - MessageQueue automatically invokes the registered callback function
    - The callback converts the message into an EventHandlerInput and calls the corresponding EventHandler method
    """

    def __init__(
            self,
            config: ControllerConfig,
    ):
        """Initialize the event queue

        Args:
            config: Controller configuration
        """
        # Lazy import
        from openjiuwen.core.runner.message_queue_inmemory import MessageQueueInMemory

        self._config = config
        self._queue: MessageQueueInMemory = MessageQueueInMemory(
            queue_max_size=self._config.event_queue_size, timeout=self._config.event_timeout
        )
        self._event_handler: Optional[EventHandler] = None

    @property
    def config(self) -> ControllerConfig:
        """Get configuration"""
        return self._config

    @config.setter
    def config(self, config: ControllerConfig):
        """Update configuration"""
        self._config = config

    def set_event_handler(self, event_handler: EventHandler):
        """Set the event handler

        Args:
            event_handler: Event handler instance
        """
        self._event_handler = event_handler

    def start(self):
        """Start event queue message processing

        Start the MessageQueue background consumer task
        """
        self._queue.start()

    async def stop(self):
        """Stop event queue message processing

        Stop the MessageQueue background consumer task
        """
        await self._queue.stop()

    def _subscribe_event(
            self,
            topic: str,
            event_handle_func: Callable[[EventHandlerInput], Awaitable[...]]
    ):
        """Subscribe to a single event topic

        Args:
            topic: Event topic
            event_handle_func: Event handling function

        Returns:
            str: Event topic
        """
        # Create subscription
        subscription = self._queue.subscribe(topic)

        async def event_handle_wrapper(payload: dict):
            """Wrapper: extract Event and Session, build EventHandlerInput"""
            event = payload["event"]
            session = payload["session"]
            handler_input = EventHandlerInput(event=event, session=session)
            return await event_handle_func(handler_input)

        subscription.set_message_handler(event_handle_wrapper)
        subscription.activate()

        return topic

    async def subscribe(
            self,
            agent_id: str,
            session_id: str
    ) -> (dict[str, str], dict[str, str]):
        """Subscribe to all event types

        Args:
            agent_id: Agent ID
            session_id: Session ID

        Returns:
            Tuple[dict, dict]: (subscription dict, topic dict)
        """
        try:
            topics = {}
            subscriptions = {}

            # Subscribe to input event
            topic = self._build_topic(agent_id, session_id, EventType.INPUT)
            sub = self._subscribe_event(topic, self._event_handler.handle_input)
            subscriptions[EventType.INPUT] = sub
            topics[EventType.INPUT] = topic

            # Subscribe to task interaction event
            topic = self._build_topic(agent_id, session_id, EventType.TASK_INTERACTION)
            sub = self._subscribe_event(topic, self._event_handler.handle_task_interaction)
            subscriptions[EventType.TASK_INTERACTION] = sub
            topics[EventType.TASK_INTERACTION] = topic

            # Subscribe to task completion event
            topic = self._build_topic(agent_id, session_id, EventType.TASK_COMPLETION)
            sub = self._subscribe_event(topic, self._event_handler.handle_task_completion)
            subscriptions[EventType.TASK_COMPLETION] = sub
            topics[EventType.TASK_COMPLETION] = topic

            # Subscribe to task failed event
            topic = self._build_topic(agent_id, session_id, EventType.TASK_FAILED)
            sub = self._subscribe_event(topic, self._event_handler.handle_task_failed)
            subscriptions[EventType.TASK_FAILED] = sub
            topics[EventType.TASK_FAILED] = topic

            # Subscribe to follow-up event
            topic = self._build_topic(agent_id, session_id, EventType.FOLLOW_UP)
            sub = self._subscribe_event(topic, self._event_handler.handle_follow_up)
            subscriptions[EventType.FOLLOW_UP] = sub
            topics[EventType.FOLLOW_UP] = topic

            return subscriptions, topics

        except Exception as e:
            logger.error(f"Event queue execution failed: {e}", exc_info=True)
            raise build_error(
                StatusCode.AGENT_CONTROLLER_EVENT_QUEUE_ERROR,
                error_msg=str(e),
                cause=e
            ) from e

    async def _unsubscribe_event(
            self,
            topic: str
    ):
        """Unsubscribe from a single event topic

        Args:
            topic: Event topic

        Returns:
            bool: Whether unsubscribe succeeded
        """
        await self._queue.unsubscribe(topic)
        return True

    async def unsubscribe(
            self,
            agent_id: str,
            session_id: str,
    ):
        """Unsubscribe from all event types

        Args:
            agent_id: Agent ID
            session_id: Session ID

        Returns:
            dict: Topic dictionary
        """
        topics = {}

        # Unsubscribe from input event
        topic = self._build_topic(agent_id, session_id, EventType.INPUT)
        await self._unsubscribe_event(topic)

        # Unsubscribe from task interaction event
        topic = self._build_topic(agent_id, session_id, EventType.TASK_INTERACTION)
        await self._unsubscribe_event(topic)

        # Unsubscribe from task completion event
        topic = self._build_topic(agent_id, session_id, EventType.TASK_COMPLETION)
        await self._unsubscribe_event(topic)

        # Unsubscribe from task failed event
        topic = self._build_topic(agent_id, session_id, EventType.TASK_FAILED)
        await self._unsubscribe_event(topic)

        # Unsubscribe from follow-up event
        topic = self._build_topic(agent_id, session_id, EventType.FOLLOW_UP)
        await self._unsubscribe_event(topic)

        return topics

    async def publish_event(
            self,
            agent_id: str,
            session: 'Session',
            event: Event
    ) -> None:
        """Publish an event to the event queue and wait until it is handled

        Args:
            agent_id: Agent ID
            session: Session object
            event: Event to be published

        Note:
            - This method waits until the EventHandler finishes processing before
              it returns, ensuring event processing order.
        """
        session_id = session.get_session_id()
        topic = self._build_topic(agent_id, session_id, event.event_type)

        # Lazy import to avoid circular imports
        from openjiuwen.core.runner.message_queue_base import InvokeQueueMessage

        queue_message = InvokeQueueMessage()
        queue_message.payload = {"event": event, "session": session}

        # Publish message
        await self._queue.produce_message(topic, queue_message)

        # Wait until EventHandler finishes processing
        try:
            await queue_message.response

        except BaseError:
            raise

        except Exception as e:
            logger.error(f"Event handler failed for {event.event_type}: {e}", exc_info=True)
            raise build_error(
                StatusCode.AGENT_CONTROLLER_EVENT_HANDLER_ERROR,
                error_msg=str(e),
                cause=e
            ) from e

    async def publish_event_async(
            self,
            agent_id: str,
            session: "Session",
            event: Event,
    ) -> None:
        """Fire-and-forget event publish.

        Enqueues the event and returns immediately without
        waiting for the handler to finish processing.

        Args:
            agent_id: Agent ID
            session: Session object
            event: Event to be published
        """
        session_id = session.get_session_id()
        topic = self._build_topic(
            agent_id, session_id, event.event_type
        )
        from openjiuwen.core.runner.message_queue_base import (
            QueueMessage,
        )
        queue_message = QueueMessage()
        queue_message.payload = {
            "event": event, "session": session
        }
        await self._queue.produce_message(
            topic, queue_message
        )

    async def unsubscribe_all(self) -> None:
        """Unsubscribe from all topics"""
        await self._queue.stop()

    @staticmethod
    def _build_topic(agent_id: str, session_id: str, event_type: str) -> str:
        """Build event topic

        Args:
            agent_id: Agent ID
            session_id: Session ID
            event_type: Event type

        Returns:
            str: Event topic string
        """
        return f"{agent_id}_{session_id}_{event_type}"
