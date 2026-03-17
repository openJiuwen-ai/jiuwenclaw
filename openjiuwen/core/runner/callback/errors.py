# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""
Callback Framework Error Classes

Defines exception classes for controlling callback execution flow.
"""

from typing import Any, Optional

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import ExecutionError


class AbortError(ExecutionError):
    """Exception to abort callback execution and propagate the error out of trigger().

    When raised in a callback, stops further callback execution and propagates
    out of trigger(). If cause is provided, trigger() re-raises cause instead of
    AbortError, so the caller sees the original exception.

    Attributes:
        reason: Human-readable reason for abort
        cause: Optional inner exception to re-raise at trigger() boundary

    Examples:
        Abort and re-raise inner exception (caller sees ValueError)::

            @framework.on("process")
            async def validator(data):
                try:
                    risky_op(data)
                except ValueError as e:
                    raise AbortError("validation failed", cause=e)

        Abort without inner exception (caller sees AbortError)::

            @framework.on("process")
            async def gate(data):
                if not allowed(data):
                    raise AbortError("access denied")
    """

    recoverable = False
    fatal = False

    def __init__(
        self,
        reason: str = "",
        *,
        cause: Optional[Exception] = None,
        details: Optional[Any] = None,
    ):
        self.reason = reason
        super().__init__(
            StatusCode.CALLBACK_EXECUTION_ABORTED,
            details=details,
            cause=cause,
            reason=reason,
        )
