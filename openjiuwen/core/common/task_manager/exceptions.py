# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from typing import Any, Optional

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import ExecutionError


class TaskError(ExecutionError):
    """Base exception for coroutine task errors"""
    pass


class TaskNotFoundError(TaskError):
    """Raised when a task is not found"""
    status = StatusCode.COMMON_TASK_NOT_FOUND

    def __init__(
            self,
            msg: Optional[str] = None,
            *,
            details: Optional[Any] = None,
            cause: Optional[BaseException] = None,
            **kwargs: Any,
    ):
        super().__init__(self.status, msg=msg, details=details, cause=cause, **kwargs)


class DuplicateTaskError(TaskError):
    """Raised when a task with the same ID already exists"""
    status = StatusCode.COMMON_TASK_CONFIG_ERROR

    def __init__(
            self,
            msg: Optional[str] = None,
            *,
            details: Optional[Any] = None,
            cause: Optional[BaseException] = None,
            **kwargs: Any,
    ):
        super().__init__(self.status, msg=msg, details=details, cause=cause, **kwargs)
