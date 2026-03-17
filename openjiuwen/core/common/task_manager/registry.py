# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from collections import defaultdict
from typing import Dict, List, Optional, Set
from weakref import WeakValueDictionary

from openjiuwen.core.common.task_manager.task import Task
from openjiuwen.core.common.task_manager.types import TaskStatus


class TaskRegistry:
    """Registry for coroutine tasks using WeakValueDictionary for auto-cleanup"""

    def __init__(self) -> None:
        self._tasks: WeakValueDictionary[str, Task] = WeakValueDictionary()
        self._groups: Dict[str, Set[str]] = defaultdict(set)

    def add(self, task: Task) -> None:
        self._tasks[task.task_id] = task
        if task.group:
            self._groups[task.group].add(task.task_id)

    def get(self, task_id: str) -> Optional[Task]:
        return self._tasks.get(task_id)

    def contains(self, task_id: str) -> bool:
        return task_id in self._tasks

    def get_by_group(self, group: str) -> List[Task]:
        return [self._tasks[tid] for tid in self._groups.get(group, set()) if tid in self._tasks]

    def get_by_parent(self, parent_id: str) -> List[Task]:
        return [t for t in self._tasks.values() if t.parent_task_id == parent_id]

    def get_by_status(self, status: TaskStatus) -> List[Task]:
        """Get all tasks with the given status"""
        return [t for t in self._tasks.values() if t.status == status]

    def get_running(self) -> List[Task]:
        """Get all running tasks"""
        return self.get_by_status(TaskStatus.RUNNING)

    def get_all(self) -> List[Task]:
        return list(self._tasks.values())

    def keys(self):
        return self._tasks.keys()

    def items(self):
        return self._tasks.items()

    def values(self):
        return self._tasks.values()

    def pop(self, task_id: str, default=None):
        return self._tasks.pop(task_id, default)

    def remove_unsafe(self, task_id: str) -> None:
        """Remove task without holding lock (caller must hold lock)"""
        task = self._tasks.pop(task_id, None)
        if not task:
            return
        if task.group:
            self._groups[task.group].discard(task_id)
            if not self._groups[task.group]:
                del self._groups[task.group]

    def _remove_unsafe(self, task_id: str) -> None:
        """Deprecated: use remove_unsafe instead"""
        self.remove_unsafe(task_id)

    def get_group_task_ids(self, group: str) -> List[str]:
        return list(self._groups.get(group, set()))
