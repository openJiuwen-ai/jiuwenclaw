# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""
TaskQueue
---------

Lightweight asynchronous queue managing rollout tasks,
tracking in-processing tasks, and buffering completed rollout messages.
"""

import asyncio
from typing import Dict, Optional

from openjiuwen.core.common.logging import logger
from openjiuwen.dev_tools.agentrl.coordinator.schemas import RLTask, RolloutMessage


class TaskQueue:
    """
    Async task queue + rollout result buffer for the RL training daemon.
    """

    def __init__(self) -> None:
        """Initialize an empty task queue and rollout buffer."""
        self._task_queue: asyncio.Queue[RLTask] = asyncio.Queue()
        self._in_processing_task: Dict[str, RLTask] = {}
        self._rollouts: Dict[str, RolloutMessage] = {}
        self._queue_lock = asyncio.Lock()
        self._rollout_lock = asyncio.Lock()

    async def queue_task(self, task: RLTask) -> str:
        """Enqueue a new task and return its task identifier."""
        async with self._queue_lock:
            await self._task_queue.put(task)
            logger.debug("Task %s queued.", task.task_id)
            return task.task_id

    async def get_task(self) -> Optional[RLTask]:
        """Retrieve the next task for processing."""
        try:
            async with self._queue_lock:
                processing_task = self._task_queue.get_nowait()
                self._in_processing_task[processing_task.task_id] = processing_task
                return processing_task
        except asyncio.QueueEmpty:
            return None

    async def delete_task(self, task: RLTask):
        """Remove a task from the in-processing pool directly."""
        async with self._queue_lock:
            if task.task_id in self._in_processing_task:
                del self._in_processing_task[task.task_id]
                logger.debug("Task %s deleted directly.", task.task_id)

    async def add_rollout(self, rollout: RolloutMessage) -> str:
        """Store a completed rollout and clear its in-processing entry."""
        rollout_id = rollout.rollout_id
        task_id = rollout.task_id
        async with self._queue_lock:
            if task_id and task_id in self._in_processing_task:
                del self._in_processing_task[task_id]
                logger.debug("Task %s removed from in_processing_task", task_id)

        async with self._rollout_lock:
            self._rollouts[rollout_id] = rollout
            logger.debug("Rollout %s added to rollouts cache.", rollout_id)
            return rollout_id

    async def get_rollouts(self) -> Dict[str, RolloutMessage]:
        """Retrieve and clear all cached rollouts atomically."""
        async with self._rollout_lock:
            rollouts = self._rollouts.copy()
            self._rollouts.clear()
            logger.debug("Retrieved %d rollouts from cache", len(rollouts))
            return rollouts

    def is_finished(self) -> bool:
        """Check whether all tasks have been processed."""
        if self._task_queue.empty() and len(self._in_processing_task) == 0:
            logger.debug("All tasks finished.")
            return True
        return False

    def clear(self) -> None:
        """Fully reset the queue.

        Must be called between training steps to avoid stale state from
        previous steps leaking into the next step.
        """
        while not self._task_queue.empty():
            try:
                self._task_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        self._in_processing_task.clear()
        self._rollouts.clear()
        logger.info("TaskQueue fully cleared.")
