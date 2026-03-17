# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""DeepAgent rail base definitions.

Extends the core AgentRail with two additional lifecycle
hooks for the outer task-loop iteration:
  - before_task_iteration
  - after_task_iteration
"""
from __future__ import annotations

from typing import Any, Dict

from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackEvent,
    AgentCallbackContext,
    AgentRail,
)

DEEP_EVENT_METHOD_MAP: Dict[AgentCallbackEvent, str] = {
    AgentCallbackEvent.BEFORE_TASK_ITERATION: "before_task_iteration",
    AgentCallbackEvent.AFTER_TASK_ITERATION: "after_task_iteration",
}


class DeepAgentRail(AgentRail):
    """Extended rail base class for DeepAgent.

    Adds two hook methods for the outer task-loop
    iteration on top of the 8 standard AgentRail hooks.

    Subclasses override ``before_task_iteration`` and/or
    ``after_task_iteration`` to participate in the
    task-loop lifecycle.

    Example::

        class MyTaskRail(DeepAgentRail):
            async def before_task_iteration(self, ctx):
                print("starting iteration...")

            async def after_task_iteration(self, ctx):
                print("iteration done")
    """

    # -- 2 additional hook methods --

    async def before_task_iteration(
        self,
        ctx: AgentCallbackContext,  # noqa: ARG002
    ) -> None:
        """Called before each task-loop iteration."""
        pass

    async def after_task_iteration(
        self,
        ctx: AgentCallbackContext,  # noqa: ARG002
    ) -> None:
        """Called after each task-loop iteration."""
        pass

    def get_callbacks(  # type: ignore[override]
        self,
    ) -> Dict[Any, Any]:
        """Extract overridden hook methods.

        Merges standard AgentRail callbacks with
        DeepAgent task-loop callbacks.

        Returns:
            Dict mapping event (AgentCallbackEvent)
            to the bound method, only for methods
            actually overridden by the subclass.
        """
        callbacks: Dict[Any, Any] = dict(super().get_callbacks())

        for event, method_name in DEEP_EVENT_METHOD_MAP.items():
            method = getattr(self, method_name, None)
            if method and not self._is_deep_base(method_name):
                callbacks[event] = method

        return callbacks

    def _is_deep_base(self, method_name: str) -> bool:
        """Check if method is the DeepAgentRail no-op.

        Args:
            method_name: Name of the hook method.

        Returns:
            True if the method has not been overridden
            from DeepAgentRail's default no-op.
        """
        method = getattr(self.__class__, method_name, None)
        base_method = getattr(DeepAgentRail, method_name, None)
        return method is base_method
