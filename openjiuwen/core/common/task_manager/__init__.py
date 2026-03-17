# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""
Coroutine Task Manager Package

Modular implementation of the coroutine task management framework.
"""
__all__ = [
    "TaskManager",
    "Task",
    "TaskStatus",
    "TERMINAL_STATES",
    "TaskError",
    "TaskNotFoundError",
    "DuplicateTaskError",
    "get_task_manager",
    "create_task",
    "cancel_group",
    "cancel_all",
    "print_task_tree",
    "get_task_group",
    "set_task_group",
    "get_current_task_id",
    "TaskManagerEvents",
]

from openjiuwen.core.common.task_manager.types import TaskStatus, TERMINAL_STATES
from openjiuwen.core.common.task_manager.exceptions import TaskError, TaskNotFoundError, DuplicateTaskError
from openjiuwen.core.common.task_manager.context import (get_task_group, set_task_group, reset_task_group,
                                                         get_current_task_id)
from openjiuwen.core.common.task_manager.task import Task
from openjiuwen.core.common.task_manager.manager import (
    TaskManager,
    get_task_manager,
    create_task,
    cancel_group,
    cancel_all,
    print_task_tree,
)
from openjiuwen.core.runner.callback.events import TaskManagerEvents
