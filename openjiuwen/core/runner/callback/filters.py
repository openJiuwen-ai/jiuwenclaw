# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""
Callback Framework Filters

Event filtering system for callback execution control.
"""

import asyncio
import logging
import time
from collections import (
    defaultdict,
    deque,
)
from typing import (
    Callable,
    Dict,
    Optional,
)

from openjiuwen.core.runner.callback.enums import FilterAction
from openjiuwen.core.runner.callback.models import FilterResult


class EventFilter:
    """Base class for event filters.

    Filters can intercept and modify callback execution before it occurs.
    Subclass this to create custom filters.

    Attributes:
        name: Human-readable name for the filter
    """

    def __init__(self, name: str = ""):
        """Initialize the event filter.

        Args:
            name: Optional name, defaults to class name
        """
        self.name = name or self.__class__.__name__

    async def filter(
            self,
            event: str,
            callback: Callable,
            *args,
            **kwargs
    ) -> FilterResult:
        """Filter logic to execute before callback.

        Override this method to implement custom filtering logic.

        Args:
            event: Event name being processed
            callback: Callback about to be executed
            *args: Positional arguments for callback
            **kwargs: Keyword arguments for callback

        Returns:
            FilterResult indicating action to take
        """
        return FilterResult(FilterAction.CONTINUE)


class RateLimitFilter(EventFilter):
    """Filter to limit callback execution rate.

    Prevents callbacks from executing too frequently within a time window.
    Thread-safe implementation using deque.

    Attributes:
        max_calls: Maximum allowed calls within time window
        time_window: Time window in seconds
    """

    def __init__(self, max_calls: int, time_window: float, name: str = "RateLimit"):
        """Initialize rate limit filter.

        Args:
            max_calls: Maximum number of calls allowed
            time_window: Time window in seconds
            name: Optional custom name
        """
        super().__init__(name)
        self.max_calls = max_calls
        self.time_window = time_window
        self._call_times: Dict[str, deque] = defaultdict(
            lambda: deque(maxlen=max_calls)
        )
        self._lock = asyncio.Lock()

    async def filter(
            self,
            event: str,
            callback: Callable,
            *args,
            **kwargs
    ) -> FilterResult:
        """Check if callback execution should be rate limited.

        Args:
            event: Event name
            callback: Callback to check
            *args: Callback arguments
            **kwargs: Callback keyword arguments

        Returns:
            CONTINUE if within rate limit, SKIP if exceeded
        """
        current_time = time.time()
        key = f"{event}:{callback.__name__}"

        async with self._lock:
            # Remove expired timestamps
            while (self._call_times[key] and
                   current_time - self._call_times[key][0] > self.time_window):
                self._call_times[key].popleft()

            # Check rate limit
            if len(self._call_times[key]) >= self.max_calls:
                return FilterResult(
                    FilterAction.SKIP,
                    reason=f"Rate limit exceeded: {self.max_calls} calls per {self.time_window}s"
                )

            # Record this call
            self._call_times[key].append(current_time)

        return FilterResult(FilterAction.CONTINUE)


class CircuitBreakerFilter(EventFilter):
    """Circuit breaker pattern implementation.

    Prevents execution of failing callbacks after a threshold is reached.
    Automatically attempts to reset after a timeout period.

    Attributes:
        failure_threshold: Number of failures before opening circuit
        timeout: Time to wait before attempting reset
    """

    def __init__(
            self,
            failure_threshold: int = 5,
            timeout: float = 60.0,
            name: str = "CircuitBreaker"
    ):
        """Initialize circuit breaker filter.

        Args:
            failure_threshold: Failures before opening circuit
            timeout: Seconds before reset attempt
            name: Optional custom name
        """
        super().__init__(name)
        self.failure_threshold = failure_threshold
        self.timeout = timeout
        self._failures: Dict[str, int] = defaultdict(int)
        self._last_failure_time: Dict[str, float] = {}
        self._is_open: Dict[str, bool] = defaultdict(bool)
        self._lock = asyncio.Lock()

    @property
    def failures(self):
        return self._failures

    async def filter(
            self,
            event: str,
            callback: Callable,
            *args,
            **kwargs
    ) -> FilterResult:
        """Check circuit breaker state.

        Args:
            event: Event name
            callback: Callback to check
            *args: Callback arguments
            **kwargs: Callback keyword arguments

        Returns:
            CONTINUE if circuit closed, SKIP if open
        """
        key = f"{event}:{callback.__name__}"
        current_time = time.time()

        async with self._lock:
            # Check if circuit is open
            if self._is_open[key]:
                # Try to close circuit if timeout passed
                if current_time - self._last_failure_time[key] > self.timeout:
                    self._is_open[key] = False
                    self._failures[key] = 0
                else:
                    return FilterResult(
                        FilterAction.SKIP,
                        reason=f"Circuit breaker open, retry after {self.timeout}s"
                    )

        return FilterResult(FilterAction.CONTINUE)

    async def record_success(self, event: str, callback: Callable) -> None:
        """Record successful execution.

        Args:
            event: Event name
            callback: Callback that succeeded
        """
        key = f"{event}:{callback.__name__}"
        async with self._lock:
            self._failures[key] = 0

    async def record_failure(self, event: str, callback: Callable) -> None:
        """Record failed execution and potentially open circuit.

        Args:
            event: Event name
            callback: Callback that failed
        """
        key = f"{event}:{callback.__name__}"
        current_time = time.time()

        async with self._lock:
            self._failures[key] += 1
            self._last_failure_time[key] = current_time

            if self._failures[key] >= self.failure_threshold:
                self._is_open[key] = True
                logging.warning(f"Circuit breaker opened for {key}")


class ValidationFilter(EventFilter):
    """Filter for validating callback arguments.

    Attributes:
        validator: Validation function that returns bool
    """

    def __init__(
            self,
            validator: Callable[..., bool],
            name: str = "Validation"
    ):
        """Initialize validation filter.

        Args:
            validator: Function to validate arguments
            name: Optional custom name
        """
        super().__init__(name)
        self.validator = validator

    async def filter(
            self,
            event: str,
            callback: Callable,
            *args,
            **kwargs
    ) -> FilterResult:
        """Validate callback arguments.

        Args:
            event: Event name
            callback: Callback to validate
            *args: Arguments to validate
            **kwargs: Keyword arguments to validate

        Returns:
            CONTINUE if valid, SKIP if invalid
        """
        try:
            is_valid = self.validator(*args, **kwargs)
            if not is_valid:
                return FilterResult(
                    FilterAction.SKIP,
                    reason="Argument validation failed"
                )
        except Exception as e:
            return FilterResult(
                FilterAction.SKIP,
                reason=f"Validation error: {e}"
            )

        return FilterResult(FilterAction.CONTINUE)


class LoggingFilter(EventFilter):
    """Filter for logging callback execution.

    Attributes:
        logger: Logger instance to use
    """

    def __init__(self, logger: Optional[logging.Logger] = None, name: str = "Logging"):
        """Initialize logging filter.

        Args:
            logger: Optional logger, creates default if None
            name: Optional custom name
        """
        super().__init__(name)
        self.logger = logger or logging.getLogger(__name__)

    async def filter(
            self,
            event: str,
            callback: Callable,
            *args,
            **kwargs
    ) -> FilterResult:
        """Log callback execution details.

        Args:
            event: Event name
            callback: Callback being executed
            *args: Callback arguments
            **kwargs: Callback keyword arguments

        Returns:
            Always CONTINUE
        """
        self.logger.info(
            f"Event: {event}, Callback: {callback.__name__}, "
            f"Args: {args}, Kwargs: {kwargs}"
        )
        return FilterResult(FilterAction.CONTINUE)


class AuthFilter(EventFilter):
    """Authorization filter for role-based access control.

    Validates that callbacks are executed only by authorized users
    with the required role.

    Attributes:
        required_role: Role required to execute the callback
    """

    def __init__(self, required_role: str, name: str = "Auth"):
        """Initialize authorization filter.

        Args:
            required_role: Required user role
            name: Optional custom name
        """
        super().__init__(name)
        self.required_role = required_role

    async def filter(
            self,
            event: str,
            callback: Callable,
            *args,
            **kwargs
    ) -> FilterResult:
        """Validate user authorization.

        Args:
            event: Event name
            callback: Callback to authorize
            *args: Callback arguments
            **kwargs: Callback keyword arguments (expects 'user_role')

        Returns:
            CONTINUE if authorized, SKIP if not
        """
        # Extract user role from kwargs
        user_role = kwargs.get("user_role", "guest")

        if user_role != self.required_role:
            return FilterResult(
                FilterAction.SKIP,
                reason=f"Unauthorized: requires {self.required_role}, got {user_role}"
            )

        return FilterResult(FilterAction.CONTINUE)


class ParamModifyFilter(EventFilter):
    """Filter for modifying callback arguments.

    Applies a modifier function to transform arguments before
    callback execution.

    Attributes:
        modifier: Function to transform arguments
    """

    def __init__(
            self,
            modifier: Callable[..., tuple],
            name: str = "ParamModify"
    ):
        """Initialize parameter modification filter.

        Args:
            modifier: Function that takes (*args, **kwargs) and returns
                     (new_args, new_kwargs)
            name: Optional custom name
        """
        super().__init__(name)
        self.modifier = modifier

    async def filter(
            self,
            event: str,
            callback: Callable,
            *args,
            **kwargs
    ) -> FilterResult:
        """Modify callback arguments.

        Args:
            event: Event name
            callback: Callback to modify args for
            *args: Original positional arguments
            **kwargs: Original keyword arguments

        Returns:
            MODIFY action with transformed arguments
        """
        try:
            new_args, new_kwargs = self.modifier(*args, **kwargs)
            return FilterResult(
                FilterAction.MODIFY,
                modified_args=new_args,
                modified_kwargs=new_kwargs
            )
        except Exception as e:
            return FilterResult(
                FilterAction.SKIP,
                reason=f"Parameter modification failed: {e}"
            )


class ConditionalFilter(EventFilter):
    """Conditional filter based on custom predicate.

    Executes callback only if a condition is met.

    Attributes:
        condition: Predicate function to evaluate
        action_on_false: Action to take when condition is false
    """

    def __init__(
            self,
            condition: Callable[..., bool],
            action_on_false: FilterAction = FilterAction.SKIP,
            name: str = "Conditional"
    ):
        """Initialize conditional filter.

        Args:
            condition: Function that takes (event, callback, *args, **kwargs)
                      and returns bool
            action_on_false: Action when condition is false (default: SKIP)
            name: Optional custom name
        """
        super().__init__(name)
        self.condition = condition
        self.action_on_false = action_on_false

    async def filter(
            self,
            event: str,
            callback: Callable,
            *args,
            **kwargs
    ) -> FilterResult:
        """Evaluate condition and decide action.

        Args:
            event: Event name
            callback: Callback to check condition for
            *args: Callback arguments
            **kwargs: Callback keyword arguments

        Returns:
            CONTINUE if condition is true, configured action if false
        """
        try:
            if self.condition(event, callback, *args, **kwargs):
                return FilterResult(FilterAction.CONTINUE)
            else:
                return FilterResult(
                    self.action_on_false,
                    reason="Condition not satisfied"
                )
        except Exception as e:
            return FilterResult(
                FilterAction.SKIP,
                reason=f"Condition evaluation failed: {e}"
            )
