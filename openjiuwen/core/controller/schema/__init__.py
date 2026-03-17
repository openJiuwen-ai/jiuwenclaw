# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""
Controller Data Model Definitions

This module defines all controller-related data models, including:
- DataFrame: Data frames (text, files, JSON)
- Event: Events (input events, task execution interaction events, task completion events,
    task failure events) and event types
- ControllerOutput: Controller outputs (batch processing and streaming)
- Intent: Intents and intent types
- Task: Tasks and task execution status
"""


from openjiuwen.core.controller.schema.dataframe import TextDataFrame, FileDataFrame, JsonDataFrame, DataFrame
from openjiuwen.core.controller.schema.event import (
    EventType, Event, InputEvent, TaskInteractionEvent, TaskCompletionEvent, TaskFailedEvent
)
from openjiuwen.core.controller.schema.intent import IntentType, Intent
from openjiuwen.core.controller.schema.task import TaskStatus, Task
from openjiuwen.core.controller.schema.controller_output import (
    ControllerOutputPayload, ControllerOutputChunk, ControllerOutput
)


Task.model_rebuild()
TaskCompletionEvent.model_rebuild()
TaskInteractionEvent.model_rebuild()
TaskFailedEvent.model_rebuild()

__all__ = [
    # DataFrame
    "TextDataFrame",
    "FileDataFrame",
    "JsonDataFrame",
    "DataFrame",
    # Event (Controller Input)
    "EventType",
    "Event",
    "InputEvent",
    "TaskInteractionEvent",
    "TaskCompletionEvent",
    "TaskFailedEvent",
    # Controller Output
    "ControllerOutputPayload",
    "ControllerOutputChunk",
    "ControllerOutput",
    # Intent
    "IntentType",
    "Intent",
    # Task
    "TaskStatus",
    "Task",
]