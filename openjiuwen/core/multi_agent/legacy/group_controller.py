# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Group Controller - Message routing controller for AgentGroup

.. deprecated::
    This module is deprecated and will be removed in a future version.
    It is only kept for backward compatibility with legacy ControllerGroup.
"""

import asyncio
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from openjiuwen.core.controller.legacy.event.event import Event
from openjiuwen.core.common.logging import logger
from openjiuwen.core.runner.message_queue_base import InvokeQueueMessage
from openjiuwen.core.runner.message_queue_inmemory import MessageQueueInMemory
from openjiuwen.core.session.agent_group import Session

if TYPE_CHECKING:
    from openjiuwen.core.multi_agent.legacy import BaseGroup


class BaseGroupController(ABC):
    """Message routing controller - Core message processing for AgentGroup
    
    Design features (similar to BaseController):
    1. Asynchronous processing architecture based on message queue
    2. Manages message routing between Agents
    3. Supports publish-subscribe pattern
    4. Developers only need to implement handle_event()
    
    Core data structures:
    1. multi_agent.agents: Dict[agent_id -> single_agent]  # Access via group reference
    2. subscriptions: Dict[message_type -> List[agent_id]]  # Subscription relationship table
    
    Message type system:
    - Uses event.custom_event_type (string identifier)
    - Developers can define custom message type strings
    - Subscription management routes based on message_type strings
    
    Note: GroupController can be initialized without parameters,
    required attributes will be injected by ControllerGroup
    """

    def __init__(self, agent_group: Optional['BaseGroup'] = None):
        """Initialize BaseGroupController
        
        Args:
            agent_group: Associated AgentGroup (optional, can be injected later)
        
        Note:
            If parameters are not provided, should be set via
            ControllerGroup's setup_from_group()
        """
        self.agent_group = agent_group

        # Create message queue (shared across all messages)
        self.msg_queue = MessageQueueInMemory()
        self._msg_queue_loop = None

        # Core data: subscription relationship table (using message_type string as key)
        self._subscriptions: Dict[str, List[str]] = {}

    def setup_from_group(self, group: 'BaseGroup'):
        """Setup controller from group - Inject required attributes
        
        This method is called by ControllerGroup to inject group reference
        
        Args:
            group: ControllerGroup instance
        """
        self.agent_group = group
        logger.info(
            f"BaseGroupController: Setup from group, "
            f"group_id={group.group_id}"
        )

    async def invoke(self, event: Event, session: Session) -> Any:
        """Synchronous invocation entry
        
        Process:
        1. Lazy start message queue
        2. Publish message to queue
        3. Wait for processing result
        
        Args:
            event: Event object (carries message_type for routing)
            session: Session context
        
        Returns:
            Processing result
        """
        # Lazy start message queue with event loop detection
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.error("No running event loop")
            raise

        if self._msg_queue_loop is not current_loop:
            # Event loop changed or first time - (re)start message queue
            if self._msg_queue_loop is not None:
                # Event loop changed - rebuild everything
                logger.info(
                    f"Event loop changed, recreating message queue "
                    f"(old loop: {id(self._msg_queue_loop)}, "
                    f"new loop: {id(current_loop)})"
                )
                try:
                    await self.msg_queue.stop()
                except Exception as e:
                    logger.warning(f"Failed to stop old message queue: {e}")

                self.msg_queue = MessageQueueInMemory()

            # Ensure subscription exists
            topic = f"group_messages_{self.agent_group.group_id}"
            subscription = self.msg_queue.subscribe(topic)
            subscription.set_message_handler(self._handle_message_wrapper)
            subscription.activate()

            self.msg_queue.start()
            self._msg_queue_loop = current_loop
            logger.debug(
                f"Message queue started in event loop {id(current_loop)}"
            )

        # Create queue message and publish
        topic = f"group_messages_{self.agent_group.group_id}"
        queue_message = InvokeQueueMessage()
        queue_message.payload = {"event": event, "session": session}
        queue_message.response = asyncio.Future()

        # Publish message
        await self.msg_queue.produce_message(topic, queue_message)

        # Wait for result
        result = await queue_message.response

        return result if result is not None else {"output": "processed"}

    async def _handle_message_wrapper(self, request: Dict) -> Any:
        """Message processing wrapper - Automatically called by message queue"""
        event = request["event"]
        session = request["session"]
        try:
            result = await self.handle_event(event, session)
            logger.info(
                f"BaseGroupController: handle_event returned: "
                f"{type(result)}"
            )
            return result
        except Exception as e:
            logger.error(
                f"BaseGroupController: handle_event raised exception: {e}",
                exc_info=True
            )
            raise

    # ===== Abstract methods (developers must implement) =====

    @abstractmethod
    async def handle_event(
        self,
        event: Event,
        session: Session
    ) -> Any:
        """Core method for message processing (must be implemented)
        
        Args:
            event: Event object
            session: Session context
        
        Returns:
            Processing result
        
        Developers implement message routing logic here:
        - Route to corresponding Agent based on message type
        - Point-to-point sending or broadcasting
        - Coordinate multiple Agents
        """
        pass

    # ===== Subscription management API =====

    def subscribe(self, message_type: str, agent_ids: List[str]) -> None:
        """Subscribe to message type
        
        Args:
            message_type: Message type string identifier
            agent_ids: List of Agent IDs
        
        Example:
            controller.subscribe("weather_query", ["agent1", "agent2"])
        """
        if message_type not in self._subscriptions:
            self._subscriptions[message_type] = []

        for agent_id in agent_ids:
            if agent_id not in self._subscriptions[message_type]:
                self._subscriptions[message_type].append(agent_id)
                logger.info(
                    f"BaseGroupController: Agent {agent_id} subscribed to "
                    f"message_type={message_type}"
                )

    def unsubscribe(self, message_type: str, agent_ids: List[str]) -> None:
        """Unsubscribe from message type
        
        Args:
            message_type: Message type string identifier
            agent_ids: List of Agent IDs
        """
        if message_type in self._subscriptions:
            for agent_id in agent_ids:
                if agent_id in self._subscriptions[message_type]:
                    self._subscriptions[message_type].remove(agent_id)
                    logger.info(
                        f"BaseGroupController: Agent {agent_id} unsubscribed "
                        f"from message_type={message_type}"
                    )

    def get_subscribers(self, message_type: str) -> List[str]:
        """Get list of subscribers for specified message type
        
        Args:
            message_type: Message type string identifier
        
        Returns:
            List of subscriber Agent IDs
        """
        return self._subscriptions.get(message_type, [])

    # ===== Message sending API =====

    async def send_to_agent(
        self,
        event: Event,
        agent_id: str,
        session: Session
    ) -> Any:
        """Send message to specified Agent (point-to-point, streaming)
        
        Call single_agent.stream() with shared session. Agent writes stream
        data to session (doesn't read from stream_iterator to avoid nested
        deadlock).
        
        External ControllerGroup.stream() reads via session.stream_iterator().
        
        Args:
            event: Event object
            agent_id: Target Agent ID
            session: Session context (shared stream)
        
        Returns:
            Final result (last chunk or default)
        """
        from openjiuwen.core.session.stream.base import OutputSchema
        from openjiuwen.core.common.constants.constant import INTERACTION

        agent = self.agent_group.agents.get(agent_id)
        if not agent:
            logger.warning(
                f"BaseGroupController: Agent {agent_id} not found in group"
            )
            return None

        # Preserve InteractiveInput object if present, otherwise extract string
        # This ensures InteractiveInput can be passed through the single_agent
        # chain without loss
        if (hasattr(event.content, 'interactive_input') 
            and event.content.interactive_input is not None):
            query_value = event.content.interactive_input
        else:
            query_value = event.content.get_query()
        
        inputs = {
            "query": query_value,
            "conversation_id": event.context.conversation_id,
            "user_id": event.source.user_id
        }
        
        logger.info(
            f"BaseGroupController: Streaming message to single_agent "
            f"{agent_id}"
        )
        
        try:
            # Call single_agent.stream with shared session
            # Agent writes to session, doesn't read from stream_iterator
            # Collect chunks to handle interrupt case
            chunks = []
            async for chunk in agent.stream(inputs, session):
                chunks.append(chunk)
            
            # Check if interrupt case (contains __interaction__ type)
            if chunks:
                has_interaction = any(
                    isinstance(c, OutputSchema) and c.type == INTERACTION
                    for c in chunks
                )
                if has_interaction:
                    # Interrupt: return entire list
                    return chunks
                
                # Normal case: return last result
                final_result = chunks[-1]
                if isinstance(final_result, OutputSchema):
                    return final_result.payload
                return final_result
            
            return {"output": "processed"}
        except Exception as e:
            logger.error(
                f"BaseGroupController: Failed to stream single_agent "
                f"{agent_id}: {e}",
                exc_info=True
            )
            raise

    async def publish(
        self,
        event: Event,
        session: Session
    ) -> List[Any]:
        """Publish message to all subscribers (broadcast)
        
        Find subscribers based on event.custom_event_type and route
        
        Args:
            event: Event object (carries message_type)
            session: Session context
        
        Returns:
            List of results from all subscribers
        """
        message_type = event.custom_event_type
        
        if not message_type:
            logger.warning(
                "BaseGroupController: Message has no message_type, "
                "cannot route to subscribers"
            )
            return []
        
        subscribers = self._subscriptions.get(message_type, [])

        if not subscribers:
            logger.info(
                f"BaseGroupController: No subscribers for "
                f"message_type={message_type}"
            )
            return []

        logger.info(
            f"BaseGroupController: Publishing message to {len(subscribers)} "
            f"subscribers for message_type={message_type}"
        )

        # Concurrently call all subscribers
        tasks = [
            self.send_to_agent(event, agent_id, session)
            for agent_id in subscribers
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Log exceptions
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error(
                    f"BaseGroupController: Subscriber {subscribers[i]} "
                    f"raised exception: {result}"
                )
        
        return results

    async def stop(self):
        """Stop group controller - Clean up all resources"""
        logger.info("BaseGroupController: Stopping")
        await self.msg_queue.stop()


class DefaultGroupController(BaseGroupController):
    """Default GroupController - Routes messages based on subscription
    
    Implements handle_event() with standard message routing logic:
    1. If receiver_id is specified: point-to-point sending
    2. If receiver_id is not specified: broadcast based on subscriptions
    """

    async def handle_event(
        self,
        event: Event,
        session: Session
    ) -> Any:
        """Handle message - Dispatch to corresponding Agent based on type
        
        Routing logic:
        1. If receiver_id is specified: point-to-point sending
        2. If receiver_id is not specified: broadcast based on subscriptions
        
        Args:
            event: Event object
            session: Session context
        
        Returns:
            Processing result
        """
        if event.receiver_id:
            # Point-to-point sending
            logger.info(
                f"DefaultGroupController: Routing message to "
                f"receiver_id={event.receiver_id}"
            )
            return await self.send_to_agent(event, event.receiver_id, session)
        else:
            # Broadcast based on subscription relationships
            logger.info(
                f"DefaultGroupController: Broadcasting message with "
                f"message_type={event.custom_event_type}"
            )
            results = await self.publish(event, session)
            # Return single result for single subscriber
            # Return list for multiple subscribers (explicit broadcast)
            return results[0] if len(results) == 1 else results
