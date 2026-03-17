# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.


"""Task executor module.

This module defines the abstract base class for task executors, used to
execute different kinds of tasks.

Main class:
- TaskExecutor: abstract base class defining the execution interface.

Key responsibilities:
- Execute tasks (``execute``).
- Check whether a task can be paused (``can_pause``).
- Pause a task (``pause``).
- Check whether a task can be canceled (``can_cancel``).
- Cancel a task (``cancel``).
"""
from __future__ import annotations

from abc import abstractmethod, ABC
from typing import AsyncIterator, Tuple, TYPE_CHECKING

from openjiuwen.core.context_engine import ContextEngine
from openjiuwen.core.controller.modules.event_queue import EventQueue
from openjiuwen.core.controller.schema.controller_output import ControllerOutputChunk
from openjiuwen.core.controller.modules.task_manager import TaskManager
from openjiuwen.core.session.agent import Session
from openjiuwen.core.single_agent.base import AbilityManager

if TYPE_CHECKING:
    from openjiuwen.core.controller.base import ControllerConfig


class TaskExecutor(ABC):
    """Abstract base class for task executors.

    Defines the interface for executing tasks. Different logical task types
    should implement their own specialized ``TaskExecutor`` subclasses.

    Responsibilities:
        - Execute tasks (``execute``).
        - Check if a task can be paused (``can_pause``).
        - Pause tasks (``pause``).
        - Check if a task can be canceled (``can_cancel``).
        - Cancel tasks (``cancel``).
    """
    def __init__(
            self,
            config: ControllerConfig,
            ability_manager: AbilityManager,
            context_engine: ContextEngine,
            task_manager: TaskManager,
            event_queue: EventQueue
    ):
        """Initialize a task executor.

        Args:
            config: Controller configuration.
            ability_manager: Ability manager containing tools, workflows, etc.
            context_engine: Context engine used to manage conversation context.
            task_manager: Task manager used to update task state.
            event_queue: Event queue used to publish task-related events.
        """
        self._config = config
        self._ability_manager = ability_manager
        self._context_engine = context_engine
        self._task_manager = task_manager
        self._event_queue = event_queue

    @abstractmethod
    async def execute(self, task_id: str, session: Session) -> AsyncIterator[ControllerOutputChunk]:
        """Execute a task and stream outputs.

        Args:
            task_id: Task identifier.
            session: Session object.

        Yields:
            ControllerOutputChunk: Output chunks produced during execution.
        """
        ...

    @abstractmethod
    async def can_pause(self, task_id: str, session: Session) -> Tuple[bool, str]:
        """Check whether the task can be paused.

        Args:
            task_id: Task identifier.
            session: Session object.

        Returns:
            Tuple[bool, str]: (can_pause, reason_if_not).
        """
        ...

    @abstractmethod
    async def pause(self, task_id: str, session: Session) -> bool:
        """Pause the given task.

        Args:
            task_id: Task identifier.
            session: Session object.

        Returns:
            bool: Whether the pause operation succeeded.
        """
        ...

    @abstractmethod
    async def can_cancel(self, task_id: str, session: Session) -> Tuple[bool, str]:
        """Check whether the task can be canceled.

        Args:
            task_id: Task identifier.
            session: Session object.

        Returns:
            Tuple[bool, str]: (can_cancel, reason_if_not).
        """
        ...

    @abstractmethod
    async def cancel(self, task_id: str, session: Session) -> bool:
        """Cancel the given task.

        Args:
            task_id: Task identifier.
            session: Session object.

        Returns:
            bool: Whether the cancel operation succeeded.
        """
        ...
