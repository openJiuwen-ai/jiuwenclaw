# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
Memory invocation operator with enabled/retries tunables.

MemoryCallOperator integrates memory read/write/retrieval into the Operator system for:
- Tracing via session.tracer()
- Checkpoint via get_state/load_state
- Tunable enabled/retries parameters
"""

from __future__ import annotations

from typing import Any, Dict, AsyncIterator, Optional, Callable, Awaitable

from openjiuwen.core.operator.base import Operator, TunableSpec
from openjiuwen.core.session.agent import Session


class MemoryCallOperator(Operator):
    """Memory invocation operator with enabled/retries tunables.

    Current responsibilities:
    - Set operator_id on session before execution for tracing
    - Provide get_state/load_state for checkpoint

    Future extensions:
    - get_tunables() for retrieval strategy (top_k, query rewrite), write strategy
    """

    def __init__(
        self,
        memory: Any = None,
        memory_call_id: str = "memory_call",
        *,
        memory_invoke: Optional[Callable[[Dict[str, Any]], Awaitable[Any]]] = None,
    ):
        """Initialize memory call operator.

        Args:
            memory: Memory instance for execution
            memory_call_id: Unique operator identifier
            memory_invoke: Custom invoke callback (for non-standard interfaces like LongTermMemory.search_user_mem)
        """
        self._memory = memory
        self._memory_call_id = memory_call_id
        self._memory_invoke = memory_invoke
        self._enabled: bool = True
        self._max_retries: int = 0

    @property
    def operator_id(self) -> str:
        """Operator identifier.

        Returns:
            Operator ID string
        """
        return self._memory_call_id

    def get_tunables(self) -> Dict[str, TunableSpec]:
        """Get tunable parameters.

        Returns:
            Dict with enabled and max_retries tunables
        """
        return {
            "enabled": TunableSpec(
                name="enabled",
                kind="discrete",
                path="enabled",
                constraint={"type": "bool"},
            ),
            "max_retries": TunableSpec(
                name="max_retries",
                kind="discrete",
                path="max_retries",
                constraint={"type": "int", "min": 0, "max": 5},
            ),
        }

    def set_parameter(self, target: str, value: Any) -> None:
        """Set tunable parameter value.

        Args:
            target: Parameter name (enabled or max_retries)
            value: New value to set
        """
        if target == "enabled":
            self._enabled = bool(value)
        if target == "max_retries":
            v = int(value)
            self._max_retries = max(0, min(5, v))

    def get_state(self) -> Dict[str, Any]:
        """Get current state for checkpoint.

        Returns:
            Dict with enabled and max_retries
        """
        return {"enabled": self._enabled, "max_retries": self._max_retries}

    def load_state(self, state: Dict[str, Any]) -> None:
        """Restore state from checkpoint.

        Args:
            state: State dict with enabled and/or max_retries
        """
        if "enabled" in state:
            self._enabled = bool(state["enabled"])
        if "max_retries" in state:
            self._max_retries = max(0, min(5, int(state["max_retries"])))

    async def invoke(self, inputs: Dict[str, Any], session: Session, **kwargs: Any) -> Any:
        """Execute memory invocation.

        Supports two modes:
        1. memory_invoke(inputs) callback (for non-standard interfaces)
        2. memory.invoke(inputs, **kwargs) (traditional mode)

        Args:
            inputs: Input dict for memory operation
            session: Session for tracing
            **kwargs: Additional parameters

        Returns:
            Memory operation result

        Raises:
            RuntimeError: if operator is disabled or no memory configured
        """
        if not self._enabled:
            raise RuntimeError(f"MemoryCallOperator disabled: {self._memory_call_id}")
        self._set_operator_context(session, self._memory_call_id)
        try:
            # Support two modes:
            # 1) memory_invoke(inputs) callback (for non-standard interfaces like LongTermMemory.search_user_mem)
            # 2) memory.invoke(inputs, **kwargs) (traditional mode)
            last_err: Exception | None = None
            for attempt in range(self._max_retries + 1):
                try:
                    if self._memory_invoke is not None:
                        return await self._memory_invoke(inputs)
                    if self._memory is None:
                        raise RuntimeError("MemoryCallOperator has no memory/memory_invoke configured")
                    return await self._memory.invoke(inputs, **kwargs)
                except Exception as e:
                    last_err = e
                    if attempt >= self._max_retries:
                        raise
            if last_err is not None:
                raise last_err
            raise RuntimeError("memory invoke failed without exception")
        finally:
            self._set_operator_context(session, None)

    async def stream(self, inputs: Dict[str, Any], session: Session, **kwargs: Any) -> AsyncIterator[Any]:
        """Stream memory invocation (if supported).

        Args:
            inputs: Input dict for memory operation
            session: Session for tracing
            **kwargs: Additional parameters

        Yields:
            Stream chunks

        Raises:
            NotImplementedError: if memory does not support streaming
        """
        if not hasattr(self._memory, "stream"):
            raise NotImplementedError("memory stream not implemented")
        self._set_operator_context(session, self._memory_call_id)
        try:
            async for chunk in self._memory.stream(inputs, **kwargs):
                yield chunk
        finally:
            self._set_operator_context(session, None)

