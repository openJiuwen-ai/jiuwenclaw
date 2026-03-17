# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Base operation class for all operations in the system."""

from abc import ABC, abstractmethod
from typing import Any, Optional
from openjiuwen.core.common.logging import context_engine_logger as logger

from ..context import RuntimeContext, ServiceContext


class BaseOp(ABC):
    """Base class for all operations.

    Operations are atomic units of computation that can be composed
    into flows using the >> (sequential) and | (parallel) operators.
    """

    def __init__(self, **kwargs):
        """Initialize operation with optional parameters.

        Args:
            **kwargs: Operation-specific parameters
        """
        self._params = kwargs
        self._service_context = ServiceContext()

    async def __call__(self, context: RuntimeContext) -> RuntimeContext:
        """Execute the operation.

        Args:
            context: Runtime context with input data

        Returns:
            Updated runtime context with output data
        """
        op_name = self.__class__.__name__
        logger.debug("Executing operation: %s", op_name)

        try:
            await self.async_execute(context)
            logger.debug("Operation %s completed successfully", op_name)
        except Exception as e:
            logger.error("Operation %s failed: %s", op_name, e)
            raise

        return context

    @abstractmethod
    async def async_execute(self, context: RuntimeContext) -> None:
        """Execute the operation logic.

        Subclasses must implement this method to define their behavior.
        Operations should read from and write to the context object.

        Args:
            context: Runtime context for reading inputs and writing outputs
        """
        pass

    @property
    def llm(self) -> Optional[Any]:
        """Get LLM service from service context."""
        return self._service_context.llm

    @property
    def embedding_model(self) -> Optional[Any]:
        """Get embedding model from service context."""
        return self._service_context.embedding_model

    @property
    def vector_store(self) -> Optional[Any]:
        """Get vector store from service context."""
        return self._service_context.vector_store

    def __rshift__(self, other: "BaseOp") -> "SequentialOp":
        """Sequential composition operator (>>).

        Args:
            other: Next operation to execute

        Returns:
            Sequential operation combining self and other
        """
        from openjiuwen.extensions.context_evolver.core.op.sequential_op import SequentialOp
        return SequentialOp(self, other)

    def __or__(self, other: "BaseOp") -> "ParallelOp":
        """Parallel composition operator (|).

        Args:
            other: Operation to execute in parallel

        Returns:
            Parallel operation combining self and other
        """
        from openjiuwen.extensions.context_evolver.core.op.parallel_op import ParallelOp
        return ParallelOp(self, other)

    def __repr__(self) -> str:
        """String representation."""
        params_str = ", ".join(f"{k}={v}" for k, v in self._params.items())
        if params_str:
            return f"{self.__class__.__name__}({params_str})"
        return f"{self.__class__.__name__}()"
