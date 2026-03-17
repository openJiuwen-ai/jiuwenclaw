# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from __future__ import annotations

"""Controller Module

Main classes included:
- ControllerConfig: controller configuration
- Controller: controller implementation

The controller is responsible for handling events, managing task lifecycle,
and performing intent recognition and processing.
"""

import asyncio
from typing import AsyncIterator, Optional, List, Callable, TYPE_CHECKING

from openjiuwen.core.context_engine import ContextEngine
from openjiuwen.core.controller.schema import ControllerOutput, ControllerOutputChunk, EventType, InputEvent
from openjiuwen.core.controller.modules import (
    TaskManager,
    TaskManagerState,
    TaskScheduler,
    TaskExecutor,
    TaskExecutorDependencies,
    EventQueue,
    EventHandler
)
from openjiuwen.core.controller.config import ControllerConfig
from openjiuwen.core.session.agent import Session
from openjiuwen.core.session.stream.base import BaseStreamMode, StreamMode
from openjiuwen.core.common.logging import logger
from openjiuwen.core.common.exception.errors import build_error, BaseError
from openjiuwen.core.common.exception.codes import StatusCode

if TYPE_CHECKING:
    from openjiuwen.core.single_agent.base import AbilityManager
    from openjiuwen.core.single_agent.schema.agent_card import AgentCard


class Controller:
    """Controller

    Responsible for handling events and managing the task lifecycle.
    It is the core component of ControllerAgent.
    """

    def __init__(self):
        """Initialize controller"""
        super().__init__()
        self._card: Optional[AgentCard] = None
        self._ability_manager: Optional['AbilityManager'] = None
        self._config: Optional[ControllerConfig] = None
        self._context_engine: Optional[ContextEngine] = None
        self._task_manager: Optional[TaskManager] = None
        self._event_queue: Optional[EventQueue] = None
        self._task_scheduler: Optional[TaskScheduler] = None
        self._event_handler: Optional[EventHandler] = None

        # Event loop tracking
        self._event_loop: Optional[asyncio.AbstractEventLoop] = None

    def init(
            self,
            card: AgentCard,
            config: ControllerConfig,
            ability_manager: 'AbilityManager',
            context_engine: ContextEngine
    ):
        """Initialize controller

        Args:
            card: Agent card
            config: controller configuration
            ability_manager: ability manager
            context_engine: context engine
        """
        self._card = card
        self._config = config
        self._ability_manager = ability_manager
        self._context_engine = context_engine
        self._task_manager = TaskManager(config=self._config)
        self._event_queue = EventQueue(config=self._config)
        self._task_scheduler = TaskScheduler(
            config=self._config,
            task_manager=self._task_manager,
            context_engine=self._context_engine,
            ability_manager=self._ability_manager,
            event_queue=self._event_queue,
            card=card
        )

        # Wire up submit notification
        self._task_manager.set_on_task_submitted(
            self._task_scheduler.notify_task_submitted
        )

    @property
    def event_queue(self) -> EventQueue:
        """Get event queue"""
        return self._event_queue

    @property
    def config(self) -> ControllerConfig:
        """Get controller configuration"""
        return self._config

    @config.setter
    def config(self, config: ControllerConfig):
        """Set controller configuration"""
        self._config = config
        if self._task_manager is not None:
            self._task_manager.config = config
        if self._event_queue is not None:
            self._event_queue.config = config
        if self._task_scheduler is not None:
            self._task_scheduler.config = config
        if self._event_handler is not None:
            self._event_handler.config = config

    @property
    def context_engine(self) -> ContextEngine:
        """Get context engine"""
        return self._context_engine

    @property
    def ability_manager(self) -> 'AbilityManager':
        """Get ability manager"""
        return self._ability_manager

    @context_engine.setter
    def context_engine(self, context_engine: ContextEngine):
        """Set context engine"""
        self._context_engine = context_engine

    @ability_manager.setter
    def ability_manager(self, ability_manager: 'AbilityManager'):
        """Set ability manager"""
        self._ability_manager = ability_manager

    @property
    def task_manager(self) -> Optional[TaskManager]:
        """Get task manager"""
        return self._task_manager

    @property
    def task_scheduler(self) -> Optional[TaskScheduler]:
        """Get task scheduler"""
        return self._task_scheduler

    @property
    def event_handler(self) -> Optional[EventHandler]:
        """Get event handler"""
        return self._event_handler

    def set_event_handler(self, event_handler: EventHandler):  # pylint: disable=attribute-defined-outside-init
        """Set event handler

        Args:
            event_handler: event handler instance
        """
        self._event_handler = event_handler
        self._event_handler.config = self._config
        self._event_handler.context_engine = self._context_engine
        self._event_handler.task_scheduler = self._task_scheduler
        self._event_handler.task_manager = self._task_manager
        self._event_handler.ability_manager = self._ability_manager

    def add_task_executor(
            self,
            task_type: str,
            task_executor_builder: Callable[[TaskExecutorDependencies], TaskExecutor]
    ) -> "Controller":
        """Add task executor

        Args:
            task_type: task type
            task_executor_builder: task executor builder function

        Returns:
            self (supports chaining)
        """
        self._task_scheduler.task_executor_registry.add_task_executor(task_type, task_executor_builder)
        return self

    def remove_task_executor(self, task_type: str):
        """Remove task executor

        Args:
            task_type: task type
        """
        self._task_scheduler.task_executor_registry.remove_task_executor(task_type)

    async def _restore_task_manager_state(self, session: Session) -> bool:
        """Restore TaskManager state from session.

        If restoration fails, clear the current task_manager state without
        raising errors so that new tasks are not affected.

        Args:
            session: session object

        Returns:
            bool: whether restoration succeeded. True if restored successfully,
            False if there was no state or restoration failed.
        """
        controller_state = session.get_state("controller")
        if not controller_state or "task_manager_state" not in controller_state:
            # No saved state, clear all task manager state
            logger.info(f"No saved state found for session {session.get_session_id()}, clearing task manager")
            await self._task_manager.clear_state()
            return False

        try:
            logger.info(f"Restoring TaskManager state for session {session.get_session_id()}")

            # Deserialize TaskManagerState from dict
            state_dict = controller_state["task_manager_state"]
            task_manager_state = TaskManagerState.model_validate(state_dict)

            # Load state into task_manager
            await self._task_manager.load_state(task_manager_state)
            logger.info(
                f"Successfully restored TaskManager state: "
                f"{len(task_manager_state.tasks)} tasks, "
                f"{len(task_manager_state.root_tasks)} root tasks"
            )
            return True

        except Exception as e:
            logger.error(
                f"Failed to restore TaskManager state for session {session.get_session_id()}: {e}, "
                f"clearing task manager state instead",
                exc_info=True
            )
            # Fallback: clear all task manager state to allow user to continue
            await self._task_manager.clear_state()
            return False

    async def _save_task_manager_state(self, session: Session) -> None:
        """Save TaskManager state to session

        Args:
            session: session object
        """
        if not self._config.enable_task_persistence:
            logger.info("Task persistence disabled, no need to save TaskManager state for session")
            return
        try:
            task_manager_state = await self._task_manager.get_state()
            controller_state = {
                "task_manager_state": task_manager_state.model_dump()
            }

            # Clear old state first, then update with new state (ensures proper cleanup of nested dict keys)
            session.update_state({"controller": None})
            session.update_state({"controller": controller_state})

            logger.info(
                f"Saved TaskManager state for session {session.get_session_id()}: "
                f"{len(task_manager_state.tasks)} tasks, "
                f"{len(task_manager_state.root_tasks)} root tasks"
            )
        except Exception as e:
            logger.error(f"Failed to save TaskManager state for session {session.get_session_id()}: {e}")

    def get_task_executor(
            self,
            config: ControllerConfig,
            ability_manager: 'AbilityManager',
            context_engine: ContextEngine,
            task_manager: TaskManager
    ) -> TaskExecutor:
        """Get task executor

        Args:
            config: controller configuration
            ability_manager: ability manager
            context_engine: context engine
            task_manager: task manager

        Returns:
            TaskExecutor: task executor instance
        """
        return self._task_scheduler.task_executor_registry.get_task_executor(
            config,
            ability_manager,
            context_engine,
            task_manager
        )

    async def start(self):
        """Start controller

        Start the task scheduler to begin processing tasks, and start the
        event queue.
        """
        self._event_queue.start()
        await self._task_scheduler.start()

    async def stop(self):
        """Stop controller

        Stop the task scheduler and event queue, and stop processing tasks.
        """
        await self._task_scheduler.stop()
        await self._event_queue.stop()

    async def invoke(
            self,
            inputs: 'InputEvent',
            session: Session,
            **kwargs
    ) -> ControllerOutput:
        """Batch execution using controller

        Args:
            inputs: input event
            session: session object
            **kwargs: additional parameters

        Returns:
            ControllerOutput: controller output result

        Note:
            1. Calls stream method
            2. Converts streamed messages into a batch output
        """
        try:
            # Collect all streamed output chunks, only get OUTPUT mode data
            chunks: List[ControllerOutputChunk] = []
            async for chunk in self.stream(
                    inputs=inputs,
                    session=session,
                    stream_modes=[BaseStreamMode.OUTPUT],
                    **kwargs
            ):
                chunks.append(chunk)

            return ControllerOutput(
                type=EventType.TASK_COMPLETION,
                data=chunks
            )

        except BaseError:
            raise

        except Exception as e:
            logger.error(f"Controller invoke error: {e}", exc_info=True)
            raise build_error(
                StatusCode.AGENT_CONTROLLER_RUNTIME_ERROR,
                error_msg=str(e),
                cause=e
            ) from e

    async def _ensure_started(self) -> None:
        """Lazy start with event loop detection.

        Ensures the event queue and task scheduler are
        running in the current event loop. Handles event
        loop changes by recreating components.
        """
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError as e:
            logger.error("No running event loop")
            raise build_error(
                StatusCode.AGENT_CONTROLLER_RUNTIME_ERROR,
                error_msg="No running event loop",
                cause=e
            ) from e

        if self._event_loop is not current_loop:
            # Event loop changed or first start
            if self._event_loop is not None:
                logger.info(
                    f"Event loop changed, restarting event_queue and task_scheduler "
                    f"(old: {id(self._event_loop)}, new: {id(current_loop)})"
                )
                try:
                    await self._task_scheduler.stop()
                    await self._event_queue.stop()
                except Exception as e:
                    logger.warning(f"Failed to stop old components: {e}")

                # Recreate components (bound to new event loop)
                self._event_queue = EventQueue(config=self._config)
                self._task_scheduler = TaskScheduler(
                    config=self._config,
                    task_manager=self._task_manager,
                    context_engine=self._context_engine,
                    ability_manager=self._ability_manager,
                    event_queue=self._event_queue,
                    card=self._card
                )

                # Re-wire submit notification
                self._task_manager.set_on_task_submitted(
                    self._task_scheduler
                    .notify_task_submitted
                )

            self._event_queue.start()
            self._event_queue.set_event_handler(self._event_handler)
            await self._task_scheduler.start()
            self._event_loop = current_loop
            logger.info(f"Controller started in event loop {id(current_loop)}")

    async def bind_session(self, session: Session) -> None:
        """Bind a session to Controller infrastructure.

        Performs setup logic: ensure started, restore
        task manager state, register session, subscribe
        event queue.

        Args:
            session: Session object.
        """
        await self._ensure_started()
        session_id = session.get_session_id()
        await self._restore_task_manager_state(session)
        self._task_scheduler.sessions[session_id] = session
        await self._event_queue.subscribe(
            self._card.id, session_id
        )

    async def unbind_session(self, session: Session) -> None:
        """Unbind a session and perform cleanup.

        Saves task manager state, unsubscribes event
        queue, removes session from task scheduler.

        Args:
            session: Session object.
        """
        session_id = session.get_session_id()
        await self._save_task_manager_state(session)
        await self._event_queue.unsubscribe(
            self._card.id, session_id
        )
        if session_id in self._task_scheduler.sessions:
            del self._task_scheduler.sessions[session_id]

    async def publish_event_async(
        self, session: Session, event: "InputEvent",
    ) -> None:
        """Fire-and-forget event publish.

        Args:
            session: Session object.
            event: Event to publish.
        """
        await self._event_queue.publish_event_async(
            self._card.id, session, event
        )

    async def stream(
            self,
            inputs: 'InputEvent',
            session: Session,
            stream_modes: Optional[List[StreamMode]] = None,
            **kwargs
    ) -> AsyncIterator[ControllerOutputChunk]:
        """Stream execution using controller

        Args:
            inputs: input event
            session: session object (created and managed by caller)
            stream_modes: list of stream output modes (optional)
            **kwargs: additional parameters

        Yields:
            ControllerOutputChunk: controller output chunk

        Note:
            1. Restore controller state (including task_manager state)
            2. Put Session into task_scheduler.sessions
            3. Subscribe via self._event_queue.subscribe
            4. Publish input event to self._event_queue
            5. Read event handling results and stream them out
            6. Deactivate all subscriptions
            7. Save controller state (including task_manager state)
            8. Remove Session from task_scheduler.sessions
        """
        await self._ensure_started()

        agent_id = self._card.id
        session_id = session.get_session_id()

        # Restore controller state
        state_restored = await self._restore_task_manager_state(session)
        if not state_restored:
            logger.info(f"Starting with clean TaskManager state for session {session_id}")

        # Register session
        self._task_scheduler.sessions[session_id] = session

        try:
            # Subscribe to 4 event types
            await self._event_queue.subscribe(agent_id, session_id)
            # Synchronous publish — wait for handler to finish so we
            # can check whether any tasks were created.
            await self._event_queue.publish_event(
                agent_id, session, inputs
            )
            # If handle_input didn't create any tasks (e.g. a pure
            # state-query round), send the completion signal now;
            # otherwise the signal will be sent by _execute_task_wrapper.
            await self._task_scheduler.ensure_session_completion_signal(
                session_id
            )

            first_frame_timeout = (
                self._config.stream_first_frame_timeout
                if self._config
                else 30.0
            )

            # Read from session stream with first-frame
            # timeout safety net
            got_first = False
            stream_iter = session.stream_iterator(
            ).__aiter__()
            # Wait for first chunk with timeout
            try:
                first_chunk = await asyncio.wait_for(
                    stream_iter.__anext__(),
                    timeout=first_frame_timeout,
                )
                got_first = True
            except asyncio.TimeoutError as exc:
                logger.error(
                    "First frame timeout after "
                    f"{first_frame_timeout}s for "
                    f"session {session_id}"
                )
                raise build_error(
                    StatusCode
                    .AGENT_CONTROLLER_RUNTIME_ERROR,
                    error_msg=(
                        "Stream first frame timeout"
                    ),
                ) from exc
            except StopAsyncIteration:
                got_first = False

            if got_first:
                if isinstance(
                    first_chunk, ControllerOutputChunk
                ):
                    if not (
                        first_chunk.payload
                        and first_chunk.payload.type
                        == "all_tasks_processed"
                    ):
                        yield first_chunk
                else:
                    yield first_chunk

                # Continue reading remaining chunks
                async for chunk in stream_iter:
                    if isinstance(
                        chunk,
                        ControllerOutputChunk,
                    ):
                        if (
                            chunk.payload
                            and chunk.payload.type
                            == "all_tasks_processed"
                        ):
                            logger.info(
                                "All tasks handled for "
                                f"session {session_id}"
                                ", stopping stream"
                            )
                            break

                    yield chunk

        except BaseError:
            raise

        except Exception as e:
            logger.error(f"Controller runtime error: {e}")
            raise build_error(
                StatusCode.AGENT_CONTROLLER_RUNTIME_ERROR,
                error_msg=str(e),
                cause=e
            ) from e

        finally:
            # Save controller state
            await self._save_task_manager_state(session)

            # Unsubscribe events
            await self._event_queue.unsubscribe(agent_id, session_id)

            # Remove session
            if session_id in self._task_scheduler.sessions:
                del self._task_scheduler.sessions[session_id]

            # Keep Controller running which allows multiple stream() calls without restart overhead
            logger.info(
                f"Session {session_id} completed, "
                f"{len(self._task_scheduler.sessions)} active sessions remaining"
            )
