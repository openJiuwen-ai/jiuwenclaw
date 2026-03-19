# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""
ParallelRuntimeExecutor
-----------------------

Parallel rollout execution engine that manages multiple async worker loops,
pulling tasks from a TaskQueue and writing results back.
"""

import asyncio
import multiprocessing as mp
import traceback
from typing import Callable, List, Optional

from openjiuwen.core.common.logging import logger
from openjiuwen.dev_tools.agentrl.agent_runtime.runtime_executor import RuntimeExecutor
from openjiuwen.dev_tools.agentrl.coordinator.schemas import RLTask
from openjiuwen.dev_tools.agentrl.coordinator.task_queue import TaskQueue


class ParallelRuntimeExecutor:
    """Parallel rollout engine pulling tasks from a TaskQueue.

    Each worker creates its own RuntimeExecutor and processes tasks
    concurrently until stopped.
    """

    def __init__(
        self,
        data_store: TaskQueue,
        num_workers: int,
        *,
        task_runner: Optional[Callable] = None,
        agent_factory: Optional[Callable] = None,
        task_data_fn: Optional[Callable] = None,
        reward_fn: Optional[Callable] = None,
    ) -> None:
        """Initialize the parallel executor with a task queue and worker count."""
        self.data_store = data_store
        self.num_workers = num_workers or mp.cpu_count()

        self._task_runner = task_runner
        self._agent_factory = agent_factory
        self._task_data_fn = task_data_fn
        self._reward_fn = reward_fn

        self._is_running = False
        self._runtime_tasks: List[asyncio.Task] = []

    async def start(self) -> None:
        """Launch all worker loops."""
        if self._is_running:
            logger.warning("ParallelRuntimeExecutor is already running")
            return
        self._is_running = True
        logger.info("Starting ParallelRuntimeExecutor with %d workers", self.num_workers)
        for i in range(self.num_workers):
            task = asyncio.create_task(self._worker_loop(worker_id=i))
            self._runtime_tasks.append(task)

    async def stop(self) -> None:
        """Stop all workers and clean up."""
        self._is_running = False
        if self._runtime_tasks:
            await asyncio.gather(*self._runtime_tasks, return_exceptions=True)
            self._runtime_tasks.clear()
        logger.info("ParallelRuntimeExecutor stopped")

    def is_running(self) -> bool:
        """Return whether the executor is currently running."""
        return self._is_running

    def set_task_runner(self, fn: Callable) -> None:
        """Set the task runner callable for executing rollout tasks."""
        self._task_runner = fn

    def set_agent_factory(self, factory: Callable) -> None:
        """Set the agent factory for creating agents per task."""
        self._agent_factory = factory

    def set_task_data_fn(self, fn: Callable) -> None:
        """Set the function to convert task samples to agent inputs."""
        self._task_data_fn = fn

    def set_reward_fn(self, fn: Callable) -> None:
        """Set the reward function to compute rewards from rollout messages."""
        self._reward_fn = fn

    async def _worker_loop(self, worker_id: int) -> None:
        """Worker loop: pull → execute → push results."""
        logger.debug("Worker %d started", worker_id)
        executor = RuntimeExecutor(
            task_runner=self._task_runner,
            agent_factory=self._agent_factory,
            task_data_fn=self._task_data_fn,
            reward_fn=self._reward_fn,
        )

        while self._is_running:
            task: Optional[RLTask] = None
            try:
                task = await self.data_store.get_task()
                if task is None:
                    await asyncio.sleep(0.1)
                    continue

                logger.debug("Worker %d START task %s", worker_id, task.task_id)
                rollout_message = await executor.execute_async(task)
                rollout_message.rollout_id = task.task_id
                await self.data_store.add_rollout(rollout_message)
                logger.debug(
                    "Worker %d DONE task %s, reward=%s",
                    worker_id, task.task_id, rollout_message.global_reward,
                )
            except Exception as e:
                traceback.print_exc()
                logger.error(
                    "Worker %d error: %s, deleting task directly.", worker_id, str(e)
                )
                if task is not None:
                    await self.data_store.delete_task(task)
                await asyncio.sleep(1)
