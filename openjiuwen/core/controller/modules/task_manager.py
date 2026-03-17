# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
Task Manager Module

This module implements the core functionality of task management, including:
- TaskManagerState: Task manager state for serialization and restoration
- TaskManager: Task manager responsible for CRUD operations, status management,
priority management, and hierarchical relationship management

Main Features:
- Task CRUD operations (Create, Read, Update, Delete)
- Task execution status management (submitted, working, paused, completed, etc.)
- Task priority management (supports querying and sorting by priority)
- Task hierarchical relationship management (parent-child task relationships)

Index Structure:
- _priority_index: Priority index for fast task lookup by priority
- _parent_to_children: Parent-to-children relationship index for fast child task lookup
- _child_to_parent: Child-to-parent relationship index for fast parent task lookup
- _root_tasks: Root task set for fast root task lookup
"""

import asyncio
from typing import (
    Callable, Dict, List, Union, Set, Optional,
)
from collections import defaultdict
from pydantic import BaseModel, model_validator
from typing_extensions import Literal

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.controller.schema import Task, TaskStatus
from openjiuwen.core.controller.config import ControllerConfig


class TaskManagerState(BaseModel):
    """Task Manager State

    Used for serialization and restoration of task manager state.
    """
    tasks: Dict[str, Task]
    priority_index: Dict[int, List[str]]
    parent_to_children: Dict[str, Set[str]]
    children_to_parent: Dict[str, str]
    root_tasks: Set[str]


class TaskFilter(BaseModel):
    """Task Filter

    Used to filter tasks in get_task, remove_task, and pop_task methods.
    All fields are optional and can be combined for complex filtering.

    task_id: Task ID, can be a single ID or a list of IDs
    session_id: Session ID
    user_id: User ID
    priority: Priority
    status: Task execution status
    with_children: Whether to include child tasks
    is_root: Whether to only query root tasks
    """

    task_id: Optional[Union[str, List[str]]] = None
    session_id: Optional[str] = None
    user_id: Optional[str] = None
    priority: Optional[Union[int, Literal["highest"]]] = None
    status: Optional[TaskStatus] = None
    with_children: bool = False
    is_root: bool = False

    @model_validator(mode='after')
    def validate_at_least_one_filter(self):
        """Validate that at least one filter parameter is not None
        
        Ensures that at least one of the filter parameters (task_id, session_id,
        user_id, priority, status, or is_root) is set to a non-None value.
        
        Returns:
            TaskFilter: The validated TaskFilter instance
            
        Raises:
            BaseError: If all filter parameters are None
        """
        params_to_check = [
            self.task_id,
            self.session_id,
            self.user_id,
            self.priority,
            self.status
        ]

        all_params_empty = (
                all(param is None for param in params_to_check) and
                not self.is_root
        )
        if all_params_empty:
            raise build_error(
                StatusCode.AGENT_CONTROLLER_TASK_PARAM_ERROR,
                error_msg="At least one filter parameter (task_id, session_id, "
                          "user_id, priority, status, or is_root) must be provided"
            )
        return self



class TaskManager:
    """Task Manager

    Responsible for task CRUD operations, status management, priority management,
    and hierarchical relationship management.
    Provides efficient index structures for fast task queries.
    """

    def __init__(self, config):
        """Initialize task manager

        Args:
            config: Agent configuration dictionary
        """
        self._config: ControllerConfig = config
        self.tasks: Dict[str, Task] = {}  # task_id -> Task

        # ==================== Priority Index ====================
        # Priority index: priority -> List[task_id]
        # Used for fast task lookup and sorting by priority
        self._priority_index: Dict[int, List[str]] = defaultdict(list)  # priority_index -> List[Task]

        # ==================== Hierarchical Relationship Index ====================
        # Parent-to-children relationship index: parent_task_id -> Set[child_task_id]
        # Used for fast lookup of all direct child tasks of a task
        self._parent_to_children: Dict[str, Set[str]] = defaultdict(set)

        # Child-to-parent relationship index: child_task_id -> parent_task_id
        # Used for fast lookup of a task's parent task
        self._child_to_parent: Dict[str, str] = {}

        # Root task set: stores all task IDs that have no parent task
        # Used for fast lookup of root tasks (is_root=True queries)
        self._root_tasks: Set[str] = set()

        # Async lock for thread-safe operations in parallel scenarios
        self._lock: asyncio.Lock = asyncio.Lock()

        # Synchronous callback invoked (outside lock)
        # when a task enters SUBMITTED status.
        self._on_task_submitted: Optional[
            Callable[[], None]
        ] = None

    def set_on_task_submitted(
        self, callback: Optional[Callable[[], None]]
    ) -> None:
        """Register callback for task-submitted events.

        Args:
            callback: Callable to invoke when a task
                enters SUBMITTED status, or None to
                clear.
        """
        self._on_task_submitted = callback

    def _notify_if_submitted(
        self, tasks: List[Task],
    ) -> None:
        """Fire callback if any task is SUBMITTED.

        Called outside the lock after add_task or
        update_task_status modifies task state.

        Args:
            tasks: Tasks that were just added/updated.
        """
        if self._on_task_submitted is None:
            return
        for t in tasks:
            if t.status == TaskStatus.SUBMITTED:
                self._on_task_submitted()
                return

    @property
    def config(self):
        """Get configuration"""
        return self._config
    
    @config.setter
    def config(self, config: ControllerConfig):
        """Update configuration"""
        self._config = config

    async def get_state(self) -> TaskManagerState:
        """Get task manager state

        Returns:
            TaskManagerState: Task manager state object
        """
        async with self._lock:
            return TaskManagerState(
                tasks={k: v.model_copy(deep=True) for k, v in self.tasks.items()},
                priority_index=dict(self._priority_index),
                parent_to_children={k: v.copy() for k, v in self._parent_to_children.items()},
                children_to_parent=self._child_to_parent.copy(),
                root_tasks=self._root_tasks.copy()
            )

    async def load_state(self, state: TaskManagerState) -> None:
        """Load task manager state

        Args:
            state: Task manager state object
        """
        async with self._lock:
            self.tasks = state.tasks.copy()
            self._priority_index = defaultdict(list, state.priority_index)
            self._parent_to_children = defaultdict(set, {k: v.copy() for k, v in state.parent_to_children.items()})
            self._child_to_parent = state.children_to_parent.copy()
            self._root_tasks = state.root_tasks.copy()

    async def clear_state(self) -> None:
        """Clear all task manager state
        
        Clears all tasks and internal index structures.
        Used when no saved state exists or state restoration fails.
        """
        async with self._lock:
            self.tasks.clear()
            self._priority_index.clear()
            self._parent_to_children.clear()
            self._child_to_parent.clear()
            self._root_tasks.clear()

    # ==================== Task CRUD Operations ====================
    async def add_task(self, task: Union[Task, List[Task]]):
        """Add task(s) to task queue

        Args:
            task: Single task or list of tasks
        """
        tasks = [task] if isinstance(task, Task) else task
        async with self._lock:
            for t in tasks:
                # Add task to dictionary
                if t.task_id in self.tasks:
                    raise build_error(
                        StatusCode.AGENT_CONTROLLER_TASK_PARAM_ERROR,
                        error_msg=f"{t.task_id} already exists!"
                    )
                self.tasks[t.task_id] = t.model_copy()

                # Update priority index
                self._priority_index[t.priority].append(t.task_id)

                # Update hierarchical relationship index
                if t.parent_task_id:
                    self._parent_to_children[t.parent_task_id].add(t.task_id)
                    self._child_to_parent[t.task_id] = t.parent_task_id
                    # If this task was previously a root task, remove it from root task set
                    if t.task_id in self._root_tasks:
                        self._root_tasks.remove(t.task_id)
                else:
                    # No parent task, add to root task set
                    self._root_tasks.add(t.task_id)

        # Notify outside lock
        self._notify_if_submitted(tasks)

    async def get_task(
            self,
            task_filter: Optional[TaskFilter] = None,
    ) -> List["Task"]:
        """Query tasks

        Query tasks based on multiple conditions, supporting various query methods.

        Args:
            task_filter: TaskFilter object containing filter criteria. If None, returns all tasks.

        Returns:
            List[Task]: List of matching tasks
        """
        async with self._lock:
            # Handle None case - return all tasks
            if task_filter is None:
                return list(self.tasks.values())

            result_tasks = []
            candidate_ids = set()

            # Query by task_id
            if task_filter.task_id is not None:
                task_ids = [task_filter.task_id] if isinstance(task_filter.task_id, str) else task_filter.task_id
                candidate_ids.update(task_ids)

            # Query by session_id
            if task_filter.session_id is not None:
                for t in self.tasks.values():
                    if t.session_id == task_filter.session_id:
                        candidate_ids.add(t.task_id)

            # Query by priority
            if task_filter.priority is not None:
                # "highest" is not supported in get_task (only in pop_task)
                if task_filter.priority == "highest":
                    raise build_error(
                        StatusCode.AGENT_CONTROLLER_TASK_PARAM_ERROR,
                        error_msg="Priority 'highest' is not supported in get_task, use pop_task instead"
                    )

                if isinstance(task_filter.priority, int) and task_filter.priority in self._priority_index:
                    candidate_ids.update(self._priority_index[task_filter.priority])

            # Query by is_root
            if task_filter.is_root:
                candidate_ids.update(self._root_tasks)

            # If no primary query conditions are specified (task_id, session_id, priority, is_root),
            # but status or user_id filters are provided, we need to check all tasks
            has_primary_filter = (task_filter.task_id is not None or 
                                 task_filter.session_id is not None or 
                                 task_filter.priority is not None or 
                                 task_filter.is_root)
            
            # If we only have status or user_id filters (no primary filters), check all tasks
            if not has_primary_filter and (task_filter.status is not None or task_filter.user_id is not None):
                candidate_ids = set(self.tasks.keys())

            # Filter tasks
            for tid in candidate_ids:
                if tid not in self.tasks:
                    continue
                task = self.tasks[tid]

                # Apply other filter conditions
                if task_filter.status is not None and task.status != task_filter.status:
                    continue
                if task_filter.user_id is not None:
                    # user_id is not in Task model, may be stored in metadata
                    if task.metadata and task.metadata.get("user_id") != task_filter.user_id:
                        continue
                    elif task.metadata is None or "user_id" not in task.metadata:
                        continue

                result_tasks.append(task)

            # Handle with_children and is_recursive
            if task_filter.with_children:
                children_ids = set()
                for task in result_tasks:
                    if task.task_id in self._parent_to_children:
                        self._collect_all_children(task.task_id, children_ids)

                # Add child tasks
                for cid in children_ids:
                    if cid in self.tasks:
                        result_tasks.append(self.tasks[cid])

            # Return deep copies to prevent external modifications to internal state
            return [task.model_copy(deep=True) for task in result_tasks]

    def _collect_all_children(self, parent_id: str, children_set: Set[str]):
        """Recursively collect all child task IDs

        Args:
            parent_id: Parent task ID
            children_set: Set for storing child task IDs
        """
        if parent_id in self._parent_to_children:
            for child_id in self._parent_to_children[parent_id]:
                children_set.add(child_id)
                self._collect_all_children(child_id, children_set)

    async def pop_task(
            self,
            task_filter: Optional[TaskFilter],
    ) -> List["Task"]:
        """Pop tasks (query and remove)

        Query tasks and remove them from the task manager. Parameters are the same as get_task.

        Args:
            task_filter: TaskFilter object containing filter criteria. Cannot be None.

        Returns:
            List[Task]: List of matching tasks (already removed from manager)
        """
        if task_filter is None:
            raise build_error(
                StatusCode.AGENT_CONTROLLER_TASK_PARAM_ERROR,
                error_msg="task_filter cannot be None in pop_task"
            )
        
        async with self._lock:
            # Handle the highest priority
            if task_filter.priority is not None and task_filter.priority == "highest":
                # Find the highest priority (priority with the largest number)
                if self._priority_index:
                    task_filter.priority = max(self._priority_index.keys())
                else:
                    return []

            # Get matching tasks (inline get_task logic since we're already in lock)
            result_tasks = []
            candidate_ids = set()

            # Query by task_id
            if task_filter.task_id is not None:
                task_ids = [task_filter.task_id] if isinstance(task_filter.task_id, str) else task_filter.task_id
                candidate_ids.update(task_ids)

            # Query by session_id
            if task_filter.session_id is not None:
                for t in self.tasks.values():
                    if t.session_id == task_filter.session_id:
                        candidate_ids.add(t.task_id)

            # Query by priority
            if task_filter.priority is not None:
                if isinstance(task_filter.priority, int) and task_filter.priority in self._priority_index:
                    candidate_ids.update(self._priority_index[task_filter.priority])

            # Query by is_root
            if task_filter.is_root:
                candidate_ids.update(self._root_tasks)

            # If no primary query conditions are specified (task_id, session_id, priority, is_root),
            # but status or user_id filters are provided, we need to check all tasks
            has_primary_filter = (task_filter.task_id is not None or 
                                 task_filter.session_id is not None or 
                                 task_filter.priority is not None or 
                                 task_filter.is_root)
            
            # If we only have status or user_id filters (no primary filters), check all tasks
            if not has_primary_filter and (task_filter.status is not None or task_filter.user_id is not None):
                candidate_ids = set(self.tasks.keys())

            # Filter tasks
            for tid in candidate_ids:
                if tid not in self.tasks:
                    continue
                task = self.tasks[tid]

                # Apply other filter conditions
                if task_filter.status is not None and task.status != task_filter.status:
                    continue
                if task_filter.user_id is not None:
                    # user_id is not in Task model, may be stored in metadata
                    if task.metadata and task.metadata.get("user_id") != task_filter.user_id:
                        continue
                    elif task.metadata is None or "user_id" not in task.metadata:
                        continue

                result_tasks.append(task)

            # Handle with_children and is_recursive
            if task_filter.with_children:
                children_ids = set()
                for task in result_tasks:
                    if task.task_id in self._parent_to_children:
                        self._collect_all_children(task.task_id, children_ids)

                # Add child tasks
                for cid in children_ids:
                    if cid in self.tasks:
                        result_tasks.append(self.tasks[cid])

            tasks = result_tasks

            # Collect all task IDs to remove
            task_ids_to_remove_set = {task.task_id for task in tasks}
            
            # Create deep copies before removing from internal state
            tasks_to_return = [task.model_copy(deep=True) for task in tasks]

            # Remove tasks from manager
            for tid in task_ids_to_remove_set:
                if tid not in self.tasks:
                    continue

                task = self.tasks[tid]

                # Remove from priority index
                if task.priority in self._priority_index:
                    try:
                        self._priority_index[task.priority].remove(tid)
                    except ValueError:
                        pass

                # Remove from hierarchical relationship index
                if task.parent_task_id:
                    # Remove from parent task's child task set
                    if task.parent_task_id in self._parent_to_children:
                        self._parent_to_children[task.parent_task_id].discard(tid)
                    # Remove from child-to-parent relationship index
                    if tid in self._child_to_parent:
                        del self._child_to_parent[tid]
                else:
                    # Remove from root task set
                    if tid in self._root_tasks:
                        self._root_tasks.remove(tid)

                # Handle child tasks: promote child tasks to root tasks (if their parent task is deleted)
                # Only promote children that are NOT being removed
                if tid in self._parent_to_children:
                    children = self._parent_to_children[tid].copy()
                    for child_id in children:
                        # Only promote if child is not being removed
                        if child_id not in task_ids_to_remove_set:
                            # Set child task's parent_task_id to None
                            if child_id in self.tasks:
                                child_task = self.tasks[child_id]
                                child_task.parent_task_id = None
                                # Add to root task set
                                self._root_tasks.add(child_id)
                                # Remove from child-to-parent relationship index
                                if child_id in self._child_to_parent:
                                    del self._child_to_parent[child_id]
                    # Delete parent-to-children mapping
                    del self._parent_to_children[tid]

                # Remove from task dictionary
                del self.tasks[tid]

            return tasks_to_return

    async def update_task(self, task: Union[Task, List[Task]]) -> bool:
        """Update task(s)

        Update task information. If the task does not exist, it will not create a new task.

        Args:
            task: Task(s) to update, can be a single task or a list of tasks

        Returns:
            bool: Whether the update was successful
        """
        tasks = [task] if isinstance(task, Task) else task
        async with self._lock:
            all_success = True

            for t in tasks:
                if t.task_id not in self.tasks:
                    all_success = False
                    continue

                old_task = self.tasks[t.task_id]

                # Update task
                self.tasks[t.task_id] = t

                # Update priority index
                # Always remove from old priority index first
                if old_task.priority in self._priority_index:
                    try:
                        self._priority_index[old_task.priority].remove(t.task_id)
                    except ValueError:
                        pass
                # Always add to new priority index
                if t.task_id not in self._priority_index.get(t.priority, []):
                    self._priority_index[t.priority].append(t.task_id)

                # If parent_task_id changed, update hierarchical relationship index
                if old_task.parent_task_id != t.parent_task_id:
                    # Remove from old parent task relationship
                    if old_task.parent_task_id:
                        if old_task.parent_task_id in self._parent_to_children:
                            self._parent_to_children[old_task.parent_task_id].discard(t.task_id)
                        if t.task_id in self._child_to_parent:
                            del self._child_to_parent[t.task_id]
                        # If old parent task has no children left, no special handling needed

                    # Update new parent task relationship
                    if t.parent_task_id:
                        self._parent_to_children[t.parent_task_id].add(t.task_id)
                        self._child_to_parent[t.task_id] = t.parent_task_id
                        # Remove from root task set
                        if t.task_id in self._root_tasks:
                            self._root_tasks.remove(t.task_id)
                    else:
                        # No parent task, add to root task set
                        self._root_tasks.add(t.task_id)
                        # Remove from child-to-parent relationship index
                        if t.task_id in self._child_to_parent:
                            del self._child_to_parent[t.task_id]

            return all_success

    async def remove_task(
            self,
            task_filter: Optional[TaskFilter],
    ):
        """Remove task(s)

        Remove tasks based on conditions, supports removing child tasks.

        Args:
            task_filter: TaskFilter object containing filter criteria. Cannot be None.

        Raises:
            BaseError: If task_filter is None or no filter criteria are provided
        """
        if task_filter is None:
            raise build_error(
                StatusCode.AGENT_CONTROLLER_TASK_PARAM_ERROR,
                error_msg="task_filter cannot be None in remove_task"
            )

        async with self._lock:
            # Get tasks to remove
            # Convert priority: "highest" is not supported in remove_task,
            # string representations should be converted to int
            priority_value = task_filter.priority
            if isinstance(task_filter.priority, str):
                if task_filter.priority == "highest":
                    raise build_error(
                        StatusCode.AGENT_CONTROLLER_TASK_PARAM_ERROR,
                        error_msg="Priority 'highest' is not supported in remove_task"
                    )
                priority_value = int(task_filter.priority)
            
            filter_for_get = TaskFilter(
                task_id=task_filter.task_id,
                session_id=task_filter.session_id,
                user_id=task_filter.user_id,
                priority=priority_value,
                status=task_filter.status,
                with_children=task_filter.with_children,
                is_root=task_filter.is_root
            )
            
            # Inline get_task logic since we're already in lock
            if filter_for_get is None:
                tasks_to_remove = list(self.tasks.values())
            else:
                result_tasks = []
                candidate_ids = set()

                # Query by task_id
                if filter_for_get.task_id is not None:
                    if isinstance(filter_for_get.task_id, str):
                        task_ids = [filter_for_get.task_id]
                    else:
                        task_ids = filter_for_get.task_id
                    candidate_ids.update(task_ids)

                # Query by session_id
                if filter_for_get.session_id is not None:
                    for t in self.tasks.values():
                        if t.session_id == filter_for_get.session_id:
                            candidate_ids.add(t.task_id)

                # Query by priority
                if filter_for_get.priority is not None:
                    if isinstance(filter_for_get.priority, int) and filter_for_get.priority in self._priority_index:
                        candidate_ids.update(self._priority_index[filter_for_get.priority])

                # Query by is_root
                if filter_for_get.is_root:
                    candidate_ids.update(self._root_tasks)

                # If no primary query conditions are specified (task_id, session_id, priority, is_root),
                # but status or user_id filters are provided, we need to check all tasks
                has_primary_filter = (filter_for_get.task_id is not None or 
                                     filter_for_get.session_id is not None or 
                                     filter_for_get.priority is not None or 
                                     filter_for_get.is_root)
                
                # If we only have status or user_id filters (no primary filters), check all tasks
                if not has_primary_filter and (filter_for_get.status is not None or filter_for_get.user_id is not None):
                    candidate_ids = set(self.tasks.keys())

                # Filter tasks
                for tid in candidate_ids:
                    if tid not in self.tasks:
                        continue
                    task = self.tasks[tid]

                    # Apply other filter conditions
                    if filter_for_get.status is not None and task.status != filter_for_get.status:
                        continue
                    if filter_for_get.user_id is not None:
                        # user_id is not in Task model, may be stored in metadata
                        if task.metadata and task.metadata.get("user_id") != filter_for_get.user_id:
                            continue
                        elif task.metadata is None or "user_id" not in task.metadata:
                            continue

                    result_tasks.append(task)

                # Handle with_children and is_recursive
                if filter_for_get.with_children:
                    children_ids = set()
                    for task in result_tasks:
                        if task.task_id in self._parent_to_children:
                            self._collect_all_children(task.task_id, children_ids)

                    # Add child tasks
                    for cid in children_ids:
                        if cid in self.tasks:
                            result_tasks.append(self.tasks[cid])

                tasks_to_remove = result_tasks

            # Collect all task IDs to remove (including child tasks)
            # Note: get_task with with_children=True already returns all children recursively,
            # so we just need to collect all task IDs from the result
            task_ids_to_remove = {task.task_id for task in tasks_to_remove}

            # Remove tasks
            for tid in task_ids_to_remove:
                if tid not in self.tasks:
                    continue

                task = self.tasks[tid]

                # Remove from priority index
                if task.priority in self._priority_index:
                    try:
                        self._priority_index[task.priority].remove(tid)
                    except ValueError:
                        pass
                    # If the list for this priority is empty, it can be kept (defaultdict will handle it automatically)

                # Remove from hierarchical relationship index
                if task.parent_task_id:
                    # Remove from parent task's child task set
                    if task.parent_task_id in self._parent_to_children:
                        self._parent_to_children[task.parent_task_id].discard(tid)
                    # Remove from child-to-parent relationship index
                    if tid in self._child_to_parent:
                        del self._child_to_parent[tid]
                else:
                    # Remove from root task set
                    if tid in self._root_tasks:
                        self._root_tasks.remove(tid)

                # Handle child tasks: promote child tasks to root tasks (if their parent task is deleted)
                # Only promote children that are NOT being removed
                if tid in self._parent_to_children:
                    children = self._parent_to_children[tid].copy()
                    for child_id in children:
                        # Only promote if child is not being removed
                        if child_id not in task_ids_to_remove:
                            # Set child task's parent_task_id to None
                            if child_id in self.tasks:
                                child_task = self.tasks[child_id]
                                child_task.parent_task_id = None
                                # Add to root task set
                                self._root_tasks.add(child_id)
                                # Remove from child-to-parent relationship index
                                if child_id in self._child_to_parent:
                                    del self._child_to_parent[child_id]
                    # Delete parent-to-children mapping
                    del self._parent_to_children[tid]

                # Remove from task dictionary
                del self.tasks[tid]

    async def get_child_task(
            self,
            task_id: Union[str, List[str]],
            is_recursive: bool = False,
    ):
        """Get child tasks

        Get all child tasks of the specified task(s).

        Args:
            task_id: Task ID, can be a single ID or a list of IDs
            is_recursive: Whether to recursively get all child tasks (including children of children)

        Returns:
            List[Task]: List of child tasks
        """
        async with self._lock:
            task_ids = [task_id] if isinstance(task_id, str) else task_id
            children_ids = set()

            for tid in task_ids:
                if tid in self._parent_to_children:
                    if is_recursive:
                        self._collect_all_children(tid, children_ids)
                    else:
                        children_ids.update(self._parent_to_children[tid])

            # Return deep copies of child task objects to prevent external modifications
            return [self.tasks[cid].model_copy(deep=True) for cid in children_ids if cid in self.tasks]

    # ==================== Task Execution Status Management ====================
    async def update_task_status(
            self,
            task_id: Union[str, List[str]],
            new_status: "TaskStatus",
            with_children: bool = False,
            is_recursive: bool = False,
            error_message: Optional[str] = None,
    ):
        """Update task status

        Update task status and synchronously update priority index.

        Args:
            task_id: Task ID, can be a single ID or a list of IDs
            new_status: New status
            with_children: Whether to also update child task status
            is_recursive: Whether to recursively update child task status
            error_message: Error message to update
        """
        async with self._lock:
            task_ids = [task_id] if isinstance(task_id, str) else task_id
            all_task_ids = set(task_ids)

            # If need to update child tasks, collect all child task IDs
            if with_children:
                for tid in task_ids:
                    if is_recursive:
                        self._collect_all_children(tid, all_task_ids)
                    else:
                        if tid in self._parent_to_children:
                            all_task_ids.update(self._parent_to_children[tid])

            # Update status for all tasks
            for tid in all_task_ids:
                if tid in self.tasks:
                    self.tasks[tid].status = new_status
                    # Set error_message when status is FAILED
                    if new_status == TaskStatus.FAILED:
                        self.tasks[tid].error_message = error_message or "Task execution failed"

        # Notify outside lock
        if new_status == TaskStatus.SUBMITTED:
            if self._on_task_submitted is not None:
                self._on_task_submitted()

    # ==================== Task Priority Management ====================
    async def set_priority(
            self,
            task_id: Union[str, List[str]],
            new_priority: Union[int, str],
            with_children: bool = False,
            is_recursive: bool = False,
    ):
        """Set task priority

        Update task priority and synchronously update priority index.

        Args:
            task_id: Task ID, can be a single ID or a list of IDs
            new_priority: New priority (integer or string representation of integer)
            with_children: Whether to also update child task priority
            is_recursive: Whether to recursively update child task priority
        """
        # Convert priority to integer
        if isinstance(new_priority, str):
            new_priority = int(new_priority)

        async with self._lock:
            task_ids = [task_id] if isinstance(task_id, str) else task_id
            all_task_ids = set(task_ids)

            # If need to update child tasks, collect all child task IDs
            if with_children:
                for tid in task_ids:
                    if is_recursive:
                        self._collect_all_children(tid, all_task_ids)
                    else:
                        if tid in self._parent_to_children:
                            all_task_ids.update(self._parent_to_children[tid])

            # Update priority for all tasks
            for tid in all_task_ids:
                if tid in self.tasks:
                    task = self.tasks[tid]
                    old_priority = task.priority

                    # Update task priority
                    task.priority = new_priority

                    # Update priority index
                    if old_priority != new_priority:
                        # Remove from old priority index
                        if old_priority in self._priority_index:
                            try:
                                self._priority_index[old_priority].remove(tid)
                            except ValueError:
                                pass
                        # Add to new priority index
                        self._priority_index[new_priority].append(tid)
