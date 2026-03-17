# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Controller module - Agent controllers

This module re-exports from legacy submodule for backward compatibility.
"""

from openjiuwen.core.controller.schema import (
    TextDataFrame,
    FileDataFrame,
    JsonDataFrame,
    DataFrame,
    EventType,
    Event,
    InputEvent,
    TaskInteractionEvent,
    TaskCompletionEvent,
    TaskFailedEvent,
    ControllerOutputPayload,
    ControllerOutputChunk,
    ControllerOutput,
    IntentType,
    Intent,
    TaskStatus,
    Task,
)
from openjiuwen.core.controller.modules import (
    EventHandlerInput,
    EventHandler,
    EventQueue,
    TaskManagerState,
    TaskManager,
    TaskFilter,
    TaskExecutor,
    TaskExecutorRegistry,
    TaskScheduler,
    IntentRecognizer,
    EventHandlerWithIntentRecognition
)
from openjiuwen.core.controller.config import ControllerConfig
from openjiuwen.core.controller.base import Controller

_CONTROLLER_CLASSES = [
    "BaseController",
]

_INTENT_CLASSES = [
    "IntentDetectionController",
    "IntentType",
    "Intent",
    "TaskQueue"
]

_TASK_CLASSES = [
    "Task",
    "TaskInput",
    "TaskStatus",
    "TaskResult"
]

_REASONER_CLASSES = [
    "IntentDetector",
    "Planner"
]

_EVENT_CLASSES = [
    "Event",
    "EventType",
    "EventPriority",
    "EventSource",
    "EventContent",
    "EventContext",
    "SourceType",
]

_CONFIG_CLASSES = [
    "IntentDetectionConfig",
    "PlannerConfig",
    "ProactiveIdentifierConfig",
    "ReflectorConfig",
    "ReasonerConfig",
]


_NEW_CLASS = [
    # ========================= DataStructure Definition =============================
    # Units
    "TextDataFrame",
    "FileDataFrame",
    "JsonDataFrame",
    "DataFrame",
    # Event (Controller Inputs)
    "EventType",
    "Event",
    "InputEvent",
    "TaskInteractionEvent",
    "TaskCompletionEvent",
    "TaskFailedEvent",
    # Controller outputs
    "ControllerOutputPayload",
    "ControllerOutputChunk",
    "ControllerOutput",
    # Intent
    "IntentType",
    "Intent",
    # Task
    "TaskStatus",
    "Task",
    # ========================= Controller Inner Modules =============================
    # Event Queue
    "EventHandlerInput",
    "EventHandler",
    "EventQueue",
    # Task Manager
    "TaskManagerState",
    "TaskManager",
    "TaskFilter",
    # Task Execution
    "TaskExecutor",
    "TaskExecutorRegistry",
    "TaskScheduler",
    # =========================    Controller   =============================
    "ControllerConfig",
    "Controller",
    "IntentRecognizer",
    "EventHandlerWithIntentRecognition"
]


__all__ = (
        _CONTROLLER_CLASSES +
        _INTENT_CLASSES +
        _TASK_CLASSES +
        _REASONER_CLASSES +
        _EVENT_CLASSES +
        _CONFIG_CLASSES +
        _NEW_CLASS
)
