# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""DeepAgents public API."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from openjiuwen.deepagents.deep_agent import DeepAgent
    from openjiuwen.deepagents.task_loop.task_loop_event_executor import (
        TaskLoopEventExecutor,
    )
    from openjiuwen.deepagents.task_loop.task_loop_event_handler import (
        TaskLoopEventHandler,
    )
    from openjiuwen.deepagents.factory import create_deep_agent
    from openjiuwen.deepagents.schema.config import (
        DeepAgentConfig,
    )
    from openjiuwen.deepagents.schema.stop_condition import (
        StopCondition,
    )
    from openjiuwen.deepagents.schema.workspace import Workspace

__all__ = [
    "DeepAgent",
    "TaskLoopEventHandler",
    "TaskLoopEventExecutor",
    "DeepAgentConfig",
    "StopCondition",
    "create_deep_agent",
    "Workspace",
]


def __getattr__(name: str) -> Any:
    """Lazily import heavy modules on demand."""
    if name == "DeepAgent":
        from openjiuwen.deepagents.deep_agent import (
            DeepAgent,
        )
        return DeepAgent
    if name == "TaskLoopEventHandler":
        from openjiuwen.deepagents.task_loop.task_loop_event_handler import (
            TaskLoopEventHandler,
        )
        return TaskLoopEventHandler
    if name == "TaskLoopEventExecutor":
        from openjiuwen.deepagents.task_loop.task_loop_event_executor import (
            TaskLoopEventExecutor,
        )
        return TaskLoopEventExecutor
    if name == "DeepAgentConfig":
        from openjiuwen.deepagents.schema.config import (
            DeepAgentConfig,
        )
        return DeepAgentConfig
    if name == "StopCondition":
        from openjiuwen.deepagents.schema.stop_condition import (
            StopCondition,
        )
        return StopCondition
    if name == "create_deep_agent":
        from openjiuwen.deepagents.factory import (
            create_deep_agent,
        )
        return create_deep_agent
    if name == "Workspace":
        from openjiuwen.deepagents.schema.workspace import (
            Workspace,
        )
        return Workspace
    raise AttributeError(
        f"module {__name__!r} has no attribute {name!r}"
    )
