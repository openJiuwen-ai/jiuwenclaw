# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Event handler module

This module defines classes related to event handlers, including:
- EventHandlerInput: input data model for event handlers
- EventHandler: abstract base class for event handlers

Event handlers are responsible for handling different types of events:
- INPUT: user input events
- TASK_INTERACTION: task interaction events (when user interaction is needed during task execution)
- TASK_COMPLETION: task completion events
- TASK_FAILED: task failure events
"""

from abc import abstractmethod, ABC
from typing import Any, TYPE_CHECKING, Dict, Optional
from pydantic import BaseModel

from openjiuwen.core.context_engine import ContextEngine
from openjiuwen.core.controller.config import ControllerConfig
from openjiuwen.core.controller.schema import Event
from openjiuwen.core.session.agent import Session

if TYPE_CHECKING:
    from openjiuwen.core.controller.modules.task_manager import TaskManager
    from openjiuwen.core.controller.modules.task_scheduler import TaskScheduler
    from openjiuwen.core.single_agent.base import AbilityManager


class EventHandlerInput(BaseModel):
    """Input data model for event handlers

    Contains event and session information that is passed to event handlers.

    Attributes:
        event: event object
        session: session object
    """
    model_config = {"arbitrary_types_allowed": True}

    event: Event
    session: Session


class EventHandler(ABC):
    """Abstract base class for event handlers

    Defines the event handling interface. Different types of controllers need
    to implement different event handlers.

    Main responsibilities:
    - handle input events (handle_input)
    - handle task interaction events (handle_task_interaction)
    - handle task completion events (handle_task_completion)
    - handle task failure events (handle_task_failed)
    """
    def __init__(self):
        """init EventHandler

        All dependencies are initialized to None and must be injected via property setters.
        """
        self._config = None
        self._context_engine = None
        self._ability_manager = None
        self._task_manager = None
        self._task_scheduler = None

    @property
    def config(self) -> ControllerConfig:
        return self._config

    @property
    def context_engine(self) -> ContextEngine:
        return self._context_engine

    @property
    def task_manager(self) -> "TaskManager":
        return self._task_manager

    @property
    def ability_manager(self) -> "AbilityManager":
        return self._ability_manager

    @property
    def task_scheduler(self) -> "TaskScheduler":
        return self._task_scheduler

    @config.setter
    def config(self, config: ControllerConfig):
        self._config = config

    @context_engine.setter
    def context_engine(self, context_engine: ContextEngine):
        self._context_engine = context_engine

    @task_manager.setter
    def task_manager(self, task_manager: "TaskManager"):
        self._task_manager = task_manager

    @ability_manager.setter
    def ability_manager(self, ability_manager: "AbilityManager"):
        self._ability_manager = ability_manager

    @task_scheduler.setter
    def task_scheduler(self, task_scheduler: "TaskScheduler"):
        self._task_scheduler = task_scheduler

    @abstractmethod
    async def handle_input(self, inputs: EventHandlerInput) -> Optional[Dict]:
        """Handle input events

        Args:
            inputs: event handler input containing event and session information

        Returns:
            Optional[Dict]: Response data
        """
        ...

    @abstractmethod
    async def handle_task_interaction(self, inputs: EventHandlerInput) -> Optional[Dict]:
        """Handle task interaction events

        Triggered when user interaction is required during task execution.

        Args:
            inputs: event handler input containing event and session information

        Returns:
            Optional[Dict]: Response data
        """
        ...

    @abstractmethod
    async def handle_task_completion(self, inputs: EventHandlerInput) -> Optional[Dict]:
        """Handle task completion events

        Triggered when task execution is completed.

        Args:
            inputs: event handler input containing event and session information

        Returns:
            Optional[Dict]: Response data
        """
        ...

    @abstractmethod
    async def handle_task_failed(self, inputs: EventHandlerInput) -> Optional[Dict]:
        """Handle task failure events

        Triggered when task execution fails.

        Args:
            inputs: event handler input containing event and session information

        Returns:
            Optional[Dict]: Response data
        """
        ...

    async def handle_follow_up(self, inputs: EventHandlerInput) -> Optional[Dict]:
        """Handle follow-up events.

        Default implementation returns an ack dict.
        Not abstract — existing subclasses are not
        required to override this method.

        Args:
            inputs: event handler input containing
                event and session information.

        Returns:
            Dict with status (must be non-empty to
            avoid MessageQueue ValueError).
        """
        return {"status": "not_supported"}

    def prepare_round(self) -> int:
        """Prepare a new execution round.

        Returns round_id for correlation. Default
        returns 0. Subclasses (e.g.
        DeepAgentEventHandler) override with
        Future-based round management.

        Returns:
            Round identifier for correlation.
        """
        return 0

    async def wait_completion(
        self,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Wait for round completion.

        Default returns immediately. Subclasses
        override with Future-based waiting.

        Args:
            timeout: Max seconds to wait.

        Returns:
            Result dict.
        """
        _ = timeout
        return {"status": "completed"}

    async def on_abort(self) -> None:
        """Called when the session is being aborted.

        Default implementation is a no-op. Subclasses
        can override to perform cleanup or signal
        completion waiters.
        """
