# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""
Logging Utility Functions

This module provides logging-related utility functions, optimized for async environments (e.g., asyncio).
Uses contextvars instead of threading.local() to support async context isolation.
"""

import contextvars
import os
from typing import (
    Any,
    Optional,
)

from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.security.path_checker import is_sensitive_path

# Use ContextVar instead of threading.local() to support async environments
# ContextVar maintains context isolation in async call chains, each coroutine has independent context
_trace_id_context: contextvars.ContextVar[str] = contextvars.ContextVar("trace_id", default="default_trace_id")


def set_session_id(trace_id: str = "default_trace_id") -> None:
    """
    Set trace_id in current context

    In async environments, this sets trace_id in current coroutine and its child coroutines.
    Each coroutine has an independent context copy that does not interfere with each other.

    Args:
        trace_id: Trace ID for log correlation and tracing

    Note:
        Function name remains "thread_session" for backward compatibility,
        but actual implementation uses contextvars to support async environments.
    """
    _trace_id_context.set(trace_id)


def get_session_id() -> Optional[str]:
    """
    Get trace_id from current context

    In async environments, this returns the trace_id from current coroutine context.
    If not set, returns default value 'default_trace_id'.

    Returns:
        trace_id from current context, or default value if not set

    Note:
        Function name remains "thread_session" for backward compatibility,
        but actual implementation uses contextvars to support async environments.
    """
    try:
        return _trace_id_context.get()
    except LookupError:
        # If no value in context, return default value
        return "default_trace_id"


def get_log_max_bytes(max_bytes_config: Any) -> int:
    try:
        max_bytes = int(max_bytes_config)
    except (ValueError, TypeError) as e:
        raise build_error(
            StatusCode.COMMON_LOG_CONFIG_INVALID,
            error_msg=f"invalid max_bytes configuration: {max_bytes_config}, error: {e}"
        ) from e

    default_log_max_bytes = 100 * 1024 * 1024
    if max_bytes <= 0 or max_bytes > default_log_max_bytes:
        max_bytes = default_log_max_bytes

    return max_bytes


def normalize_and_validate_log_path(path_value: Any) -> str:
    """
    Normalize log path (realpath -> abspath) and check sensitivity.

    This helper is shared by logger config and default logger implementation.
    It raises BaseError when:
      - the value type is invalid, or
      - the normalized path is considered sensitive/unsafe.
    """
    # Support str / PathLike, and guard against invalid types / empty values
    try:
        path_str = os.fspath(path_value)
    except TypeError:
        raise build_error(
            StatusCode.COMMON_LOG_PATH_INVALID,
            error_msg=f'the path_value is {path_value}'
        ) from e

    if not path_str or str(path_str).strip() == "":
        raise build_error(
            StatusCode.COMMON_LOG_PATH_INVALID,
            error_msg=f'the path_str is {path_str}'
        )

    try:
        real_path = os.path.realpath(path_str)
    except OSError:
        real_path = os.path.abspath(os.path.expanduser(path_str))

    if is_sensitive_path(real_path):
        raise build_error(
            StatusCode.COMMON_LOG_PATH_INVALID,
            error_msg=f'the real_path is {real_path}'
        )

    return real_path

