# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
Controller configuration module.

This module defines configuration-related classes for the controller:
- ControllerConfig: controller configuration class.
"""

from typing import Optional, List, Literal
from pydantic import BaseModel, Field


class DefaultResponse(BaseModel):
    """Default response configuration for workflow agent."""

    type: Literal["text", "workflow"] = "text"
    text: str = None


class ControllerConfig(BaseModel):
    """Controller configuration.

    Defines configuration parameters for the controller and controls its behavior.
    The configuration items are grouped into several categories: task scheduling,
    task management, event queue, and intent recognition.

    Attributes:
        # ==================== Task scheduling configuration ====================
        max_concurrent_tasks: Maximum number of concurrent tasks, controlling the
                             upper limit of tasks executed at the same time.
                             Defaults to 5. A value of 0 means no limit.
        schedule_interval: Task scheduling interval in seconds. The scheduler
                           periodically scans pending tasks using this interval.
                           Defaults to 1.0 seconds. Smaller values improve
                           responsiveness but increase CPU usage.
        task_timeout: Task timeout in seconds. Tasks that exceed this duration are marked as failed.
                     Defaults to None, meaning no timeout.

        # ==================== Task management configuration ====================
        default_task_priority: Default task priority. Used when a task is created without an explicit priority.
                               Defaults to 1. Larger numbers indicate higher priority.
        enable_task_persistence: Whether to enable task persistence. When enabled, task states are stored for recovery.
                                 Defaults to False.

        # ==================== Event queue configuration ====================
        event_queue_size: Event queue size, limiting the number of events that can be stored in the queue.
                         Defaults to 10000. Not modifiable at runtime.
        event_timeout: Event processing timeout in seconds. Events that are not
                      processed within this time are discarded.
                      Default timeout is 120000.0 ms.

        # ==================== Intent recognition configuration ====================
        enable_intent_recognition: Whether to enable intent recognition. Defaults to True.
                                   When enabled, user intent is automatically recognized
                                   and routed to the corresponding handler.
        intent_confidence_threshold: Confidence threshold for intent recognition.
                                    Intents below this value are treated as UNKNOWN_TASK.
                                    Defaults to 0.7. Range 0.0–1.0.

    Example:
        ```python
        config = ControllerConfig(
            max_concurrent_tasks=10,
            schedule_interval=0.5,
            default_task_priority=5,
            enable_intent_recognition=True
        )
        ```
    """
    # ==================== Task scheduling configuration ====================
    max_concurrent_tasks: int = Field(
        default=5,
        description="Maximum number of concurrent tasks. "
                    "Controls the upper limit of tasks running at the same time. 0 means no limit."
    )
    schedule_interval: float = Field(
        default=1.0,
        ge=0.1,
        description="Task scheduling interval in seconds. "
                    "The scheduler periodically scans pending tasks using this interval."
    )
    task_timeout: Optional[float] = Field(
        default=None,
        ge=600,
        description="Task timeout in seconds. Tasks that exceed this duration are marked as failed. "
                    "None means no timeout."
    )

    # ==================== Task management configuration ====================
    default_task_priority: int = Field(
        default=1,
        description="Default task priority. Used when a task is created without an explicit priority. "
                    "Larger numbers mean higher priority."
    )

    enable_task_persistence: bool = Field(
        default=False,
        description="Whether to enable task persistence. When enabled, task states are stored for recovery. "
                    "Defaults to False."
    )

    # ==================== Event queue configuration ====================
    event_queue_size: Optional[int] = Field(
        default=10000,
        ge=1,
        description="Event queue size. Limits the number of events that can be stored in the queue. Default is 10000."
    )
    event_timeout: Optional[float] = Field(
        default=300,
        ge=100,
        description="Event processing timeout in seconds. "
                    "Events that are not processed within this time are discarded. "
                    "Default timeout is 300 s."
    )

    # ==================== Intent recognition configuration ====================
    enable_intent_recognition: bool = False
    intent_llm_id: str = Field(
        default="",
    )
    intent_confidence_threshold: float = Field(
        default=0.7,
        ge=0.0,
        le=1.0,
        description="Confidence threshold for intent recognition. "
                    "Intents below this value are treated as UNKNOWN_TASK. Range 0.0–1.0."
    )
    intent_type_list: List[str] = Field(
        default=[
            "create_task",
            "pause_task",
            "resume_task",
            "cancel_task",
            "unknown_task",
        ],
        description="List of intent types supported by this controller. "
                    "Supported types: "
                    "create_task, pause_task, resume_task, cancel_task, unknown_task"
                    "create_dependent_task, modify_task, supplement_task"
    )
    default_response: DefaultResponse = Field(default_factory=DefaultResponse)

    # ==================== Completion signal config ====================
    suppress_completion_signal: bool = Field(
        default=False,
        description=(
            "When True, TaskScheduler will not send "
            "'all_tasks_processed' signal to session "
            "stream. Used by multi-round callers that "
            "manage their own completion logic."
        ),
    )

    # ==================== Stream config ====================
    stream_first_frame_timeout: Optional[float] = Field(
        default=30.0,
        description=(
            "Max seconds to wait for the first chunk "
            "from session stream after fire-and-forget "
            "publish. None means no timeout."
        ),
    )
