# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""LoopCoordinator — controls the DeepAgent outer task loop.

Tracks iteration count, token usage, wall-clock time,
and abort flag.  ``should_continue()`` evaluates all
StopCondition fields with OR semantics.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

from openjiuwen.deepagents.schema.stop_condition import (
    StopCondition,
)

logger = logging.getLogger(__name__)


class LoopCoordinator:
    """Coordinates the outer task-loop lifecycle.

    Attributes:
        current_iteration: Number of completed
            iterations (read-only).
        is_aborted: Whether abort was requested
            (read-only).
    """

    def __init__(
        self,
        stop_condition: Optional[StopCondition] = None,
    ) -> None:
        self._stop_condition = (
            stop_condition or StopCondition()
        )
        self._iteration: int = 0
        self._token_usage: int = 0
        self._aborted: bool = False
        self._start_time: float = 0.0

    # -- read-only properties --

    @property
    def current_iteration(self) -> int:
        """Number of completed iterations."""
        return self._iteration

    @property
    def is_aborted(self) -> bool:
        """Whether abort has been requested."""
        return self._aborted

    # -- mutation --

    def reset(self) -> None:
        """Reset for a new invoke cycle."""
        self._iteration = 0
        self._token_usage = 0
        self._aborted = False
        self._start_time = time.monotonic()

    def increment_iteration(self) -> None:
        """Record one completed iteration."""
        self._iteration += 1

    def add_token_usage(self, tokens: int) -> None:
        """Accumulate token consumption.

        Args:
            tokens: Number of tokens used.
        """
        if tokens > 0:
            self._token_usage += tokens

    def request_abort(self) -> None:
        """Signal the loop to stop immediately."""
        self._aborted = True

    # -- stop evaluation --

    def should_continue(self) -> bool:
        """Return True if the loop may proceed.

        Evaluates all StopCondition fields with OR
        semantics — any single condition being met
        causes the loop to stop.
        """
        if self._aborted:
            return False

        sc = self._stop_condition

        if (
            sc.max_iterations is not None
            and self._iteration >= sc.max_iterations
        ):
            return False

        if (
            sc.max_token_usage is not None
            and self._token_usage >= sc.max_token_usage
        ):
            return False

        if sc.timeout_seconds is not None:
            elapsed = time.monotonic() - self._start_time
            if elapsed >= sc.timeout_seconds:
                return False

        if sc.custom is not None:
            try:
                if sc.custom(None):  # type: ignore[arg-type]
                    return False
            except Exception:
                logger.warning(
                    "Custom stop-condition raised an error",
                    exc_info=True,
                )

        return True


__all__ = [
    "LoopCoordinator",
]
