# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from __future__ import annotations

"""Task Scheduler Module

This module implements the core functionality for task scheduling and execution, including:
- TaskExecutor: abstract base class that defines the task execution interface
- TaskExecutorRegistry: registry for managing task executors of different types
- TaskScheduler: the task scheduler, responsible for scheduling, running, pausing, and canceling tasks

Workflow:
- Receive tasks ready for execution (status = submitted)
- Obtain the corresponding TaskExecutor from the registry based on task type
- Execute multiple tasks concurrently
- Stream the output generated during task execution
- Update task status according to the output type (completion/interaction/failed)
"""

import asyncio
from abc import abstractmethod, ABC
from dataclasses import dataclass
from typing import AsyncIterator, Callable, Dict, Optional, Tuple, TYPE_CHECKING

from openjiuwen.core.context_engine import ContextEngine
from openjiuwen.core.controller.config import ControllerConfig
from openjiuwen.core.controller.modules.event_queue import EventQueue
from openjiuwen.core.controller.modules.task_manager import TaskManager, TaskFilter
from openjiuwen.core.session.agent import Session
from openjiuwen.core.controller.schema import (EventType, TaskCompletionEvent, TaskInteractionEvent, TaskFailedEvent,
                                               TaskStatus, ControllerOutputChunk, ControllerOutputPayload,
                                               TextDataFrame, Task)
from openjiuwen.core.common.logging import logger
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.exception.codes import StatusCode

if TYPE_CHECKING:
    from openjiuwen.core.single_agent.base import AbilityManager
    from openjiuwen.core.single_agent.schema.agent_card import AgentCard


@dataclass
class TaskExecutorDependencies:
    """Task executor dependencies

    Encapsulates all dependencies required to construct a TaskExecutor instance.
    This reduces parameter passing complexity and improves maintainability.

    Attributes:
        config: Controller configuration
        ability_manager: Ability manager containing available tools, workflows, etc.
        context_engine: Context engine for managing conversation context
        task_manager: Task manager for updating task status
        event_queue: Event queue for publishing task-related events
    """
    config: ControllerConfig
    ability_manager: 'AbilityManager'
    context_engine: ContextEngine
    task_manager: TaskManager
    event_queue: EventQueue


class TaskExecutor(ABC):
    """Task executor abstract base class

    Defines the task execution interface. Different task types need to implement different TaskExecutors.

    Main responsibilities:
    - Execute tasks (execute_ability)
    - Check whether a task can be paused (can_pause)
    - Pause tasks (pause)
    - Check whether a task can be canceled (can_cancel)
    - Cancel tasks (cancel)
    """

    def __init__(self, dependencies: TaskExecutorDependencies):
        """Initialize task executor

        Args:
            dependencies: Task executor dependencies
        """
        self._config = dependencies.config
        self._ability_manager = dependencies.ability_manager
        self._context_engine = dependencies.context_engine
        self._task_manager = dependencies.task_manager
        self._event_queue = dependencies.event_queue

    @abstractmethod
    async def execute_ability(self, task_id: str, session: Session) -> AsyncIterator[ControllerOutputChunk]:
        """Execute task

        Args:
            task_id: Task ID
            session: Session object

        Yields:
            ControllerOutputChunk: Output chunks generated during task execution
        """
        ...

    @abstractmethod
    async def can_pause(self, task_id: str, session: Session) -> Tuple[bool, str]:
        """Check whether the task can be paused

        Args:
            task_id: Task ID
            session: Session object

        Returns:
            Tuple[bool, str]: (whether it can be paused, reason if it cannot)
        """
        ...

    @abstractmethod
    async def pause(self, task_id: str, session: Session) -> bool:
        """Pause task

        Args:
            task_id: Task ID
            session: Session object

        Returns:
            bool: Whether it was successfully paused
        """
        ...

    @abstractmethod
    async def can_cancel(self, task_id: str, session: Session) -> Tuple[bool, str]:
        """Check whether the task can be canceled

        Args:
            task_id: Task ID
            session: Session object

        Returns:
            Tuple[bool, str]: (whether it can be canceled, reason if it cannot)
        """
        ...

    @abstractmethod
    async def cancel(self, task_id: str, session: Session) -> bool:
        """Cancel task

        Args:
            task_id: Task ID
            session: Session object

        Returns:
            bool: Whether it was successfully canceled
        """
        ...


class TaskExecutorRegistry:
    """Task executor registry

    Manages task executors of different types, supporting dynamic registration and retrieval.
    Uses task_type to find the corresponding TaskExecutor builder.
    """

    def __init__(self):
        """Initialize task executor registry"""
        self.task_executor_builders: Dict[
            str,
            Callable[[TaskExecutorDependencies], TaskExecutor]
        ] = {}

    def add_task_executor(
            self,
            task_type: str,
            task_executor_builder: Callable[[TaskExecutorDependencies], TaskExecutor]
    ):
        """Register task executor

        Args:
            task_type: Task type identifier
            task_executor_builder: Task executor builder that receives dependencies
                                    and returns a TaskExecutor instance
        """
        self.task_executor_builders[task_type] = task_executor_builder

    def remove_task_executor(self, task_type: str):
        """Remove task executor

        Args:
            task_type: Task type identifier
        """
        if task_type in self.task_executor_builders:
            del self.task_executor_builders[task_type]

    def get_task_executor(
            self,
            task_type: str,
            dependencies: TaskExecutorDependencies
    ) -> TaskExecutor:
        """Get task executor instance

        Args:
            task_type: Task type identifier
            dependencies: Task executor dependencies

        Returns:
            TaskExecutor: Task executor instance

        Raises:
            Exception: If the task type is not registered
        """
        executor_builder = self.task_executor_builders.get(task_type, None)
        if executor_builder is None:
            raise build_error(StatusCode.AGENT_CONTROLLER_TASK_EXECUTION_ERROR, error_msg="task executor not found")
        else:
            return executor_builder(dependencies)


class TaskScheduler:
    """Task scheduler

    Responsible for scheduling, executing, pausing, and canceling tasks.
    Supports concurrent execution of multiple tasks and streaming of task execution output.

    Workflow:
    1. Periodically scan for tasks to be executed (status = submitted)
    2. Execute multiple tasks concurrently
    3. Stream output generated during task execution
    4. Update task status based on output type

    Attributes:
        _sessions: Session dictionary, session_id -> Session
        _running_tasks: Dictionary of running tasks, task_id -> (TaskExecutor, asyncio.Task)
        _running: Whether the scheduler is running
        _scheduler_task: Scheduler background task
        _lock: Lock used for synchronized access
    """

    def __init__(
            self,
            config: ControllerConfig,
            task_manager: TaskManager,
            context_engine: ContextEngine,
            ability_manager: 'AbilityManager',
            event_queue: EventQueue,
            card: AgentCard
    ):
        """Initialize task scheduler

        Args:
            config: Controller configuration
            task_manager: Task manager
            context_engine: Context engine
            ability_manager: Ability manager
            event_queue: Event queue
            card: Agent card (used to publish events)
        """
        self._config = config
        self._task_manager = task_manager
        self._context_engine = context_engine
        self._ability_manager = ability_manager
        self._event_queue = event_queue
        self._task_executor_registry = TaskExecutorRegistry()
        self._sessions: Dict[str, Session] = {}
        self._card = card

        # Scheduler running state
        self._running = False
        self._scheduler_task: Optional[asyncio.Task] = None

        # Running tasks: task_id -> (TaskExecutor, asyncio.Task)
        self._running_tasks: Dict[str, Tuple[Optional[TaskExecutor], Optional[asyncio.Task]]] = {}

        # Lock used for synchronized access
        self._lock = asyncio.Lock()

        # Event-driven wakeup for new SUBMITTED tasks
        self._submit_event = asyncio.Event()

    @property
    def config(self) -> ControllerConfig:
        """Get configuration"""
        return self._config

    @config.setter
    def config(self, config: ControllerConfig):
        """Update configuration"""
        self._config = config

    @property
    def sessions(self) -> Dict[str, Session]:
        """Get session dictionary"""
        return self._sessions

    @property
    def task_manager(self) -> Optional[TaskManager]:
        """Get task manager"""
        return self._task_manager

    @property
    def task_executor_registry(self):
        """Get task executor registry"""
        return self._task_executor_registry

    def notify_task_submitted(self) -> None:
        """Wake up the schedule loop.

        Called synchronously by TaskManager when a task
        enters SUBMITTED status. Sets the internal
        asyncio.Event so the scheduler picks it up
        without waiting for the poll interval.
        """
        self._submit_event.set()

    async def _handle_task_execution_failure(self, task_id: str, session: Session, error_message: str):
        """Handle task failure by updating status and publishing failure event

        Args:
            task_id: Task ID
            session: Session object
            error_message: Error message to publish
        """
        # Update task status to FAILED
        await self._task_manager.update_task_status(task_id, TaskStatus.FAILED, error_message=error_message)

        # Publish failure event
        failed_chunk = ControllerOutputChunk(
            index=0,
            type="controller_output",
            payload=ControllerOutputPayload(
                type=EventType.TASK_FAILED,
                data=[TextDataFrame(type="text", text=error_message)]
            )
        )
        await self._publish_task_event(task_id, session, failed_chunk)

    async def _execute_task_wrapper(self, task_id: str, session: Session):
        """Task execution wrapper

        Wraps execute_task to ensure exceptions are captured and handled correctly.

        Args:
            task_id: Task ID
            session: Session object
        """
        try:
            await asyncio.wait_for(
                self.execute_task(task_id, session),
                timeout=self._config.task_timeout
            )

        except asyncio.TimeoutError:
            # Task exceeded configured task_timeout
            error_msg = f"Task timeout after {self._config.task_timeout} seconds"
            logger.error(f"Task {task_id} {error_msg}")
            await self._handle_task_execution_failure(task_id, session, error_msg)

        except asyncio.CancelledError:
            logger.info(
                f"Task {task_id} cancelled (likely by pause_task/cancel_task), "
                f"continuing gracefully to not affect other tasks"
            )

        except Exception as e:
            logger.error(f"Task {task_id} execution failed: {e}", exc_info=True)
            await self._handle_task_execution_failure(task_id, session, str(e))

        finally:
            # Cleanup: remove from running tasks (if not already removed by pause/cancel)
            async with self._lock:
                if task_id in self._running_tasks:
                    del self._running_tasks[task_id]

            # Critical: Check if all tasks are done and send completion signal
            # This MUST succeed, otherwise Controller will hang forever
            await self._ensure_session_completion_signal(session.get_session_id())

    async def execute_task(self, task_id: str, session: Session):
        """Execute task

        Execution flow:
        1. Get task and validate
        2. Create task executor based on task type
        3. Update task status to WORKING
        4. Execute task and stream output
        5. Handle completion/interaction/failure events

        Args:
            task_id: Task ID
            session: Session object
        """
        # 1. Get task object
        tasks = await self._task_manager.get_task(task_filter=TaskFilter(task_id=task_id))
        if not tasks:
            logger.error(f"Task {task_id} not found")
            raise build_error(
                StatusCode.AGENT_CONTROLLER_TASK_EXECUTION_ERROR,
                error_msg=f"task {task_id} not found"
            )
        task = tasks[0]

        logger.info(f"Executing task {task_id} (type: {task.task_type})")

        # 2. Create TaskExecutor
        dependencies = TaskExecutorDependencies(
            config=self._config,
            ability_manager=self._ability_manager,
            context_engine=self._context_engine,
            task_manager=self._task_manager,
            event_queue=self._event_queue
        )
        executor = self._task_executor_registry.get_task_executor(
            task_type=task.task_type,
            dependencies=dependencies
        )

        # Update running task records (add executor)
        async with self._lock:
            if task_id in self._running_tasks:
                _, exec_task = self._running_tasks[task_id]
                self._running_tasks[task_id] = (executor, exec_task)

        # 3. Update task status to WORKING
        await self._task_manager.update_task_status(task_id, TaskStatus.WORKING)

        # 4. Execute task in streaming mode
        async for chunk in executor.execute_ability(task_id, session):
            # 4.1 Write to session stream (so ControllerAgent can read)
            await session.write_stream(chunk)

            # 4.2 Check output type and decide whether to stop the task
            if chunk.payload and chunk.payload.type:
                payload_type = chunk.payload.type

                # Task completed
                if payload_type == EventType.TASK_COMPLETION:
                    logger.info(f"Task {task_id} completed")
                    await self._task_manager.update_task_status(task_id, TaskStatus.COMPLETED)
                    await self._publish_task_event(task_id, session, chunk)
                    break

                # Task requires interaction
                elif payload_type == EventType.TASK_INTERACTION:
                    logger.info(f"Task {task_id} requires interaction")
                    await self._task_manager.update_task_status(task_id, TaskStatus.INPUT_REQUIRED)
                    await self._publish_task_event(task_id, session, chunk)
                    break

                # Task failed
                elif payload_type == EventType.TASK_FAILED:
                    logger.error(f"Task {task_id} failed")
                    await self._task_manager.update_task_status(task_id, TaskStatus.FAILED)
                    await self._publish_task_event(task_id, session, chunk)
                    break

                # Processing (continue execution)
                elif payload_type == "processing":
                    continue

    async def _are_all_tasks_completed(self, session_id: str) -> bool:
        """Check if all tasks for a session are in terminal status

        Terminal status includes:
        - COMPLETED: task finished successfully
        - FAILED: task failed with error
        - CANCELED: task was cancelled
        - PAUSED: task was paused
        - INPUT_REQUIRED: task waiting for user input
        - WAITING: task waiting for dependencies (included so failed tasks don't block completion)

        Args:
            session_id: Session ID

        Returns:
            bool: True if all tasks are completed, False otherwise
        """
        try:
            session_tasks = await self._task_manager.get_task(task_filter=TaskFilter(session_id=session_id))
            if not session_tasks:
                logger.warning(f"No tasks found for session {session_id}")
                return True

            # Check if any task is still actively working or submitted
            active_states = {TaskStatus.SUBMITTED, TaskStatus.WORKING}
            has_active_tasks = any(task.status in active_states for task in session_tasks)

            # If no active tasks, consider all tasks as "done" (even if some are WAITING)
            if not has_active_tasks:
                logger.info(f"No active tasks for session {session_id}")
                return True

            return False
        except Exception as e:
            logger.error(f"Error checking task completion status: {e}", exc_info=True)
            return False

    async def ensure_session_completion_signal(self, session_id: str):
        """Public wrapper for _ensure_session_completion_signal.

        Called by Controller.stream() after publish_event returns,
        so that rounds with no new tasks can emit the completion signal.
        """
        await self._ensure_session_completion_signal(session_id)

    async def _ensure_session_completion_signal(self, session_id: str):
        """Ensure completion signal is sent if all tasks are done

        Design principles:
        1. Check if all tasks completed
        2. Send completion signal
        3. Never raise exceptions

        Args:
            session_id: Session ID
        """
        if self._config.suppress_completion_signal:
            return

        try:
            # Check if all tasks are done
            if not await self._are_all_tasks_completed(session_id):
                logger.info("not all tasks completed, continue")
                return

            logger.info(f"All tasks completed for session {session_id}, sending completion signal")

            # Get session object
            session = self._sessions.get(session_id)
            if not session:
                logger.warning(f"Session {session_id} not found, cannot send completion signal")
                return

            # Build and send completion message
            completion_chunk = ControllerOutputChunk(
                index=0,
                type="controller_output",
                payload=ControllerOutputPayload(
                    type="all_tasks_processed",
                    data=[TextDataFrame(type="text", text="All tasks have been successfully processed")],
                ),
                last_chunk=True
            )

            await session.write_stream(completion_chunk)
            logger.info(f"Completion signal sent for session {session_id}")

        except Exception as e:
            logger.error(f"Unexpected error in _ensure_session_completion_signal: {e}", exc_info=True)

    async def _publish_task_event(
            self,
            task_id: str,
            session: Session,
            chunk: ControllerOutputChunk
    ):
        """Publish task event (auto-recognize type from chunk)

        Automatically determines event type based on chunk.payload.type, extracts data and
        constructs the corresponding event object.

        Args:
            task_id: Task ID
            session: Session Object
            chunk: Output chunk (contains event type and data)
        """
        if not chunk.payload or not chunk.payload.type:
            logger.error(f"Invalid chunk for task {task_id}: missing payload or type")
            return

        tasks = await self._task_manager.get_task(task_filter=TaskFilter(task_id=task_id))
        if not tasks:
            logger.error(f"Task {task_id} not found in TaskManager")
            return
        task = tasks[0]
        payload_type = chunk.payload.type
        payload_data = chunk.payload.data if chunk.payload else []
        payload_metadata = chunk.payload.metadata

        # Automatically construct event based on payload type
        if payload_type == EventType.TASK_COMPLETION:
            event = TaskCompletionEvent(
                task_result=payload_data,
                task=task,
                metadata=payload_metadata,
            )
        elif payload_type == EventType.TASK_INTERACTION:
            event = TaskInteractionEvent(
                interaction=payload_data,
                task=task,
                metadata=payload_metadata,
            )
        elif payload_type == EventType.TASK_FAILED:
            error_msg = payload_data[0].text if payload_data else "Unknown error"
            if task:
                task.error_message = error_msg
            event = TaskFailedEvent(
                error_message=error_msg,
                task=task,
                metadata=payload_metadata,
            )
        else:
            logger.error(f"Unsupported payload type: {payload_type}")
            return

        # Merge task.metadata into event.metadata so
        # that _handler_round_id propagates correctly.
        if task.metadata:
            if event.metadata is None:
                event.metadata = {}
            event.metadata.update(task.metadata)

        await self._event_queue.publish_event(
            self._card.id, session, event
        )
        logger.info(f"Published {payload_type} for task {task_id}")

    async def pause_task(self, task_id: str) -> bool:
        """Pause task

        Can be called directly by EventHandler in callbacks.

        Execution flow:
            1. Check whether the current task is running; if not, return directly
            2. Get the executor of the current task and call can_pause; if it cannot be paused,
                return the reason; otherwise continue
            3. After calling executor.pause, cancel the corresponding asyncio.Task of the current task
            4. Update the task status to PAUSED in task_manager

        Args:
            task_id: Task ID

        Returns:
            bool: Whether it was successfully paused
        """
        tasks = await self._task_manager.get_task(task_filter=TaskFilter(task_id=task_id))
        if not tasks:
            logger.error(f"Task {task_id} not found in TaskManager")
            return False
        task = tasks[0]

        session = self._sessions.get(task.session_id)
        if not session:
            logger.error(f"Session {task.session_id} not found for task {task_id}")
            return False

        async with self._lock:
            # Check whether the task is running
            if task_id not in self._running_tasks:
                logger.warning(f"Task {task_id} is not running, cannot pause")
                return False

            # Get task and executor
            executor, exec_task = self._running_tasks[task_id]

        # Check whether it can be paused
        if executor:
            try:
                can_pause, reason = await executor.can_pause(task_id, session)
                if not can_pause:
                    logger.warning(f"Task {task_id} cannot be paused, reason: {reason}")
                    return False

                # Execute executor pause logic
                await executor.pause(task_id, session)
            except Exception as e:
                logger.error(f"Error pausing task {task_id}: {e}", exc_info=True)
                return False

        async with self._lock:
            if task_id not in self._running_tasks:
                logger.warning(f"Task {task_id} was already removed during pause operation")
                return False

            _, exec_task = self._running_tasks[task_id]

            if exec_task and not exec_task.done():
                exec_task.cancel()

        # Update task status
        await self._task_manager.update_task_status(task_id, TaskStatus.PAUSED)

        if exec_task:
            try:
                await exec_task
            except asyncio.CancelledError:
                logger.info(f"Unexpected: CancelledError propagated from {task_id}")
            except Exception as e:
                logger.error(f"Error cancelling task {task_id}: {e}", exc_info=True)
                return False

        logger.info(f"Task {task_id} paused successfully")
        return True

    async def cancel_task(self, task_id: str) -> bool:
        """Cancel task

        Can be called directly by EventHandler in callbacks.

        Execution flow:
        1. Check whether the current task is running; if not, return directly
        2. Get the executor of the current task and call can_cancel; if it cannot be canceled,
            return the reason; otherwise continue
        3. After calling executor.cancel, cancel the corresponding asyncio.Task of the current task
        4. Update the task status to CANCELED in task_manager

        Args:
            task_id: Task ID

        Returns:
            bool: Whether it was successfully canceled
        """
        tasks = await self._task_manager.get_task(task_filter=TaskFilter(task_id=task_id))
        if not tasks:
            logger.error(f"Task {task_id} not found in TaskManager")
            return False
        task = tasks[0]

        session = self._sessions.get(task.session_id)
        if not session:
            logger.error(f"Session {task.session_id} not found for task {task_id}")
            return False

        async with self._lock:
            # Check whether the task is running
            if task_id not in self._running_tasks:
                logger.warning(f"Task {task_id} is not running, cannot cancel")
                return False

            # Get task executor
            executor, exec_task = self._running_tasks[task_id]

        # Check whether it can be canceled
        if executor:
            try:
                can_cancel, reason = await executor.can_cancel(task_id, session)
                if not can_cancel:
                    logger.warning(f"Task {task_id} cannot be cancelled: {reason}")
                    return False

                # Execute executor cancel logic
                await executor.cancel(task_id, session)
            except Exception as e:
                logger.error(f"Error cancelling task {task_id}: {e}", exc_info=True)
                return False

        async with self._lock:
            if task_id not in self._running_tasks:
                logger.warning(f"Task {task_id} was already removed during cancel operation")
                return False

            _, exec_task = self._running_tasks[task_id]

            if exec_task and not exec_task.done():
                exec_task.cancel()

        # Update task status
        await self._task_manager.update_task_status(task_id, TaskStatus.CANCELED)

        if exec_task:
            try:
                await exec_task
            except asyncio.CancelledError:
                logger.info(f"Unexpected: CancelledError propagated from {task_id}")
            except Exception as e:
                logger.error(f"Error cancelling task {task_id}: {e}", exc_info=True)
                return False

        logger.info(f"Task {task_id} cancelled successfully")
        return True

    async def schedule(self):
        """Background scheduling loop

        Periodically scans TaskManager and concurrently executes tasks with status SUBMITTED.
        Uses create_task to keep the loop active and gather for graceful cleanup when stopping.

        Execution flow:
        1. Get all tasks whose session_id is in sessions and whose status is submitted
        2. Use create_task to start all new tasks non-blockingly and stream execution output
        3. Sleep briefly before the next scan
        4. When stopping, wait for all running tasks to finish
        """
        logger.info("TaskScheduler schedule loop started")
        while self._running:
            try:
                # 1. Get tasks to execute
                submitted_tasks = await self._task_manager.get_task(task_filter=TaskFilter(status=TaskStatus.SUBMITTED))

                # 2. Concurrently start all new tasks (non-blocking)
                for task in submitted_tasks:
                    # Check whether session exists
                    session = self._sessions.get(task.session_id)
                    if not session:
                        logger.warning(
                            f"Task {task.task_id} session {task.session_id} not found, skipping"
                        )
                        continue

                    async with self._lock:
                        if len(self._running_tasks) >= self._config.max_concurrent_tasks:
                            logger.warning(f"Reached max concurrent tasks limit ({self._config.max_concurrent_tasks}), "
                                        "waiting for next schedule")
                            break

                        # Check whether it is already running
                        if task.task_id in self._running_tasks:
                            continue

                        # Start non-blockingly using create_task
                        exec_task = asyncio.create_task(
                            self._execute_task_wrapper(task.task_id, session)
                        )

                        # Record in running tasks
                        self._running_tasks[task.task_id] = (None, exec_task)

                    logger.info(f"Task {task.task_id} ({task.task_type}) started")

                # 3. Wait for new SUBMITTED task or timeout
                self._submit_event.clear()
                try:
                    await asyncio.wait_for(
                        self._submit_event.wait(),
                        timeout=(
                            self._config.schedule_interval
                        ),
                    )
                except asyncio.TimeoutError:
                    pass

            except asyncio.CancelledError:
                logger.info("TaskScheduler schedule loop cancelled")
                break

            except Exception as e:
                logger.error(f"Error in schedule loop: {e}", exc_info=True)
                await asyncio.sleep(1)

        # 4. When stopping, use gather to wait for all tasks to complete
        await self._wait_all_tasks_complete()

        logger.info("TaskScheduler schedule end")

    async def _wait_all_tasks_complete(self):
        """Wait for all running tasks to complete

        Uses gather to gracefully wait for all tasks.
        """
        async with self._lock:
            if not self._running_tasks:
                return

            logger.info(f"Waiting for {len(self._running_tasks)} running tasks to complete...")

            # Collect all asyncio.Task objects
            tasks = [
                exec_task
                for _, exec_task in self._running_tasks.values()
                if exec_task is not None
            ]

        if tasks:
            # Use gather to wait for all tasks to complete
            results = await asyncio.gather(*tasks, return_exceptions=True)

            # Collect statistics
            success_count = sum(1 for r in results if not isinstance(r, Exception))
            error_count = len(results) - success_count

            logger.info(
                f"All tasks completed: {success_count} succeeded, {error_count} failed"
            )

        # Cleanup
        async with self._lock:
            self._running_tasks.clear()

    async def start(self):
        """Start task scheduler

        Starts the background scheduling task and begins periodic scanning and execution of pending tasks.
        """
        if self._running:
            logger.warning(f"TaskScheduler is already running")
            return
        self._running = True
        self._scheduler_task = asyncio.create_task(self.schedule())
        logger.info(f"TaskScheduler started")

    async def stop(self):
        """Stop task scheduler

        Stops the background scheduling task and stops scanning and executing tasks.
        Cancels all running tasks to ensure clean shutdown.
        """
        if not self._running:
            logger.warning(f"TaskScheduler is not running")
            return

        self._running = False

        # Cancel all running tasks to prevent hanging on stream writes
        if self._running_tasks:
            logger.info(f"Cancelling {len(self._running_tasks)} running tasks before stopping...")
            async with self._lock:
                for task_id, (executor, exec_task) in list(self._running_tasks.items()):
                    if exec_task and not exec_task.done():
                        exec_task.cancel()
                        logger.info(f"Cancelled task {task_id} successfully")

        if self._scheduler_task:
            self._scheduler_task.cancel()
            try:
                await self._scheduler_task
            except asyncio.CancelledError:
                pass

        logger.info(f"TaskScheduler stopped")
