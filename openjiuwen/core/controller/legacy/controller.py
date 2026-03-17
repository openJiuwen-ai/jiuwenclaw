# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved
"""Controller of Agent"""
import asyncio
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from openjiuwen.core.common.exception.errors import build_error, BaseError
from openjiuwen.core.single_agent.legacy import AgentConfig
from openjiuwen.core.controller.legacy.event.event import Event
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.logging import logger
from openjiuwen.core.context_engine import ContextEngine
from openjiuwen.core.runner.message_queue_base import InvokeQueueMessage
from openjiuwen.core.runner.message_queue_inmemory import MessageQueueInMemory
from openjiuwen.core.session.agent import Session


class BaseController(ABC):
    """Message queue based Controller
    
    Design: Lazy subscription per conversation_id to avoid message mixing
    - Each conversation_id gets its own topic: "controller_messages_{conversation_id}"
    - Subscriptions are created on-demand in invoke()
    - Supports concurrent multi-conversation processing
    
    Note: Controller can be initialized without parameters, required attributes
    will be injected by ControllerAgent
    """

    def __init__(
            self,
            config: AgentConfig = None,
            context_engine: ContextEngine = None,
            session: Session = None
    ):
        """Initialize BaseController
        
        Args:
            config: Agent configuration (optional, can be injected later)
            context_engine: Context engine (optional, can be injected later)
            session: Agent-level Session (optional, can be injected later)
            
        Note:
            If parameters are not provided during initialization,
            they should be set by ControllerAgent via setup_from_agent()
        """
        # Hold core dependencies (can be None initially)
        self._config = config
        self._context_engine = context_engine
        
        # Group reference (auto-injected by BaseGroup.add_agent)
        self._group = None

        # Create message queue (shared across all conversations)
        self.msg_queue = MessageQueueInMemory()
        self._msg_queue_loop = None  # Track which event loop the queue is running in

        # Lazy subscription management: conversation_id -> Subscription
        self._subscriptions = {}
        self._lock = asyncio.Lock()

    def setup_from_agent(self, agent):
        """Setup controller from single_agent - inject required attributes
        
        This method is called by ControllerAgent to inject config, context_engine
        and session into the controller.
        
        Args:
            agent: ControllerAgent instance
            
        Usage (internal, called by ControllerAgent):
            controller = WorkflowController()  # No parameters
            controller.setup_from_agent(single_agent)  # Auto-inject
        """
        # Get agent_config (support both _agent_config and _config.get_agent_config())
        if hasattr(agent, '_agent_config'):
            self._config = agent.agent_config
        elif hasattr(agent, '_config') and hasattr(agent._config, 'get_agent_config'):
            self._config = agent._config.get_agent_config()
        else:
            raise AttributeError(
                f"Agent {agent.__class__.__name__} must have _agent_config "
                "or _config.get_agent_config()"
            )

        # Get context_engine
        if not hasattr(agent, '_context_engine'):
            raise AttributeError(
                f"Agent {agent.__class__.__name__} must have _context_engine"
            )
        self._context_engine = agent._context_engine

    async def _get_or_create_subscription(self, conversation_id: str):
        """Get or create subscription for conversation_id (lazy subscription)
        
        Args:
            conversation_id: Conversation ID
            
        Returns:
            Subscription object
        """
        async with self._lock:
            if conversation_id not in self._subscriptions:
                topic = f"controller_messages_{conversation_id}"
                subscription = self.msg_queue.subscribe(topic)
                subscription.set_message_handler(self._handle_message_wrapper)
                subscription.activate()
                self._subscriptions[conversation_id] = subscription
                logger.info(
                    f"BaseController: Created subscription for "
                    f"conversation_id={conversation_id}, topic={topic}"
                )
            return self._subscriptions[conversation_id]

    async def invoke(self, inputs: Dict, session: Session) -> Dict:
        """Synchronous invocation entry

        Process:
        1. Get conversation_id and ensure subscription exists
        2. Create message
        3. Publish message to conversation-specific queue
        4. Wait for processing result
        """
        # Lazy start message queue with event loop detection
        # If event loop changes (e.g., multiple asyncio.run() calls), restart queue
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError as e:
            logger.error("No running event loop")
            raise build_error(StatusCode.AGENT_CONTROLLER_RUNTIME_ERROR,
                              error_msg="no running event loop") from e
        
        if self._msg_queue_loop is not current_loop:
            # Event loop changed or first time - (re)start message queue
            if self._msg_queue_loop is not None:
                # Event loop changed - recreate everything
                logger.info(
                    f"Event loop changed, recreating message queue "
                    f"(old loop: {id(self._msg_queue_loop)}, "
                    f"new loop: {id(current_loop)})"
                )
                # Stop old queue (best effort, may fail if old loop is closed)
                try:
                    await self.msg_queue.stop()
                except Exception as e:
                    logger.warning(f"Failed to stop old message queue: {e}")
                
                # Recreate message queue (cleanest approach)
                self.msg_queue = MessageQueueInMemory()
                # Clear old subscriptions (they're tied to old loop)
                self._subscriptions.clear()
                # Recreate lock for new loop
                self._lock = asyncio.Lock()
            
            # Start queue in current event loop
            self.msg_queue.start()
            self._msg_queue_loop = current_loop
            logger.debug(f"Message queue started in event loop {id(current_loop)}")
        
        # 1. Get conversation_id
        conversation_id = inputs.get("conversation_id", "default_session")

        # 2. Ensure subscription exists for this conversation
        await self._get_or_create_subscription(conversation_id)

        # 3. Get conversation-specific topic
        topic = f"controller_messages_{conversation_id}"

        # 4. Create event
        event = self.create_message(inputs)

        # 5. Create queue message and publish
        queue_message = InvokeQueueMessage()
        queue_message.payload = {"message": event, "session": session}
        queue_message.response = asyncio.Future()

        # 6. Publish to conversation-specific topic
        await self.msg_queue.produce_message(topic, queue_message)

        # 7. Wait for result
        result = await queue_message.response

        return result if result is not None else {"output": "processed"}

    async def _handle_message_wrapper(self, request: Dict) -> Dict:
        """Message processing wrapper - Automatically called by message queue

        Args:
            request: Dictionary containing event and session
            
        Returns:
            dict: Processing result
        """
        event = request.get("message")
        session = request.get("session")
        try:
            result = await self.handle_event(event, session)
            result_type = type(result)
            has_result = result is not None
            logger.info(
                f"BaseController: handle_event returned: "
                f"{result_type}, {has_result}"
            )
            return result
        except Exception as e:
            error_msg = f"BaseController: handle_event raised exception: {e}"
            logger.error(error_msg, exc_info=True)
            if isinstance(e, BaseError):
                raise e
            else:
                raise build_error(StatusCode.AGENT_CONTROLLER_RUNTIME_ERROR, error_msg=str(e)) from e

    # ===== Abstract methods (developers must implement) =====
    @abstractmethod
    async def handle_event(self, event: Event, session: Session) -> Optional[Dict]:
        """Core method for message processing (must be implemented)

        Args:
            event: Event object
            session: Session context
        Returns:
            Optional[Dict]: Processing result

        Developers implement all business logic here:
        - Dispatch processing based on event.event_type
        - Execute tasks (synchronously or asynchronously)
        - Manage state
        - Handle interruptions
        - If multi-round processing is needed, loop inside this method
        """
        pass

    # ===== Extension methods (developers can optionally override) =====
    def create_message(self, inputs: Dict) -> Event:
        """Create message object (can be overridden)

        Default: Extract content/query from inputs, create user input message
        Supports both string and InteractiveInput via query field
        """
        conversation_id = inputs.get("conversation_id", "default_session")
        user_id = inputs.get("user_id")

        # Unified: get content from query field (supports str or InteractiveInput)
        content = inputs.get("query", "")

        return Event.create_user_event(
            content=content,
            conversation_id=conversation_id,
            user_id=user_id,
            extensions={k: _ for k, _ in inputs.items() if k not in ["query", "conversation_id", "user_id"]}
        )

    async def cleanup_conversation(self, conversation_id: str):
        """Clean up subscription for completed conversation
        
        Args:
            conversation_id: Conversation ID to clean up
        """
        async with self._lock:
            if conversation_id in self._subscriptions:
                topic = f"controller_messages_{conversation_id}"
                subscription = self._subscriptions[conversation_id]
                await subscription.deactivate()
                await self.msg_queue.unsubscribe(topic)
                del self._subscriptions[conversation_id]
                logger.info(
                    f"BaseController: Cleaned up subscription for "
                    f"conversation_id={conversation_id}"
                )

    async def stop(self):
        """Stop controller - Clean up all resources"""
        # Clean up all subscriptions
        for conversation_id, subscription in list(self._subscriptions.items()):
            topic = f"controller_messages_{conversation_id}"
            await subscription.deactivate()
            await self.msg_queue.unsubscribe(topic)
        self._subscriptions.clear()

        # Stop message queue
        await self.msg_queue.stop()

    # ===== Group routing support (auto-injected by BaseGroup.add_agent) =====

    def set_group(self, group):
        """Set group reference (auto-injected by BaseGroup.add_agent)
        
        This method is called automatically when an single_agent is added to a group.
        Developers should not call this method directly.
        
        Args:
            group: The AgentGroup instance this controller's single_agent belongs to
        """
        self._group = group
        logger.debug(
            f"{self.__class__.__name__}: Group reference injected "
            f"(group_id={getattr(group, 'group_id', 'unknown')})"
        )

    async def send_to_agent(
        self,
        agent_id: str,
        event: Event,
        session
    ) -> Any:
        """Send event to specified single_agent (point-to-point)
        
        This method delegates to the group's controller for actual routing.
        Only works when this controller's single_agent is part of a group.
        
        Args:
            agent_id: Target single_agent ID
            event: Event object
            session: Session context
        
        Returns:
            Agent's return result
        
        Raises:
            RuntimeError: If single_agent is not part of a group
        """
        if self._group and hasattr(self._group, 'group_controller'):
            return await self._group.group_controller.send_to_agent(
                event, agent_id, session
            )
        raise RuntimeError(
            f"{self.__class__.__name__}: Cannot send_to_agent('{agent_id}'). "
            "Agent is not part of a group with a controller."
        )

    async def publish(
        self,
        event: Event,
        session
    ) -> List[Any]:
        """Publish event to subscribers (broadcast)
        
        This method delegates to the group's controller for actual routing.
        Only works when this controller's single_agent is part of a group.
        
        Args:
            event: Event object (must have custom_event_type set)
            session: Session context
        
        Returns:
            List of results from all subscribers
        
        Raises:
            RuntimeError: If single_agent is not part of a group
        """
        if self._group and hasattr(self._group, 'group_controller'):
            return await self._group.group_controller.publish(event, session)
        raise RuntimeError(
            f"{self.__class__.__name__}: Cannot publish(). "
            "Agent is not part of a group with a controller."
        )
