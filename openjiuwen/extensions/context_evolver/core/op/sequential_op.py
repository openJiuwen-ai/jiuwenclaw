# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Sequential operation for chaining operations."""

from typing import List
from .base_op import BaseOp
from ..context import RuntimeContext


class SequentialOp(BaseOp):
    """Sequential composition of operations.

    Executes operations one after another, passing context between them.
    Created using the >> operator: Op1() >> Op2() >> Op3()
    """

    def __init__(self, *ops: BaseOp):
        """Initialize sequential operation.

        Args:
            *ops: Operations to execute sequentially
        """
        super().__init__()
        self._ops: List[BaseOp] = list(ops)

    async def async_execute(self, context: RuntimeContext) -> None:
        """Execute all operations sequentially.

        Args:
            context: Runtime context passed through all operations
        """
        for op in self._ops:
            await op(context)

    def __rshift__(self, other: BaseOp) -> "SequentialOp":
        """Add another operation to the sequence.

        Args:
            other: Operation to append

        Returns:
            Extended sequential operation
        """
        if isinstance(other, SequentialOp):
            # Flatten nested sequential operations
            return SequentialOp(*self._ops, *other._ops)
        return SequentialOp(*self._ops, other)

    def __repr__(self) -> str:
        """String representation."""
        ops_str = " >> ".join(repr(op) for op in self._ops)
        return f"({ops_str})"
