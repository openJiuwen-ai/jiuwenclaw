# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import contextvars
from contextvars import Token
from typing import Optional

import anyio


_current_task_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "current_task_id", default=None
)

_root_task_group: contextvars.ContextVar[Optional[anyio.abc.TaskGroup]] = contextvars.ContextVar(
    "root_task_group", default=None
)


def get_task_group() -> Optional[anyio.abc.TaskGroup]:
    """Get the current task group from context"""
    return _root_task_group.get()


def set_task_group(tg: Optional[anyio.abc.TaskGroup]) -> Token:
    """Set the current task group, returns token for restoration"""
    return _root_task_group.set(tg)


def reset_task_group(token: Token) -> None:
    """Reset the task group to its previous state using the token"""
    _root_task_group.reset(token)


def get_current_task_id() -> Optional[str]:
    """Get the current task ID from context"""
    return _current_task_id.get()
