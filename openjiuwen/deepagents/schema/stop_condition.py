# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Stop condition definitions for DeepAgent task loop."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, Optional

if TYPE_CHECKING:
    from openjiuwen.core.single_agent.rail.base import AgentCallbackContext


@dataclass
class StopCondition:
    """Conditions that terminate the DeepAgent task loop.

    All fields are optional. When multiple fields are set,
    the loop stops as soon as ANY condition is met (OR
    semantics).

    Attributes:
        max_iterations: Maximum number of task-loop
            iterations.
        max_token_usage: Cumulative token budget across
            all iterations.
        completion_promise: Natural-language description
            of the desired end state (reserved for P1).
        timeout_seconds: Wall-clock timeout in seconds.
        custom: User-supplied predicate evaluated after
            each iteration. Return ``True`` to stop.
    """
    max_iterations: Optional[int] = None
    max_token_usage: Optional[int] = None
    completion_promise: Optional[str] = None
    timeout_seconds: Optional[float] = None
    custom: Optional[Callable[["AgentCallbackContext"], bool]] = field(
        default=None,
        repr=False,
    )
