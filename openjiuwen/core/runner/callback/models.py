# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""
Callback Framework Data Models

Defines data structures and dataclasses for callback framework.
"""

import time
from dataclasses import (
    dataclass,
    field,
)
from typing import (
    Any,
    Awaitable,
    Callable,
    Dict,
    List,
    Optional,
    Set,
)

from openjiuwen.core.runner.callback.enums import (
    ChainAction,
    FilterAction,
)


@dataclass
class CallbackMetrics:
    """Performance metrics for callback execution.

    Tracks execution statistics including call counts, timing, and errors.

    Attributes:
        call_count: Total number of executions
        total_time: Cumulative execution time in seconds
        min_time: Fastest execution time
        max_time: Slowest execution time
        error_count: Number of failed executions
        last_call_time: Timestamp of last execution
    """
    call_count: int = 0
    total_time: float = 0.0
    min_time: float = float('inf')
    max_time: float = 0.0
    error_count: int = 0
    last_call_time: Optional[float] = None

    def update(self, execution_time: float, is_error: bool = False) -> None:
        """Update metrics with new execution data.

        Args:
            execution_time: Time taken for execution in seconds
            is_error: Whether the execution resulted in an error
        """
        self.call_count += 1
        self.total_time += execution_time
        self.min_time = min(self.min_time, execution_time)
        self.max_time = max(self.max_time, execution_time)
        self.last_call_time = time.time()
        if is_error:
            self.error_count += 1

    @property
    def avg_time(self) -> float:
        """Calculate average execution time.

        Returns:
            Average execution time in seconds, or 0 if no calls
        """
        return self.total_time / self.call_count if self.call_count > 0 else 0.0

    def to_dict(self) -> Dict[str, Any]:
        """Convert metrics to dictionary format.

        Returns:
            Dictionary containing all metric values
        """
        return {
            "call_count": self.call_count,
            "avg_time": self.avg_time,
            "min_time": self.min_time if self.min_time != float('inf') else 0,
            "max_time": self.max_time,
            "error_count": self.error_count,
            "error_rate": self.error_count / self.call_count if self.call_count > 0 else 0,
            "last_call_time": self.last_call_time
        }


@dataclass
class FilterResult:
    """Result returned by event filters.

    Attributes:
        action: The action to take (CONTINUE, STOP, SKIP, MODIFY)
        modified_args: New positional arguments if action is MODIFY
        modified_kwargs: New keyword arguments if action is MODIFY
        reason: Optional reason for the action taken
    """
    action: FilterAction
    modified_args: Optional[tuple] = None
    modified_kwargs: Optional[dict] = None
    reason: Optional[str] = None


@dataclass
class ChainContext:
    """Execution context for callback chains.

    Provides state management and data sharing across chain execution.

    Attributes:
        event: Name of the event being processed
        initial_args: Original positional arguments
        initial_kwargs: Original keyword arguments
        results: List of results from executed callbacks
        metadata: Arbitrary metadata for sharing data
        current_index: Index of currently executing callback
        is_completed: Whether chain completed successfully
        is_rolled_back: Whether chain was rolled back
        start_time: Timestamp when chain execution started
    """
    event: str
    initial_args: tuple
    initial_kwargs: dict
    results: List[Any] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    current_index: int = 0
    is_completed: bool = False
    is_rolled_back: bool = False
    start_time: float = field(default_factory=time.time)

    def get_last_result(self) -> Any:
        """Get the result from the previous callback.

        Returns:
            Last result in the chain, or None if no results
        """
        return self.results[-1] if self.results else None

    def get_all_results(self) -> List[Any]:
        """Get all results from executed callbacks.

        Returns:
            Copy of all results list
        """
        return self.results.copy()

    def set_metadata(self, key: str, value: Any) -> None:
        """Store metadata in the context.

        Args:
            key: Metadata key
            value: Metadata value
        """
        self.metadata[key] = value

    def get_metadata(self, key: str, default: Any = None) -> Any:
        """Retrieve metadata from the context.

        Args:
            key: Metadata key
            default: Default value if key not found

        Returns:
            Metadata value or default
        """
        return self.metadata.get(key, default)

    @property
    def elapsed_time(self) -> float:
        """Calculate elapsed time since chain start.

        Returns:
            Elapsed time in seconds
        """
        return time.time() - self.start_time


@dataclass
class ChainResult:
    """Result of callback chain execution.

    Attributes:
        action: Final action taken by the chain
        result: Final result value
        context: The chain execution context
        error: Exception if chain failed
    """
    action: ChainAction
    result: Any = None
    context: Optional['ChainContext'] = None
    error: Optional[Exception] = None


@dataclass
class CallbackInfo:
    """Metadata and configuration for a registered callback.

    Attributes:
        callback: The callback function
        priority: Execution priority (higher executes first)
        once: Whether callback should execute only once
        enabled: Whether callback is currently enabled
        namespace: Namespace for grouping callbacks
        tags: Set of tags for filtering
        max_retries: Maximum retry attempts on failure
        retry_delay: Delay between retries in seconds
        timeout: Execution timeout in seconds
        created_at: Timestamp when callback was registered
        wrapper: Optional wrapper function returned by decorator (for unregistering)
        callback_type: Semantic type marker, e.g. "transform"
    """
    callback: Callable[..., Awaitable[Any]]
    priority: int
    once: bool = False
    enabled: bool = True
    namespace: str = "default"
    tags: Set[str] = field(default_factory=set)
    max_retries: int = 0
    retry_delay: float = 0.0
    timeout: Optional[float] = None
    created_at: float = field(default_factory=time.time)
    wrapper: Optional[Callable[..., Awaitable[Any]]] = None
    callback_type: str = ""

    def __hash__(self) -> int:
        """Hash based on callback identity.

        Returns:
            Hash value based on callback id
        """
        return hash(id(self.callback))
