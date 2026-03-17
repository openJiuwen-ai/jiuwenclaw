# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
Unified Operator abstraction for agent self-evolution.

Operators are atomic executable and optimizable units:
- One LLM call, one tool call, one memory operation
- Execution via invoke/stream with Session (trajectory via session.tracer())
- Optimization via get_tunables, set_parameter, get_state/load_state
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional
from typing import AsyncIterator

from openjiuwen.core.session.agent import Session


# TunableSpec kind: "prompt" | "continuous" | "discrete" | "tool_selector" | "memory_selector"
TunableKind = str


class TunableSpec:
    """Describes a single tunable parameter of an operator.

    Attributes:
        name: Parameter name
        kind: Tunable type (prompt, continuous, discrete, etc.)
        path: Path to the parameter in the operator
        constraint: Optional constraints for the parameter
    """

    __slots__ = ("name", "kind", "path", "constraint")

    def __init__(
        self,
        name: str,
        kind: TunableKind,
        path: str,
        constraint: Optional[Any] = None,
    ):
        self.name = name
        self.kind = kind
        self.path = path
        self.constraint = constraint


class Operator(ABC):
    """Base class for atomic execution and optimization units.

    Execution: invoke/stream with session (trajectory via session.tracer()).
    Optimization: get_tunables, set_parameter, get_state/load_state.
    """

    @property
    @abstractmethod
    def operator_id(self) -> str:
        """Unique identifier within a trajectory for tracing and attribution.

        Returns:
            Operator identifier string
        """
        ...

    @abstractmethod
    def get_tunables(self) -> Dict[str, TunableSpec]:
        """Describe tunable parameters (e.g., prompt path, temperature, tool_filter).

        Returns:
            Dict mapping tunable names to TunableSpec
        """
        ...

    @abstractmethod
    def set_parameter(self, target: str, value: Any) -> None:
        """Set a parameter to the optimized value.

        The optimizer computes the new value; this method applies it.
        For prompt-type tunables, value is the new content (str or list).
        For continuous params, value is the new numeric value.

        Args:
            target: Parameter name to set
            value: New value to apply
        """
        ...

    @abstractmethod
    def get_state(self) -> Dict[str, Any]:
        """Get current state for rollback, snapshot, or version comparison.

        Returns:
            State dict for serialization
        """
        ...

    @abstractmethod
    def load_state(self, state: Dict[str, Any]) -> None:
        """Restore operator state from serialized dict.

        Args:
            state: State dict to restore from
        """
        ...

    @abstractmethod
    async def invoke(
        self,
        inputs: Dict[str, Any],
        session: Session,
        **kwargs: Any,
    ) -> Any:
        """Execute one step.

        Trajectory is written via session.tracer(), not callback.

        Args:
            inputs: Input data for this step
            session: Session for state and tracing
            **kwargs: Additional execution parameters

        Returns:
            Execution result
        """
        ...

    async def stream(
        self,
        inputs: Dict[str, Any],
        session: Session,
        **kwargs: Any,
    ) -> AsyncIterator[Any]:
        """Optional streaming execution.

        Args:
            inputs: Input data for this step
            session: Session for state and tracing
            **kwargs: Additional execution parameters

        Yields:
            Stream chunks

        Raises:
            NotImplementedError: if streaming not supported
        """
        raise NotImplementedError("stream not implemented")

    def _set_operator_context(self, session: Session, context_id: Optional[str] = None) -> None:
        """Set operator context on session for tracing.

        Args:
            session: Session to configure
            context_id: Operator ID to set, or None to clear the context
        """
        if hasattr(session, "set_current_operator_id"):
            session.set_current_operator_id(context_id)
