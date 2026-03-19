# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""
RuntimeExecutor
---------------

Executes a single rollout task:
1. Runs the agent via TrajectoryCollector (RAIL mode) or a user-injected task_runner.
2. Optionally applies a reward function.
3. Returns a fully populated RolloutMessage.

Designed to be called repeatedly from ParallelRuntimeExecutor worker loops.
"""

import inspect
import traceback
import uuid
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import logger
from openjiuwen.dev_tools.agentrl.coordinator.schemas import Rollout, RolloutMessage, RLTask


TaskRunnerCallable = Callable[[RLTask], Awaitable[RolloutMessage]]
TaskDataFn = Callable[[Dict[str, Any]], Dict[str, Any]]


class RuntimeExecutor:
    """Self-contained single-task executor.

    Supports two execution modes:

    1. **task_runner mode**: caller injects a ``task_runner(rl_task) -> RolloutMessage``
       coroutine for full control over execution.
    2. **agent mode**: uses ``agent_factory`` + ``TrajectoryCollector`` (RAIL-based)
       to run the agent and collect structured trajectory data.
    """

    def __init__(
        self,
        *,
        task_runner: Optional[TaskRunnerCallable] = None,
        agent_factory: Optional[Callable[[RLTask], Any]] = None,
        task_data_fn: Optional[TaskDataFn] = None,
        reward_fn: Optional[Callable[[RolloutMessage], Any]] = None,
    ) -> None:
        """Initialize the runtime executor with optional task runner, agent factory, and helpers."""
        self._task_runner = task_runner
        self._agent_factory = agent_factory
        self._task_data_fn = task_data_fn
        self._reward_fn = reward_fn

    def set_task_runner(self, fn: TaskRunnerCallable) -> None:
        """Set the task runner callable for executing rollout tasks."""
        self._task_runner = fn

    def set_agent_factory(self, factory: Callable[[RLTask], Any]) -> None:
        """Set the agent factory for creating agents per task."""
        self._agent_factory = factory

    def set_task_data_fn(self, fn: TaskDataFn) -> None:
        """Set the function to convert task samples to agent inputs."""
        self._task_data_fn = fn

    def set_reward_fn(self, fn: Callable[[RolloutMessage], Any]) -> None:
        """Set the reward function to compute rewards from rollout messages."""
        self._reward_fn = fn

    async def execute_async(self, rollout_task: RLTask) -> RolloutMessage:
        """Execute a rollout task and return a populated RolloutMessage."""
        start_time = datetime.now(tz=timezone.utc).isoformat()

        rollout_message = RolloutMessage(
            rollout_id=str(uuid.uuid4()),
            task_id=rollout_task.task_id,
            origin_task_id=rollout_task.origin_task_id,
            start_time=start_time,
            rollout_info=[],
            reward_list=[],
            global_reward=0.0,
            turn_count=0,
            round_num=rollout_task.round_num,
        )

        try:
            if self._task_runner is not None:
                rollout_message = await self._task_runner(rollout_task)
                if rollout_message.task_id is None:
                    rollout_message.task_id = rollout_task.task_id
                if rollout_message.origin_task_id is None:
                    rollout_message.origin_task_id = rollout_task.origin_task_id
                rollout_message.turn_count = rollout_message.turn_count or len(
                    rollout_message.rollout_info
                )
            elif self._agent_factory is not None:
                rollout_message = await self._execute_with_agent(
                    rollout_task, rollout_message
                )
            else:
                raise build_error(
                    StatusCode.AGENT_RL_EXECUTOR_NOT_INITIALIZED,
                    error_msg="neither task_runner nor agent_factory is set",
                )
        except Exception as e:
            logger.error(
                "RuntimeExecutor error for task %s: %s\n%s",
                rollout_task.task_id,
                e,
                traceback.format_exc(),
            )
            return rollout_message

        if self._reward_fn is not None:
            try:
                self._apply_reward(rollout_message)
            except Exception as e:
                logger.error("Reward computation failed: %s", e)

        rollout_message.end_time = datetime.now(tz=timezone.utc).isoformat()
        return rollout_message

    async def _execute_with_agent(
        self, rl_task: RLTask, rollout_message: RolloutMessage
    ) -> RolloutMessage:
        """Run agent with TrajectoryCollector (RAIL mode) and build RolloutMessage."""
        from openjiuwen.dev_tools.agentrl.agent_runtime.trajectory import TrajectoryCollector

        if inspect.iscoroutinefunction(self._agent_factory):
            agent = await self._agent_factory(rl_task)
        else:
            agent = self._agent_factory(rl_task)

        inputs = self._build_agent_inputs(rl_task)
        collector = TrajectoryCollector()
        rollouts: List[Rollout] = await collector.collect(agent, inputs)

        now = datetime.utcnow().isoformat()
        msg = RolloutMessage(
            task_id=rl_task.task_id,
            origin_task_id=rl_task.origin_task_id,
            rollout_id=f"rollout-{rl_task.task_id}",
            start_time=now,
            end_time=now,
            rollout_info=rollouts,
            reward_list=[],
            global_reward=None,
            turn_count=len(rollouts),
            round_num=rl_task.round_num,
        )

        ground_truth = inputs.get("ground_truth", "")
        if ground_truth and msg.rollout_info:
            if msg.rollout_info[0].input_prompt is None:
                msg.rollout_info[0].input_prompt = {}
            msg.rollout_info[0].input_prompt["ground_truth"] = ground_truth

        return msg

    def _build_agent_inputs(self, rl_task: RLTask) -> Dict[str, Any]:
        """Build agent inputs from the task sample."""
        if self._task_data_fn is not None:
            inputs = self._task_data_fn(rl_task.task_sample)
            inputs.setdefault("conversation_id", rl_task.task_id)
            return inputs
        return {
            "query": rl_task.task_sample.get("query", ""),
            "ground_truth": rl_task.task_sample.get("ground_truth", ""),
            "conversation_id": rl_task.task_id,
        }

    def _apply_reward(self, msg: RolloutMessage) -> None:
        """Apply the reward function and fill reward_list / global_reward."""
        result = self._reward_fn(msg)

        if isinstance(result, list):
            msg.reward_list = [float(x) for x in result]
            msg.global_reward = float(
                sum(msg.reward_list) / max(len(msg.reward_list), 1)
            )
        elif isinstance(result, dict):
            if "reward_list" in result:
                msg.reward_list = [float(x) for x in result["reward_list"]]
            if "global_reward" in result:
                msg.global_reward = float(result["global_reward"])
        else:
            msg.global_reward = float(result)
            msg.reward_list = [msg.global_reward] * (msg.turn_count or 1)
