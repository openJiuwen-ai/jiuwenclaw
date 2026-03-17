# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Parallel operation for concurrent execution."""

import asyncio
from typing import List
from .base_op import BaseOp
from ..context import RuntimeContext


class ParallelOp(BaseOp):
    """Parallel composition of operations.

    Executes operations concurrently using asyncio.gather().
    Created using the | operator: Op1() | Op2() | Op3()
    """

    def __init__(self, *ops: BaseOp):
        """Initialize parallel operation.

        Args:
            *ops: Operations to execute in parallel
        """
        super().__init__()
        self._ops: List[BaseOp] = list(ops)

    async def async_execute(self, context: RuntimeContext) -> None:
        """Execute all operations in parallel.

        Args:
            context: Runtime context shared by all operations
        """
        # Execute all operations concurrently
        await asyncio.gather(*[op(context) for op in self._ops])

    def __or__(self, other: BaseOp) -> "ParallelOp":
        """Add another operation to parallel execution.

        Args:
            other: Operation to add

        Returns:
            Extended parallel operation
        """
        if isinstance(other, ParallelOp):
            # Flatten nested parallel operations
            return ParallelOp(*self._ops, *other._ops)
        return ParallelOp(*self._ops, other)

    def __repr__(self) -> str:
        """String representation."""
        ops_str = " | ".join(repr(op) for op in self._ops)
        return f"({ops_str})"
