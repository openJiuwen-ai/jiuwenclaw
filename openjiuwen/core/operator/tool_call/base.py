# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
Tool invocation operator; tunables are tool descriptions only.

ToolCallOperator integrates tool calls into the Operator system for:
- Tracing via session.tracer()
- Checkpoint via get_state/load_state
- Tunable: tool descriptions only (tool_description) when tool_registry is set
"""

from __future__ import annotations

from typing import Any, Dict, Optional, AsyncIterator, Callable, Awaitable, List, Tuple

from openjiuwen.core.operator.base import Operator, TunableSpec
from openjiuwen.core.session.agent import Session


class ToolCallOperator(Operator):
    """Tool invocation operator; optimizable parameters are tool descriptions only.

    Exposes tool_description tunable when tool_registry is set (tool-description
    self-evolution). Set operator_id on session for tracing; get_state/load_state for checkpoint.
    """

    def __init__(
        self,
        tool: Any = None,
        tool_call_id: str = "tool_call",
        *,
        tool_executor: Optional[Callable[[Any, Session], Awaitable[Tuple[Any, Any]]]] = None,
        tool_registry: Optional[Any] = None,
    ):
        """Initialize tool call operator.

        Args:
            tool: Tool instance for execution
            tool_call_id: Unique operator identifier
            tool_executor: Custom executor for tool_calls in inputs (router pattern)
            tool_registry: Optional object with get_tool_defs() -> List[dict],
                get_tools() -> Dict[tool_name, Tool], and
                set_tool_description(tool_name, description). When set, exposes
                tool_description tunable for tool-description self-evolution.
        """
        self._tool = tool
        self._tool_call_id = tool_call_id
        self._tool_executor = tool_executor
        self._tool_registry = tool_registry
        self._enabled: bool = True
        self._max_retries: int = 0

    @property
    def operator_id(self) -> str:
        """Operator identifier.

        Returns:
            Operator ID string
        """
        return self._tool_call_id

    def get_tunables(self) -> Dict[str, TunableSpec]:
        """Get tunable parameters: single 'tool_description' tunable for all tools.

        Returns:
            Dict with single 'tool_description' key when tool_registry is set; empty otherwise.
            set_parameter('tool_description', value) expects value as Dict[tool_name, description_str].
        """
        if not self._tool_registry:
            return {}

        return {
            "tool_description": TunableSpec(
                name="tool_description",
                kind="text",
                path="tool_description",
                constraint={"type": "dict"},
            )
        }

    def set_parameter(self, target: str, value: Any) -> None:
        """Set tunable parameter value (tool descriptions only).

        Args:
            target: Must be 'tool_description'
            value: Dict[tool_name, description_str] mapping tool names to new descriptions
        """
        if target != "tool_description":
            return
        if not isinstance(value, dict):
            return

        set_desc = getattr(self._tool_registry, "set_tool_description", None) if self._tool_registry else None
        if not callable(set_desc):
            return

        for tool_name, description in value.items():
            set_desc(tool_name, description)

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
        """Execute tool invocation.

        Supports two modes:
        1. Router mode: inputs["tool_calls"] is a list, use tool_executor(tool_call, session)
        2. Direct mode: use self.tool.invoke(inputs, **kwargs)

        Args:
            inputs: Input dict with tool_calls or tool parameters
            session: Session for tracing
            **kwargs: Additional parameters

        Returns:
            Tool execution result(s)

        Raises:
            RuntimeError: if operator is disabled or no tool configured
        """
        if not self._enabled:
            raise RuntimeError(f"ToolCallOperator disabled: {self._tool_call_id}")
        self._set_operator_context(session, self._tool_call_id)
        try:
            # Router mode: inputs["tool_calls"] is a list, use tool_executor(tool_call, session)
            tool_calls = inputs.get("tool_calls")
            if isinstance(tool_calls, list) and self._tool_executor is not None:
                results: List[Tuple[Any, Any]] = []
                for tool_call in tool_calls:
                    last: Tuple[Any, Any] | None = None
                    for _ in range(self._max_retries + 1):
                        last = await self._tool_executor(tool_call, session)
                        # Executor returns (result, ToolMessage); None result means retry
                        if last[0] is not None:
                            break
                    if last is not None:
                        results.append(last)
                return results

            if self._tool is None:
                raise RuntimeError("ToolCallOperator has no tool/tool_executor configured")

            last_err: Optional[Exception] = None
            for attempt in range(self._max_retries + 1):
                try:
                    return await self._tool.invoke(inputs, **kwargs)
                except Exception as e:
                    last_err = e
                    if attempt >= self._max_retries:
                        raise
            if last_err is not None:
                raise last_err
            raise RuntimeError("tool invoke failed without exception")
        finally:
            self._set_operator_context(session, None)

    async def stream(self, inputs: Dict[str, Any], session: Session, **kwargs: Any) -> AsyncIterator[Any]:
        """Stream tool invocation (if supported).

        Args:
            inputs: Input dict with tool parameters
            session: Session for tracing
            **kwargs: Additional parameters

        Yields:
            Stream chunks

        Raises:
            NotImplementedError: if tool does not support streaming
        """
        if not hasattr(self._tool, "stream"):
            raise NotImplementedError("tool stream not implemented")
        self._set_operator_context(session, self._tool_call_id)
        try:
            async for chunk in self._tool.stream(inputs, **kwargs):
                yield chunk
        finally:
            self._set_operator_context(session, None)

