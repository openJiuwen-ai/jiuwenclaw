# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""DeepAgent task-loop runtime components."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from openjiuwen.deepagents.task_loop.task_loop_event_executor import (
        TaskLoopEventExecutor,
        build_deep_executor,
    )
    from openjiuwen.deepagents.task_loop.task_loop_event_handler import (
        TaskLoopEventHandler,
    )
    from openjiuwen.deepagents.task_loop.loop_coordinator import (
        LoopCoordinator,
    )
    from openjiuwen.deepagents.task_loop.loop_queues import (
        LoopQueues,
    )
    from openjiuwen.deepagents.task_loop.task_loop_controller import (
        TaskLoopController,
    )

__all__ = [
    "DEEP_TASK_TYPE",
    "TaskLoopEventExecutor",
    "build_deep_executor",
    "TaskLoopEventHandler",
    "LoopCoordinator",
    "LoopQueues",
    "TaskLoopController",
]


def __getattr__(name: str) -> Any:
    """Lazily import task-loop modules on demand."""
    if name in {
        "DEEP_TASK_TYPE",
        "TaskLoopEventExecutor",
        "build_deep_executor",
    }:
        from openjiuwen.deepagents.task_loop.task_loop_event_executor import (
            DEEP_TASK_TYPE,
            TaskLoopEventExecutor,
            build_deep_executor,
        )
        mapping = {
            "DEEP_TASK_TYPE": DEEP_TASK_TYPE,
            "TaskLoopEventExecutor": TaskLoopEventExecutor,
            "build_deep_executor": build_deep_executor,
        }
        result = mapping.get(name)
        if result is not None:
            return result
        raise AttributeError(
            f"module {__name__!r} has no attribute {name!r}"
        )
    if name == "TaskLoopEventHandler":
        from openjiuwen.deepagents.task_loop.task_loop_event_handler import (
            TaskLoopEventHandler,
        )
        return TaskLoopEventHandler
    if name == "LoopCoordinator":
        from openjiuwen.deepagents.task_loop.loop_coordinator import (
            LoopCoordinator,
        )
        return LoopCoordinator
    if name == "LoopQueues":
        from openjiuwen.deepagents.task_loop.loop_queues import (
            LoopQueues,
        )
        return LoopQueues
    if name == "TaskLoopController":
        from openjiuwen.deepagents.task_loop.task_loop_controller import (
            TaskLoopController,
        )
        return TaskLoopController
    raise AttributeError(
        f"module {__name__!r} has no attribute {name!r}"
    )
