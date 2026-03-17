# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Task schema definitions for DeepAgent task loop.

Provides Pydantic-based TaskPlan / TaskItem models that
replace the earlier ``TaskPlan(dict)`` placeholder.
"""
from __future__ import annotations

import uuid
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class TaskStatus(str, Enum):
    """Lifecycle status of a single task item."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


class TaskItem(BaseModel):
    """A single actionable task inside a TaskPlan.

    Attributes:
        id: Unique task identifier.
        title: Short imperative title.
        description: Detailed description.
        status: Current lifecycle status.
        depends_on: IDs of tasks that must finish
            before this one can start.
        result_summary: Brief summary filled after
            completion or failure.
    """

    id: str = Field(
        default_factory=lambda: str(uuid.uuid4())[:8]
    )
    title: str = ""
    description: str = ""
    status: TaskStatus = TaskStatus.PENDING
    depends_on: List[str] = Field(default_factory=list)
    result_summary: Optional[str] = None


class TaskPlan(BaseModel):
    """Structured task plan for the outer task loop.

    Attributes:
        goal: High-level objective.
        tasks: Ordered list of task items.
        current_task_id: ID of the task currently
            being executed.
    """

    goal: str = ""
    tasks: List[TaskItem] = Field(default_factory=list)
    current_task_id: Optional[str] = None

    # -- query helpers --

    def get_task(self, task_id: str) -> Optional[TaskItem]:
        """Return task by ID or None."""
        for t in self.tasks:
            if t.id == task_id:
                return t
        return None

    def get_next_task(self) -> Optional[TaskItem]:
        """Return the first PENDING task whose deps are met."""
        done_ids: set[str] = set()
        for t in self.tasks:
            if t.status in (
                TaskStatus.COMPLETED, TaskStatus.FAILED
            ):
                done_ids.add(t.id)
        for t in self.tasks:
            if t.status != TaskStatus.PENDING:
                continue
            if all(d in done_ids for d in t.depends_on):
                return t
        return None

    def add_task(self, task: TaskItem) -> None:
        """Append a task item."""
        self.tasks.append(task)

    # -- mutation helpers --

    def mark_in_progress(self, task_id: str) -> None:
        """Set task status to IN_PROGRESS."""
        task = self.get_task(task_id)
        if task is not None:
            task.status = TaskStatus.IN_PROGRESS
            self.current_task_id = task_id

    def mark_completed(
        self,
        task_id: str,
        summary: str = "",
    ) -> None:
        """Set task status to COMPLETED."""
        task = self.get_task(task_id)
        if task is not None:
            task.status = TaskStatus.COMPLETED
            task.result_summary = summary
            if self.current_task_id == task_id:
                self.current_task_id = None

    def mark_failed(
        self,
        task_id: str,
        reason: str = "",
    ) -> None:
        """Set task status to FAILED."""
        task = self.get_task(task_id)
        if task is not None:
            task.status = TaskStatus.FAILED
            task.result_summary = reason
            if self.current_task_id == task_id:
                self.current_task_id = None

    # -- presentation --

    def get_progress_summary(self) -> str:
        """Return e.g. '3/7 completed'."""
        total = len(self.tasks)
        done = sum(
            1
            for t in self.tasks
            if t.status == TaskStatus.COMPLETED
        )
        return f"{done}/{total} completed"

    def to_markdown(self) -> str:
        """Render plan as a markdown checklist."""
        lines = [f"## Goal: {self.goal}", ""]
        for t in self.tasks:
            if t.status == TaskStatus.COMPLETED:
                mark = "x"
            elif t.status == TaskStatus.IN_PROGRESS:
                mark = "~"
            elif t.status == TaskStatus.FAILED:
                mark = "!"
            else:
                mark = " "
            suffix = ""
            if t.result_summary:
                suffix = f" — {t.result_summary}"
            lines.append(
                f"- [{mark}] {t.title}{suffix}"
            )
        return "\n".join(lines)

    # -- serialization --

    def to_dict(self) -> Dict[str, Any]:
        """JSON-friendly dict for session persistence."""
        return self.model_dump(mode="python")

    @classmethod
    def from_dict(
        cls, data: Optional[Dict[str, Any]]
    ) -> "TaskPlan":
        """Build from persisted dict."""
        if not data:
            return cls()
        return cls.model_validate(data)


__all__ = [
    "TaskItem",
    "TaskPlan",
    "TaskStatus",
]
