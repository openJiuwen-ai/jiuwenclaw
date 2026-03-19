# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Rail & Callback base definitions for Agent lifecycle hooks.

Main classes included:
 - AgentCallbackEvent: lifecycle event types
 - AgentCallbackContext: Unified callback context
 - AgentRail: Class-based rail with tools/skills support
 - rail: Decorator for before/after/on_exception events

Created on: 2025-11-25
"""
from __future__ import annotations

import asyncio
from abc import ABC
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from enum import Enum
from functools import wraps
from typing import (
    Any,
    Union,
    Dict,
    List,
    Optional,
    Callable,
    Awaitable,
    TYPE_CHECKING,
)

from openjiuwen.core.context_engine import ModelContext
from openjiuwen.core.session import InteractiveInput
from openjiuwen.core.session.agent import Session

if TYPE_CHECKING:
    from openjiuwen.core.single_agent.base import BaseAgent


# ================================================================
# Typed Event Inputs
# ================================================================
@dataclass
class InvokeInputs:
    """Data for BEFORE/AFTER_INVOKE events.

    Before: query + conversation_id filled.
    After: result also filled.

    Attributes:
        query: User query string
        conversation_id: Optional conversation/session ID
        result: Agent invoke result (filled after invoke)
    """
    query: Optional[str, InteractiveInput]
    conversation_id: Optional[str] = None
    result: Optional[Dict[str, Any]] = None


@dataclass
class ModelCallInputs:
    """Input data for BEFORE/AFTER_MODEL_CALL events.

    Attributes:
        messages: Message list for LLM
        tools: Optional tool definitions
        response: LLM response (filled after call)
    """
    messages: List[Any] = field(default_factory=list)
    tools: Optional[List[Any]] = None
    response: Optional[Any] = None


@dataclass
class ToolCallInputs:
    """Input data for BEFORE/AFTER_TOOL_CALL events.

    Attributes:
        tool_call: Raw tool call object
        tool_name: Name of the tool to execute
        tool_args: Arguments for the tool
        tool_result: Tool execution result (filled after call)
        tool_msg: Tool message (filled after call)
    """
    tool_call: Optional[Any] = None
    tool_name: str = ""
    tool_args: Any = None
    tool_result: Optional[Any] = None
    tool_msg: Optional[Any] = None


@dataclass
class TaskIterationInputs:
    """Input data for task-iteration lifecycle events.

    Used by agents that support an outer task loop
    (for example DeepAgent extensions).

    Attributes:
        iteration: 1-based outer-loop iteration index
        loop_event: Event object that triggered this iteration
        conversation_id: Optional conversation/session ID
        result: Iteration result (filled after iteration)
    """
    iteration: int
    loop_event: Any
    conversation_id: Optional[str] = None
    result: Optional[Dict[str, Any]] = None


@dataclass
class RetryRequest:
    """Retry directive produced by on_exception rails."""

    delay_seconds: float = 0.0


@dataclass
class ForceFinishRequest:
    """Signal to terminate the agent loop and return a result immediately."""

    result: Dict[str, Any]


#: Union type for all typed event inputs
EventInputs = Union[
    InvokeInputs,
    ModelCallInputs,
    ToolCallInputs,
    TaskIterationInputs,
    Dict[str, Any],
]


# ================================================================
# Agent Callback Event Types
# ================================================================
class AgentCallbackEvent(str, Enum):
    """Agent callback event types for agent lifecycle.

    Lifecycle Callbacks:
        BEFORE_INVOKE: Before agent.invoke() starts
        AFTER_INVOKE: After agent.invoke() completes
        BEFORE_TASK_ITERATION: Before one outer task-loop iteration starts
        AFTER_TASK_ITERATION: After one outer task-loop iteration completes

    Model Interaction Callbacks:
        BEFORE_MODEL_CALL: Before LLM is called
        AFTER_MODEL_CALL: After LLM response is received
        ON_MODEL_EXCEPTION: When LLM call raises

    Tool Execution Callbacks:
        BEFORE_TOOL_CALL: Before a tool is executed
        AFTER_TOOL_CALL: After a tool execution completes
        ON_TOOL_EXCEPTION: When tool execution raises
    """
    BEFORE_INVOKE = "before_invoke"
    AFTER_INVOKE = "after_invoke"
    BEFORE_TASK_ITERATION = "before_task_iteration"
    AFTER_TASK_ITERATION = "after_task_iteration"
    BEFORE_MODEL_CALL = "before_model_call"
    AFTER_MODEL_CALL = "after_model_call"
    ON_MODEL_EXCEPTION = "on_model_exception"
    BEFORE_TOOL_CALL = "before_tool_call"
    AFTER_TOOL_CALL = "after_tool_call"
    ON_TOOL_EXCEPTION = "on_tool_exception"


# ================================================================
# Agent Callback Context
# ================================================================
@dataclass
class AgentCallbackContext:
    """Unified context object passed to rail/callback hooks.

    Attributes:
        agent: Reference to the BaseAgent instance
        event: Current callback event (set by fire())
        inputs: Current event input data (changes per event)
        config: Runtime configuration
        session: Current Session object
        context: Current ModelContext
        extra: Cross-rail communication dict (persists
            across events within a single invoke)
        exception: Exception object (set on error events)
        retry_attempt: Current failed-attempt index
    """
    agent: 'BaseAgent'
    event: Optional[AgentCallbackEvent] = None
    inputs: EventInputs = field(default_factory=dict)
    config: Any = None
    session: Optional[Session] = None
    context: Optional[ModelContext] = None
    extra: Dict[str, Any] = field(default_factory=dict)
    exception: Optional[Exception] = None
    retry_attempt: int = 0
    _retry_request: Optional[RetryRequest] = field(
        default=None, init=False, repr=False
    )
    _force_finish_request: Optional[ForceFinishRequest] = field(
        default=None, init=False, repr=False
    )

    async def fire(
        self, event: AgentCallbackEvent
    ) -> None:
        """Trigger all registered callbacks for an event.

        Args:
            event: The event to fire
        """
        self.event = event
        await self.agent.agent_callback_manager.execute(
            event, self
        )

    def request_retry(self, delay_seconds: float = 0.0) -> None:
        """Request the wrapped rail method to retry once more.

        This method is intended to be called inside
        ``on_model_exception`` / ``on_tool_exception`` hooks.

        Args:
            delay_seconds: Sleep duration before next attempt
        """
        if delay_seconds < 0:
            delay_seconds = 0.0
        self._retry_request = RetryRequest(
            delay_seconds=delay_seconds
        )

    def consume_retry_request(self) -> Optional[RetryRequest]:
        """Read and clear pending retry request."""
        request = self._retry_request
        self._retry_request = None
        return request

    def request_force_finish(self, result: Dict[str, Any]) -> None:
        """Request the agent loop to terminate and return *result* immediately.

        Can be called in any hook (e.g. before_model_call, after_tool_call).
        The agent loop checks this signal after every railed operation.
        If called in a ``before`` hook, the decorated method body is skipped.
        """
        self._force_finish_request = ForceFinishRequest(result=result)

    def consume_force_finish(self) -> Optional[ForceFinishRequest]:
        """Read and clear a pending force-finish request."""
        request = self._force_finish_request
        self._force_finish_request = None
        return request

    @property
    def has_force_finish_request(self) -> bool:
        """Check whether a force-finish request is pending."""
        return self._force_finish_request is not None

    @asynccontextmanager
    async def lifecycle(
        self,
        before: AgentCallbackEvent,
        after: AgentCallbackEvent,
    ):
        """Async context manager for before/after event pairs.

        Fires ``before`` on entry, ``after`` in finally block.
        Automatically saves and restores ``self.inputs`` so
        that inner steps (model_call, tool_call) can freely
        overwrite it without affecting the after event.

        Args:
            before: Event to fire on entry
            after: Event to fire on exit (always)
        """
        saved_inputs = self.inputs
        await self.fire(before)
        try:
            yield self
        finally:
            self.inputs = saved_inputs
            await self.fire(after)


# ================================================================
# Callback Type Aliases
# ================================================================
AgentCallback = Callable[
    [AgentCallbackContext], Awaitable[None]
]
SyncAgentCallback = Callable[
    [AgentCallbackContext], None
]
AnyAgentCallback = Union[AgentCallback, SyncAgentCallback]


# ================================================================
# Event → Method Name Mapping
# ================================================================
EVENT_METHOD_MAP: Dict[AgentCallbackEvent, str] = {
    AgentCallbackEvent.BEFORE_INVOKE: "before_invoke",
    AgentCallbackEvent.AFTER_INVOKE: "after_invoke",
    AgentCallbackEvent.BEFORE_MODEL_CALL: "before_model_call",
    AgentCallbackEvent.AFTER_MODEL_CALL: "after_model_call",
    AgentCallbackEvent.ON_MODEL_EXCEPTION: "on_model_exception",
    AgentCallbackEvent.BEFORE_TOOL_CALL: "before_tool_call",
    AgentCallbackEvent.AFTER_TOOL_CALL: "after_tool_call",
    AgentCallbackEvent.ON_TOOL_EXCEPTION: "on_tool_exception",
    AgentCallbackEvent.BEFORE_TASK_ITERATION: "before_task_iteration",
    AgentCallbackEvent.AFTER_TASK_ITERATION: "after_task_iteration",
}


# ================================================================
# AgentRail Base Class
# ================================================================
class AgentRail(ABC):
    """Base class for agent rails.

    Rails provide class-based lifecycle hooks with:
    - State management across callback invocations
    - Tools/skills that are auto-registered on the agent
    - Priority-based execution ordering (higher = first)

    Attributes:
        priority: Execution priority (higher runs first)

    Example::

        class LogRail(AgentRail):
            async def before_model_call(self, ctx):
                print("calling LLM...")

            async def after_model_call(self, ctx):
                print("LLM responded")

        await agent.register_rail(LogRail())
    """

    priority: int = 50

    def init(self, agent):
        pass

    def uninit(self, agent):
        pass

    # -- 8 hook methods (override to activate) --

    async def before_invoke(
        self, ctx: AgentCallbackContext
    ) -> None:
        """Called before agent.invoke() starts."""
        pass

    async def after_invoke(
        self, ctx: AgentCallbackContext
    ) -> None:
        """Called after agent.invoke() completes."""
        pass

    async def before_model_call(
        self, ctx: AgentCallbackContext
    ) -> None:
        """Called before LLM is invoked."""
        pass

    async def after_model_call(
        self, ctx: AgentCallbackContext
    ) -> None:
        """Called after LLM response is received."""
        pass

    async def on_model_exception(
        self, ctx: AgentCallbackContext
    ) -> None:
        """Called when LLM call raises an exception."""
        pass

    async def before_tool_call(
        self, ctx: AgentCallbackContext
    ) -> None:
        """Called before a tool is executed."""
        pass

    async def after_tool_call(
        self, ctx: AgentCallbackContext
    ) -> None:
        """Called after a tool execution completes."""
        pass

    async def on_tool_exception(
        self, ctx: AgentCallbackContext
    ) -> None:
        """Called when tool execution raises."""
        pass

    async def before_task_iteration(
        self, ctx: AgentCallbackContext
    ) -> None:
        """Called before each task-loop iteration."""
        pass

    async def after_task_iteration(
        self, ctx: AgentCallbackContext
    ) -> None:
        """Called after each task-loop iteration."""
        pass

    def get_callbacks(
        self,
    ) -> Dict[AgentCallbackEvent, AgentCallback]:
        """Extract overridden hook methods.

        Returns:
            Dict mapping event to the bound method,
            only for methods actually overridden by
            the subclass.
        """
        callbacks: Dict[
            AgentCallbackEvent, AgentCallback
        ] = {}
        for event, method_name in EVENT_METHOD_MAP.items():
            method = getattr(self, method_name, None)
            if method and not self._is_base_method(
                method_name
            ):
                callbacks[event] = method
        return callbacks

    def _is_base_method(self, method_name: str) -> bool:
        """Check if method is the base AgentRail no-op."""
        method = getattr(
            self.__class__, method_name, None
        )
        base_method = getattr(
            AgentRail, method_name, None
        )
        return method is base_method


# ================================================================
# @rail Decorator
# ================================================================
def rail(
    before: Optional[AgentCallbackEvent] = None,
    after: Optional[AgentCallbackEvent] = None,
    on_exception: Optional[AgentCallbackEvent] = None,
):
    """Decorator to fire lifecycle events around a method.

    Args:
        before: Event fired before the method body
        after: Event fired in finally (always runs)
        on_exception: Event fired when an exception occurs

    Usage::

        @rail(
            before=AgentCallbackEvent.BEFORE_MODEL_CALL,
            after=AgentCallbackEvent.AFTER_MODEL_CALL,
            on_exception=AgentCallbackEvent.ON_MODEL_EXCEPTION,
        )
        async def _do_model_call(self, ctx):
            ...
    """
    def decorator(fn):
        @wraps(fn)
        async def wrapper(self, ctx, *args, **kwargs):
            attempt = 0
            while True:
                # Drop stale requests from previous attempts.
                ctx.consume_retry_request()
                ctx.retry_attempt = attempt
                ctx.exception = None
                try:
                    if before:
                        await ctx.fire(before)
                    # If a before hook requested force_finish, skip the method body.
                    if ctx.has_force_finish_request:
                        return None
                    return await fn(self, ctx, *args, **kwargs)
                except Exception as e:
                    ctx.exception = e
                    if on_exception:
                        await ctx.fire(on_exception)

                    retry_request = ctx.consume_retry_request()
                    if not retry_request:
                        raise

                    if retry_request.delay_seconds > 0:
                        await asyncio.sleep(
                            retry_request.delay_seconds
                        )
                    attempt += 1
                finally:
                    if after:
                        await ctx.fire(after)
        events = (before, after, on_exception)
        wrapper.rail_events = events
        return wrapper
    return decorator
