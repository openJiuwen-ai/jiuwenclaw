# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""
Callback Framework Enumerations

Defines enumeration types for controlling callback execution flow.
"""

from enum import Enum


class FilterAction(Enum):
    """Actions that filters can return to control callback execution.

    Attributes:
        CONTINUE: Continue with callback execution
        STOP: Stop the entire event processing
        SKIP: Skip current callback and continue to next
        MODIFY: Modify arguments and continue
    """
    CONTINUE = "continue"
    STOP = "stop"
    SKIP = "skip"
    MODIFY = "modify"


class ChainAction(Enum):
    """Actions that callbacks can return to control chain execution.

    Attributes:
        CONTINUE: Continue to next callback in chain
        BREAK: Break the chain and return current result
        RETRY: Retry current callback
        ROLLBACK: Rollback all executed callbacks
    """
    CONTINUE = "continue"
    BREAK = "break"
    RETRY = "retry"
    ROLLBACK = "rollback"


class HookType(Enum):
    """Types of hooks that can be registered for lifecycle events.

    Attributes:
        BEFORE: Executed before event processing
        AFTER: Executed after event processing
        ERROR: Executed when an error occurs
        CLEANUP: Executed during cleanup phase
    """
    BEFORE = "before"
    AFTER = "after"
    ERROR = "error"
    CLEANUP = "cleanup"
