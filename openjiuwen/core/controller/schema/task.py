# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
Task Data Model Definitions

This module defines task-related data models, including:
- TaskStatus: Task status enumeration
- Task: Task data model

Task Status Flow:
submitted -> working -> (completed | failed | paused | canceled)
                |
                -> input-required -> (continue execution or cancel)
"""

from __future__ import annotations

from enum import Enum
from typing import Optional, List, Dict, Any, Union, TYPE_CHECKING
from pydantic import BaseModel, Field, field_validator, model_validator

from openjiuwen.core.controller.schema.controller_output import ControllerOutputChunk

if TYPE_CHECKING:
    from openjiuwen.core.controller.schema.event import Event


class TaskStatus(str, Enum):
    """Task Status Enumeration

    Defines all possible states of a task:
    - SUBMITTED: Submitted, waiting for execution
    - WORKING: Currently executing
    - PAUSED: Paused
    - INPUT_REQUIRED: Requires user input
    - COMPLETED: Completed
    - CANCELED: Canceled
    - FAILED: Execution failed
    - WAITING: Waiting (maybe waiting for dependent tasks to complete)
    - UNKNOWN: Unknown status
    """
    SUBMITTED = "submitted"
    WORKING = "working"
    PAUSED = "paused"
    INPUT_REQUIRED = "input-required"
    COMPLETED = "completed"
    CANCELED = "canceled"
    FAILED = "failed"
    WAITING = "waiting"
    UNKNOWN = "unknown"


class Task(BaseModel):
    """Task Model

    Defines the structure of a task, including task basic information, status,
    input/output, and hierarchical relationships.

    Attributes:
        session_id: Session ID, identifies the session to which the task belongs
        task_id: Task ID, uniquely identifies a task
        task_type: Task type, used to find the corresponding TaskExecutor
        description: Task description
        priority: Task priority, smaller numbers indicate higher priority, default is 1
        inputs: List of all input events related to this task
        outputs: List of output chunks during task execution
        status: Task status
        parent_task_id: Parent task ID, used to build task hierarchical relationships
        context_id: Context ID, used to associate context information with the task
        input_required_fields: Field definitions for required user input (used when status is INPUT_REQUIRED)
        error_message: Error message (used when status is FAILED)
        metadata: Task metadata, can store additional task information
    """
    session_id: str
    task_id: str
    task_type: str
    description: Optional[str] = None
    priority: int = 1
    inputs: Optional[List[Event]] = None
    outputs: List[ControllerOutputChunk] = Field(default_factory=list)
    status: TaskStatus = TaskStatus.UNKNOWN
    parent_task_id: Optional[str] = None
    context_id: Optional[str] = None
    input_required_fields: Optional[Union[Dict[str, Any], BaseModel]] = Field(default=None)
    error_message: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    extensions: Optional[Dict[str, Any]] = None

    @field_validator('task_id', 'session_id', 'task_type')
    @classmethod
    def validate_required_strings(cls, v: str) -> str:
        """Validate that required string fields are not empty

        Args:
            v: The field value to validate

        Returns:
            str: The validated value

        Raises:
            ValueError: If the value is empty or None
        """
        if not v or not v.strip():
            raise ValueError(f"{cls.__name__} field cannot be empty")
        return v.strip()

    @field_validator('priority')
    @classmethod
    def validate_priority(cls, v: int) -> int:
        """Validate task priority

        Args:
            v: Priority value

        Returns:
            int: The validated priority value

        Raises:
            ValueError: If priority is negative
        """
        if v < 0:
            raise ValueError("Priority must be a non-negative integer")
        return v

    @field_validator('parent_task_id')
    @classmethod
    def validate_parent_task_id(cls, v: Optional[str]) -> Optional[str]:
        """Validate parent task ID

        Args:
            v: Parent task ID value

        Returns:
            Optional[str]: The validated parent task ID

        Raises:
            ValueError: If parent_task_id is an empty string
        """
        if v is not None and (not v or not v.strip()):
            raise ValueError("parent_task_id cannot be an empty string if provided")
        return v.strip() if v else None

    @model_validator(mode='after')
    def validate_task_consistency(self):
        """Validate task consistency and status-specific requirements

        Validates:
        - task_id should not equal parent_task_id (no self-reference)
        - FAILED status requires error_message
        - INPUT_REQUIRED status requires input_required_fields

        Returns:
            Task: The validated task instance

        Raises:
            ValueError: If validation fails
        """
        # Check for circular reference
        if self.parent_task_id and self.task_id == self.parent_task_id:
            raise ValueError("task_id cannot be the same as parent_task_id (circular reference)")

        # Validate status-specific fields
        if self.status == TaskStatus.FAILED:
            if not self.error_message or not self.error_message.strip():
                raise ValueError("error_message is required when status is FAILED")

        if self.status == TaskStatus.INPUT_REQUIRED:
            if self.input_required_fields is None:
                raise ValueError("input_required_fields is required when status is INPUT_REQUIRED")

        return self
