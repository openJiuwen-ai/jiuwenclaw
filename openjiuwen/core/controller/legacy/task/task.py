# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional, Dict, Any, Set, List
from pydantic import BaseModel, Field
from openjiuwen.core.common.constants.enums import TaskType


class TaskStatus(Enum):
    PENDING = auto()
    RUNNING = auto()
    SUCCESS = auto()
    FAILED = auto()
    CANCELLED = auto()
    INTERRUPTED = auto()  # Task interrupted, waiting for user input


class DependencyType(Enum):
    """Dependency type"""
    SEQUENTIAL = "sequential"  # Sequential (exec after dependency completes)
    PARALLEL = "parallel"  # Parallel (can run concurrently, wait for deps)
    CONDITIONAL = "conditional"  # Conditional (execute based on condition)
    DATA = "data"  # Data dependency (needs dependency output)


@dataclass
class TaskDependency:
    """Task dependency"""
    dependency_id: str  # Dependent task ID
    dependency_type: DependencyType = DependencyType.SEQUENTIAL
    condition: Optional[str] = Field(default=None)  # Condition expression
    data_mapping: Dict[str, str] = field(default_factory=dict)  # Data mapping: {source: target}
    required: bool = True  # Whether required

    def __post_init__(self):
        if self.data_mapping is None:
            self.data_mapping = {}


class TaskInput(BaseModel):
    """Task input - unified handling for tools, workflows, MCP calls"""
    target_id: str = Field(default="")
    target_name: str = Field(default="")
    arguments: Any = Field(default_factory=dict)


class TaskResult(BaseModel):
    """Task result - minimal design, no duplicate fields"""
    status: TaskStatus
    output: Any = Field(default=None)  # Output on success (WorkflowOutput etc)
    error: Optional[str] = Field(default=None)  # Error message on failure
    metadata: Dict[str, Any] = Field(default_factory=dict)  # Extended info (execution_time etc)


class Task(BaseModel):
    """Unified task class - supports dependencies"""
    agent_id: Optional[str] = Field(default=None)
    task_id: str = Field(default="")
    task_type: TaskType = Field(default=TaskType.UNDEFINED)

    description: Optional[str] = Field(default=None)
    status: TaskStatus = Field(default=TaskStatus.PENDING)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    input: TaskInput = Field(default_factory=TaskInput)
    result: Optional[TaskResult] = Field(default=None)  # Explicit type, not Any

    # Dependency management
    dependencies: List[TaskDependency] = Field(default_factory=list)  # Tasks this depends on
    dependents: Set[str] = Field(default_factory=set)  # Task IDs depending on this

    # DAG attributes
    parent_task_id: Optional[str] = Field(default=None)  # Parent task ID (for subtasks)
    child_task_ids: Set[str] = Field(default_factory=set)  # Child task ID set
    group_id: Optional[str] = Field(default=None)  # Task group ID
    level: int = Field(default=0)  # Level in dependency graph (0 is root)

    def set_agent_id(self, agent_id: str) -> None:
        """Set Agent ID"""
        self.agent_id = agent_id
