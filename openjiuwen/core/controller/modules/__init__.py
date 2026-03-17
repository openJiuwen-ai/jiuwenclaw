# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Internal controller modules.

This package contains the core building blocks used by the controller:

- EventQueue: event queue responsible for publishing and subscribing events.
- TaskManager: task manager that handles task CRUD and state management.
- TaskScheduler: task scheduler that orchestrates task execution.
- IntentRecognizer: intent recognizer that detects user intent.
- EventHandler: base class for event handlers.
- EventHandlerWithIntentRecognition: event handler with built‑in intent
  recognition.
"""
from openjiuwen.core.controller.modules.event_handler import EventHandlerInput, EventHandler
from openjiuwen.core.controller.modules.event_queue import EventQueue
from openjiuwen.core.controller.modules.task_manager import TaskManagerState, TaskManager, TaskFilter
from openjiuwen.core.controller.modules.task_scheduler import (
    TaskExecutor,
    TaskExecutorDependencies,
    TaskExecutorRegistry,
    TaskScheduler
)
from openjiuwen.core.controller.modules.intent_recognizer import IntentRecognizer, EventHandlerWithIntentRecognition


__all__ = [
    # Event queue and event handling
    "EventHandlerInput",
    "EventHandler",
    "EventQueue",
    # Task management
    "TaskManagerState",
    "TaskManager",
    "TaskFilter",
    # Task execution and scheduling
    "TaskExecutor",
    "TaskExecutorDependencies",
    "TaskExecutorRegistry",
    "TaskScheduler",
    "IntentRecognizer",
    "EventHandlerWithIntentRecognition",
]
