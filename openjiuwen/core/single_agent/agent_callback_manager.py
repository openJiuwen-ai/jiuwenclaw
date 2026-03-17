# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""AgentCallbackManager Class Definition
"""
import asyncio
from typing import Optional

from openjiuwen.core.single_agent.rail.base import AgentCallbackEvent, AgentRail, \
    AnyAgentCallback, AgentCallbackContext


class AgentCallbackManager:
    """Manager for Middlewares.

    Supports both function-style and middleware-style callbacks with priority ordering.
    """
    def __init__(self, agent_id):
        self.agent_id = agent_id

    async def register_callback(
        self,
        event: AgentCallbackEvent,
        callback: AnyAgentCallback,
        priority: int = 100
    ) -> 'AgentCallbackManager':
        """Register an agent callback for an event.

        Args:
            event: The agent callback event to register for
            callback: The callback function (sync or async)
            priority: Execution priority (lower = runs first)

        Returns:
            self for chaining
        """
        # Wrap sync callbacks to async
        if not asyncio.iscoroutinefunction(callback):
            original = callback

            async def async_wrapper(ctx: AgentCallbackContext):
                original(ctx)
            callback = async_wrapper

        agent_event = self._get_agent_event(event)
        from openjiuwen.core.runner import Runner
        await Runner.callback_framework.register(agent_event, callback, priority=priority)
        return self

    async def register_rail(self, rail: AgentRail, agent: 'object') -> 'AgentCallbackManager':
        """Register a rail instance.

        Args:
            rail: AgentRail instance
            agent: BaseAgent instance (for tool registration)

        Returns:
            self for chaining
        """
        for event, callback in rail.get_callbacks().items():
            await self.register_callback(event, callback, rail.priority)

        return self

    async def unregister_rail(self, rail: AgentRail, agent: 'object') -> None:
        """Unregister a rail instance.

        Args:
            rail: AgentRail instance to remove
            agent: BaseAgent instance (for tool removal)
        """
        for event, callback in rail.get_callbacks().items():
            await self.unregister(event, callback)

    async def unregister(self, event: AgentCallbackEvent, callback: AnyAgentCallback) -> None:
        """Unregister a hook callback.

        Args:
            event: The hook event
            callback: The callback to remove
        """
        agent_event = self._get_agent_event(event)
        from openjiuwen.core.runner import Runner
        await Runner.callback_framework.unregister(agent_event, callback)

    async def clear(self, event: Optional[AgentCallbackEvent] = None) -> None:
        """Clear hooks.

        Args:
            event: Specific event to clear, or None to clear all
        """
        from openjiuwen.core.runner import Runner
        if event:
            agent_event = self._get_agent_event(event)
            await Runner.callback_framework.unregister_event(agent_event)
        else:
            for e in AgentCallbackEvent:
                agent_event = self._get_agent_event(e)
                await Runner.callback_framework.unregister_event(agent_event)

    def has_hooks(self, event: AgentCallbackEvent) -> bool:
        """Check if any hooks are registered for an event.

        Args:
            event: The hook event to check

        Returns:
            True if hooks are registered
        """
        agent_event = self._get_agent_event(event)
        from openjiuwen.core.runner import Runner
        return len(Runner.callback_framework.list_callbacks(agent_event)) > 0

    async def execute(
        self,
        event: AgentCallbackEvent,
        ctx: AgentCallbackContext,
    ) -> AgentCallbackContext:
        """Execute all hooks for an event.

        Args:
            event: The hook event
            ctx: The hook context

        Returns:
            The (potentially modified) context
        """
        from openjiuwen.core.runner import Runner
        agent_event = self._get_agent_event(event)
        await Runner.callback_framework.trigger(agent_event, ctx)
        return ctx

    def _get_agent_event(self, event: AgentCallbackEvent) -> str:
        """Unified generation of event name with agent_id prefix to avoid duplicate name

        Args:
            event: Original callback event

        Returns:
            Event name string prefixed with agent_id
        """
        return f"{self.agent_id}_{event}"