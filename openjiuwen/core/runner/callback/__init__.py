# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""
Comprehensive Async Callback Framework

A production-ready callback framework designed exclusively for asyncio environments,
featuring filtering, chaining, metrics, hooks, and circuit breakers.
"""

# Chain
from openjiuwen.core.runner.callback.chain import CallbackChain
# Errors
from openjiuwen.core.runner.callback.errors import AbortError
# Enumerations
from openjiuwen.core.runner.callback.enums import (
    ChainAction,
    FilterAction,
    HookType,
)
# Events
from openjiuwen.core.runner.callback.events import (
    AgentEvents,
    ContextEvents,
    LLMCallEvents,
    MemoryEvents,
    RetrievalEvents,
    SessionEvents,
    ToolCallEvents,
    WorkflowEvents,
)
# Filters
from openjiuwen.core.runner.callback.filters import (
    AuthFilter,
    CircuitBreakerFilter,
    ConditionalFilter,
    EventFilter,
    LoggingFilter,
    ParamModifyFilter,
    RateLimitFilter,
    ValidationFilter,
)
# Main Framework
from openjiuwen.core.runner.callback.framework import AsyncCallbackFramework
# Utility functions
from openjiuwen.core.runner.callback.utils import (
    trigger,
    get_callback_framework,
    lazy_callback_framework,
)
# Data Models
from openjiuwen.core.runner.callback.models import (
    CallbackInfo,
    CallbackMetrics,
    ChainContext,
    ChainResult,
    FilterResult,
)

__all__ = [
    # Enumerations
    "FilterAction",
    "ChainAction",
    "HookType",
    # Events
    "AgentEvents",
    "ContextEvents",
    "LLMCallEvents",
    "MemoryEvents",
    "RetrievalEvents",
    "SessionEvents",
    "ToolCallEvents",
    "WorkflowEvents",
    # Data Models
    "CallbackMetrics",
    "FilterResult",
    "ChainContext",
    "ChainResult",
    "CallbackInfo",
    # Filters
    "EventFilter",
    "RateLimitFilter",
    "CircuitBreakerFilter",
    "ValidationFilter",
    "LoggingFilter",
    "AuthFilter",
    "ParamModifyFilter",
    "ConditionalFilter",
    # Chain
    "CallbackChain",
    # Errors
    "AbortError",
    # Framework
    "AsyncCallbackFramework",
    # Utility functions
    "trigger",
    "get_callback_framework",
    "lazy_callback_framework",
]

