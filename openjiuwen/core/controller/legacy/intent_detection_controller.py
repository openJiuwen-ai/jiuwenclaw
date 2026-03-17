# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Intent Detection Controller - Intent detection and task management"""

import asyncio
from abc import abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Optional

from openjiuwen.core.common.utils.message_utils import MessageUtils
from openjiuwen.core.controller.legacy.controller import BaseController
from openjiuwen.core.controller.legacy.event.event import Event
from openjiuwen.core.controller.legacy.task.task import Task, TaskStatus
from openjiuwen.core.common.logging import logger
from openjiuwen.core.session import InteractiveInput
from openjiuwen.core.session.agent import Session
from openjiuwen.core.session.stream import OutputSchema


@dataclass
class RunningTaskInfo:
    """Running task information"""
    task: Task
    asyncio_task: asyncio.Task
    target_id: str
    start_time: float


class TaskQueue:
    """Task queue - Manages running tasks
    
    Design reference: MessageQueueInMemory
    - Lightweight memory management
    - conversation_id as key
    - Support cancellation mechanism
    """
    
    def __init__(self):
        # conversation_id -> RunningTaskInfo
        self._running_tasks: Dict[str, RunningTaskInfo] = {}
        self._lock = asyncio.Lock()
    
    async def register_task(
        self,
        conversation_id: str,
        task: Task,
        asyncio_task: asyncio.Task,
        target_id: str
    ):
        """Register running task"""
        async with self._lock:
            self._running_tasks[conversation_id] = RunningTaskInfo(
                task=task,
                asyncio_task=asyncio_task,
                target_id=target_id,
                start_time=asyncio.get_event_loop().time()
            )
            logger.info(
                f"TaskQueue: Registered task for {conversation_id}, "
                f"target={target_id}"
            )
    
    async def cancel_running_task(self, conversation_id: str) -> bool:
        """Cancel running task
        
        Returns:
            bool: Whether cancellation was successful
        """
        async with self._lock:
            info = self._running_tasks.get(conversation_id)
            if not info:
                return False
            
            # Cancel asyncio.Task
            if not info.asyncio_task.done():
                logger.info(
                    f"TaskQueue: Cancelling task for {conversation_id}, "
                    f"target={info.target_id}"
                )
                info.asyncio_task.cancel()
                
                # Wait for cancellation to complete
                try:
                    await info.asyncio_task
                except asyncio.CancelledError:
                    logger.info("TaskQueue: Task cancelled successfully")
                except Exception as e:
                    logger.warning(f"TaskQueue: Cancel error: {e}")
                
                return True
            return False
    
    async def unregister_task(self, conversation_id: str):
        """Unregister task"""
        async with self._lock:
            if conversation_id in self._running_tasks:
                del self._running_tasks[conversation_id]
                logger.info(
                    f"TaskQueue: Unregistered task for {conversation_id}"
                )
    
    def find_task(self, conversation_id: str) -> Optional[RunningTaskInfo]:
        """Find running task"""
        return self._running_tasks.get(conversation_id)
    
    def has_running_task(self, conversation_id: str) -> bool:
        """Check if has running task"""
        return conversation_id in self._running_tasks


class IntentType(Enum):
    """Intent type"""
    ExecNewTask = "exec_new_task"  # Execute new task
    ResumeTask = "resume_task"  # Resume task
    CancelTask = "cancel_task"  # Cancel task
    DefaultResponse = "default_response"  # Return default response text
    Unknown = "unknown"  # Unknown intent


@dataclass
class Intent:
    """Intent object - Encapsulates intent detection result"""
    intent_type: IntentType  # Intent type
    task: Optional[Task] = None  # Associated task object
    workflow: Optional[Any] = None  # Selected workflow (use Any to avoid circular import)
    metadata: Dict[str, Any] = None  # Extended metadata

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}


class IntentDetectionController(BaseController):
    """Intent Detection Controller - Provides task management and intent routing capabilities
    
    Core responsibilities:
    1. Intent detection: Recognize user intent through intent_detection()
    2. Message routing: Route to different handlers based on Intent type
    3. Task execution: Call exec_task() to execute tasks
    4. Interruption handling: Call interrupt_task() to handle interruptions
    5. Real-time interruption: Cancel running tasks when new request arrives
    """

    def __init__(self, config=None, context_engine=None, session=None):
        """Initialize Intent Detection Controller
        
        Args:
            config: Agent configuration (optional, can be injected later)
            context_engine: Context engine (optional, can be injected later)
            session: Agent-level Session (optional, can be injected later)
            
        Note:
            If parameters are not provided, they will be injected by
            ControllerAgent via setup_from_agent()
        """
        super().__init__(config, context_engine, session)
        
        # Initialize task queue for managing running tasks
        self.task_queue = TaskQueue()
        
        # Track currently processing handlers (conversation_id -> asyncio.Task)
        # This tracks at handle_event level, earlier than TaskQueue
        self._processing_handlers: Dict[str, asyncio.Task] = {}
        self._handler_lock = asyncio.Lock()

    async def invoke(self, inputs: Dict, session: Session) -> Dict:
        """Override invoke to support real-time interruption
        
        Key mechanism for real-time interruption:
        1. When new request arrives, cancel currently processing handler first
        2. Cancelled handler will quickly return {"status": "cancelled"}
        3. After handler returns, Subscription immediately processes new message
        
        Args:
            inputs: Input dictionary containing query and conversation_id
            session: Session context
            
        Returns:
            Processing result
        """
        conversation_id = inputs.get("conversation_id", "default_session")
        
        # Key: Cancel processing handler BEFORE sending message to queue
        # This tracks earlier than TaskQueue (which only registers in exec_task)
        async with self._handler_lock:
            if conversation_id in self._processing_handlers:
                old_handler = self._processing_handlers[conversation_id]
                if not old_handler.done():
                    logger.info(
                        f"[IntentDetectionController] New request received, "
                        f"cancelling processing handler for {conversation_id}"
                    )
                    old_handler.cancel()
                    # Don't wait here - let it be cancelled asynchronously
        
        # Also check TaskQueue for running workflow tasks
        if self.task_queue.has_running_task(conversation_id):
            logger.info(
                f"[IntentDetectionController] Also cancelling workflow task "
                f"for {conversation_id}"
            )
            await self.task_queue.cancel_running_task(conversation_id)
        
        # Call parent's invoke (sends message to queue)
        return await super().invoke(inputs, session)

    async def handle_event(self, event: Event, session: Session) -> Dict:
        """Standard message processing flow: Intent detection -> Route processing
        
        Supports real-time interruption:
        - Registers current handler task at start
        - Can be cancelled by new request in invoke()
        - Returns cancelled status if interrupted
        
        Args:
            event: Event object
            session: Session context
            
        Returns:
            Processing result
        """
        conversation_id = event.source.conversation_id
        current_task = asyncio.current_task()
        
        # Register current handler task for cancellation tracking
        async with self._handler_lock:
            self._processing_handlers[conversation_id] = current_task
            logger.debug(
                f"[IntentDetectionController] Registered handler for {conversation_id}"
            )
        
        try:
            # 1. Intent detection
            intent = await self.intent_detection(event, session)

            await MessageUtils.add_user_message(
                event.get_display_content(), self._context_engine, session
            )

            # 2. Route processing based on intent type
            if intent.intent_type == IntentType.ExecNewTask:
                result = await self._handle_new_task(event, intent, session)
            elif intent.intent_type == IntentType.ResumeTask:
                result = await self._handle_resume(event, intent, session)
            elif intent.intent_type == IntentType.CancelTask:
                result = await self._handle_cancel(event, intent, session)
            elif intent.intent_type == IntentType.DefaultResponse:
                result = await self._handle_default_response(event, intent, session)
            else:
                result = await self._handle_unknown_intent(event, intent, session)

            return result
            
        except asyncio.CancelledError:
            # Handler was cancelled by new request - return cancelled status
            logger.info(
                f"[IntentDetectionController] Handler cancelled for {conversation_id}"
            )
            return {"status": "cancelled", "conversation_id": conversation_id}
            
        finally:
            # Unregister handler task
            async with self._handler_lock:
                if conversation_id in self._processing_handlers:
                    if self._processing_handlers[conversation_id] is current_task:
                        del self._processing_handlers[conversation_id]
                        logger.debug(
                            f"[IntentDetectionController] Unregistered handler "
                            f"for {conversation_id}"
                        )

    async def _handle_new_task(
            self,
            event: Event,
            intent: Intent,
            session: Session
    ) -> Dict:
        """Handle new task: Update state -> Execute
        
        Uses synchronous execution mode because:
        - Users expect invoke() to return results
        - Simplifies implementation, avoids extra queue waiting
        
        Args:
            event: Event object
            intent: Intent object
            session: Session context
            
        Returns:
            dict: Execution result
        """
        task = intent.task
        if not task:
            return {"status": "error", "message": "Task not found in intent"}

        task.status = TaskStatus.PENDING

        # Execute task directly
        logger.info(f"Handling new task: task_id={task.task_id}")
        result = await self.exec_task(event.content, task, session)
        return result

    async def _handle_resume(
            self,
            event: Event,
            intent: Intent,
            session: Session
    ) -> Dict:
        """Handle task resumption: Update input -> Execute
        
        Key: Must create InteractiveInput with new user input to update task parameters
        
        Args:
            event: Event object
            intent: Intent object
            session: Session context
            
        Returns:
            dict: Execution result
        """
        task = intent.task
        if not task:
            return {"status": "error", "message": "Task not found in intent"}

        # Task status should already be INTERRUPTED
        if task.status != TaskStatus.INTERRUPTED:
            logger.warning(
                f"Resuming task with unexpected status: {task.status}"
            )

        logger.info(f"Handling resume task: task_id={task.task_id}")

        # Get target workflow's interrupted component_id
        workflow_id = task.input.target_id
        state = session.get_state("workflow_controller")
        target_component_id = "questioner"  # Default value

        if state:
            state_key = workflow_id.replace('.', '_')
            interrupted_tasks = state.get("interrupted_tasks", {})
            interrupted_info = interrupted_tasks.get(state_key)
            if interrupted_info:
                target_component_id = interrupted_info.get(
                    "component_id",
                    "questioner"
                )

        logger.info(
            f"Target workflow interrupted component_id: {target_component_id}"
        )

        # Check if InteractiveInput is already provided
        if (hasattr(event.content, 'interactive_input') and 
                event.content.interactive_input is not None):
            provided_input = event.content.interactive_input
            logger.info(
                f"Provided InteractiveInput: {provided_input}"
            )

            # Check if provided input's component_id matches target workflow
            if provided_input.user_inputs:
                provided_keys = list(provided_input.user_inputs.keys())
                
                # Normalize target_component_id to list for unified handling
                target_ids = (
                    target_component_id if isinstance(target_component_id, list)
                    else [target_component_id]
                )
                
                # Check if any provided key matches any target component
                matches = any(key in target_ids for key in provided_keys)
                
                if not matches:
                    # Mismatch: Remap user input value to first target component
                    user_value = list(provided_input.user_inputs.values())[0]
                    logger.info(
                        f"Component ID mismatch: provided={provided_keys}, "
                        f"target={target_component_id}. "
                        f"Remapping user value '{user_value}' to "
                        f"target component."
                    )
                    interactive_input = InteractiveInput()
                    interactive_input.update(target_ids[0], user_value)
                else:
                    # Match: Use provided input directly
                    interactive_input = provided_input
            else:
                # No user_inputs, use provided input directly
                interactive_input = provided_input
        else:
            # Create InteractiveInput from user query text
            if hasattr(event.content, 'query'):
                query_text = event.content.query
            else:
                query_text = ""

            # Normalize target_component_id to list for unified handling
            target_ids = (
                target_component_id if isinstance(target_component_id, list)
                else [target_component_id]
            )
            
            interactive_input = InteractiveInput()
            # Only update the first interrupted component with user's text input
            interactive_input.update(target_ids[0], query_text)
            logger.info(
                f"Created InteractiveInput for resume: "
                f"component_id={target_component_id}, query={query_text}"
            )

        # Key: Update task input parameters to InteractiveInput
        task.input.arguments = interactive_input

        # Execute task (resume)
        result = await self.exec_task(event.content, task, session)
        return result

    async def _handle_cancel(
            self,
            event: Event,
            intent: Intent,
            session: Session
    ) -> Dict:
        """Handle task cancellation
        
        Args:
            event: Event object
            intent: Intent object
            session: Session context
            
        Returns:
            dict: Cancellation result
        """
        task = intent.task
        if not task:
            return {"status": "error", "message": "Task not found in intent"}

        task.status = TaskStatus.CANCELLED

        logger.info(f"Handling cancel task: task_id={task.task_id}")

        return {"status": "cancelled", "task_id": task.task_id}

    async def _handle_default_response(
            self,
            event: Event,
            intent: Intent,
            session: Session
    ) -> Dict:
        """Handle default response when no task could be detected
        
        Args:
            event: Event object
            intent: Intent object (with default_response_text in metadata)
            session: Session context
            
        Returns:
            dict: Default response result
        """
        default_text = intent.metadata.get("default_response_text", "")
        logger.info(f"Returning default response: {default_text}")

        # Construct workflow_final frame payload (same format as End component)
        final_payload = {
            "response": default_text,
            "output": {}
        }

        # Write workflow_final frame to stream for streaming mode
        workflow_final = OutputSchema(
            type="workflow_final",
            index=0,
            payload=final_payload
        )
        await session.write_stream(workflow_final)

        return {
            "status": "default_response",
            "output": {"answer": default_text},
            "result_type": "answer"
        }

    async def _handle_unknown_intent(
            self,
            event: Event,
            intent: Intent,
            session: Session
    ) -> Dict:
        """Handle unknown intent
        
        Args:
            event: Event object
            intent: Intent object
            session: Session context
            
        Returns:
            dict: Error result
        """
        logger.warning(f"Unknown intent type: {intent.intent_type}")
        return {
            "status": "error",
            "message": f"Unknown intent type: {intent.intent_type}"
        }

    # ===== Abstract methods (subclasses must implement) =====

    @abstractmethod
    async def intent_detection(
            self,
            event: Event,
            session: Session
    ) -> Intent:
        """Intent detection (subclasses must implement)
        
        Subclasses need to implement:
        - Select execution target (e.g. workflow, tool)
        - Check interruption state
        - Return Intent object
        
        Args:
            event: Event object
            session: Session context
            
        Returns:
            Intent object
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} must implement intent_detection()"
        )

    @abstractmethod
    async def exec_task(
            self,
            message_content: Any,
            task: Task,
            session: Session
    ) -> Dict:
        """Execute task (subclasses must implement)
        
        Subclasses need to implement:
        - Decide execution method based on task.status (new/resume)
        - Call session to execute workflow/tool
        - Handle execution results and exceptions
        
        Args:
            message_content: Message content
            task: Task object
            session: Session context
            
        Returns:
            Execution result
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} must implement exec_task()"
        )

    @abstractmethod
    async def interrupt_task(
            self,
            task: Task,
            session: Session
    ) -> Dict:
        """Interrupt task (subclasses must implement)
        
        Subclasses need to implement:
        - Save interruption state
        - Return interruption information
        
        Args:
            task: Task object
            session: Session context
            
        Returns:
            Interruption information
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} must implement interrupt_task()"
        )
