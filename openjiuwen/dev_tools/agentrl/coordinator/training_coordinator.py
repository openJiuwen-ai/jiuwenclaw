# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""
TrainingCoordinator
------------------

Controller class that orchestrates the full rollout-feedback-update cycle.

Manages:
- Parallel rollout generation through worker executors
- Multi-round task scheduling and state tracking
- Rollout classification, validation, and sampling
- Batch assembly for RL training via RLBatchBuilder
- Synchronous and asynchronous validation routines
- Caching and merging of positive/negative rollouts across rounds
"""

import asyncio
import functools
import uuid
from typing import Any, Dict, List, Tuple

import numpy as np

from openjiuwen.core.common.logging import logger
from openjiuwen.dev_tools.agentrl.agent_runtime.parallel_executor import (
    ParallelRuntimeExecutor,
)
from openjiuwen.dev_tools.agentrl.coordinator.batch_builder import RLBatchBuilder
from openjiuwen.dev_tools.agentrl.coordinator.encoding import RolloutEncoder
from openjiuwen.dev_tools.agentrl.coordinator.processors import ProcessorsRegistry
from openjiuwen.dev_tools.agentrl.coordinator.schemas import RLTask
from openjiuwen.dev_tools.agentrl.coordinator.task_queue import TaskQueue


class TrainingCoordinator:
    """
    Core loop coordinator handling task submission, rollout collection,
    stop-condition checking, and construction of training-ready RL batches.
    """

    def __init__(self, config, tokenizer, persistence=None):
        self.config = config
        self._total_positive = 0
        self._total_negative = 0
        self._total_activate_num = 0
        self.round_state: List[Dict[str, Any]] = []
        self._rollout_state: Dict[str, Dict[str, Any]] = {}
        self.positive_cache: Dict[str, list] = {}
        self.negative_cache: Dict[str, list] = {}
        self._turn_counts: List[int] = []
        self.last_avg_turn_count: float = 0.0
        self._reward_lists: List[List[float]] = []
        self.rewards_by_uid: Dict[str, List[Dict[str, Any]]] = {}
        self.last_training_sample_count: int = 0  # training samples in current step (post-split granularity)

        self.tokenizer = tokenizer
        self.datastore = TaskQueue()
        self.processors_registry = ProcessorsRegistry()
        self.batch_builder = RLBatchBuilder(
            config["data"]["max_prompt_length"],
            tokenizer.pad_token_id,
            config["data"]["max_response_length"],
        )
        self.parallel_executor: ParallelRuntimeExecutor | None = None
        self._is_parallel_setup = False
        self.rollout_encoder = RolloutEncoder(tokenizer=tokenizer)

        self._persistence = persistence
        self._current_step = 0
        self._final_keep_per_prompt = config.get(
            "JiuwenRL", {}
        ).get("final_keep_per_prompt")

        try:
            self.whole_trajectory = bool(
                config["JiuwenRL"]["whole_trajectory"]
            )
        except (KeyError, TypeError, AttributeError):
            self.whole_trajectory = False
        if self.whole_trajectory:
            logger.info("Whole-trajectory training mode ENABLED")
        else:
            logger.info("Per-turn training mode ENABLED")

    # -- public entry points ------------------------------------------------

    async def run_demon_loop(self, rl_data, device, *, step: int = 0):
        """Run the full rollout-feedback-update demon loop."""
        self._current_step = step
        logger.info("Setting up RL trainer data to datastore.")
        await self._setup_parallel()
        tasks_dic = self._build_initial_tasks(rl_data)
        self.clear_up_data()
        logger.info("Starting RL trainer demon loop with parallel execution.")

        try:
            await self._run_rounds(tasks_dic)
        except Exception as e:
            logger.error("Error in demon loop: %s", e)
            raise
        finally:
            await self._stop_parallel_executor_if_needed()

        rl_batch, merged_dict = self._build_rl_batch_from_caches(device)
        self.last_training_sample_count = getattr(
            rl_batch, "batch_size", sum(len(v) for v in merged_dict.values())
        )
        self.last_avg_turn_count = (
            float(np.mean(self._turn_counts)) if self._turn_counts else 0.0
        )
        all_rewards = [r for rl in self._reward_lists for r in rl]
        reward_mean = float(np.mean(all_rewards)) if all_rewards else 0.0
        logger.info(
            "Rollout done: %d rollouts, %d training samples, avg_turns=%.2f, reward_mean=%.4f",
            len(merged_dict),
            self.last_training_sample_count,
            self.last_avg_turn_count,
            reward_mean,
        )
        return rl_batch

    def run_demon_loop_sync(self, rl_data, device, *, step: int = 0):
        """Synchronous wrapper for run_demon_loop."""
        return asyncio.run(self.run_demon_loop(rl_data, device, step=step))

    async def validate(self, rl_data):
        """Perform a full validation pass using one batch of tasks."""
        await self._initialize_parallel_processing()
        batch_size = len(next(iter(rl_data.values())))
        self.clear_up_data()
        tasks_dic: Dict[str, RLTask] = {}

        for i in range(batch_size):
            task = {key: rl_data[key][i] for key in rl_data}
            task_id = str(uuid.uuid4())
            tasks_dic[task_id] = RLTask(
                task_id=task_id,
                origin_task_id=task_id,
                task_sample=task,
                round_num=0,
            )

        try:
            await self._submit_tasks_for_round(tasks_dic)
            collected_data = await self._wait_for_tasks_completion(round_id=0)
        except Exception as e:
            logger.error("Error in validation: %s", e)
            raise
        finally:
            if self.parallel_executor and self.parallel_executor.is_running():
                await self.parallel_executor.stop()
                logger.debug("Parallel executor stopped during validation")

        if self._persistence is not None:
            for _, rollout_msg in collected_data.items():
                try:
                    await self._persistence.save_rollout(
                        step=getattr(self, '_current_step', 0),
                        task_id=rollout_msg.task_id or "",
                        rollout=rollout_msg,
                        phase="val",
                    )
                except Exception as e:
                    logger.debug("Persistence save_rollout (val) failed: %s", e)

        global_rewards = []
        turn_num = []
        rewards = []
        for _, rollout_msg in collected_data.items():
            reward_list = rollout_msg.reward_list
            rewards.append(reward_list)
            turn_num.append(len(rollout_msg.rollout_info))
            global_rewards.append(rollout_msg.global_reward)

        # Accuracy: global_reward >= 0.9 means correct final answer
        correct_count = sum(1 for r in global_rewards if r >= 0.9)
        total_count = len(global_rewards)
        accuracy = correct_count / total_count if total_count > 0 else 0.0

        logger.info(
            "Validation reward_list: %s  accuracy=%.4f (%d/%d)",
            rewards,
            accuracy,
            correct_count,
            total_count,
        )

        return {
            "val/global_reward_mean": float(np.mean(global_rewards)) if global_rewards else 0.0,
            "val/accuracy": accuracy,
            "val/correct_count": correct_count,
            "val/sample_count": total_count,
            "val/average_turn_num": float(np.mean(turn_num)) if turn_num else 0.0,
            "val/turn_num": turn_num,
            "val/reward_list": rewards,
        }

    def validate_sync(self, rl_data):
        """Synchronous wrapper for the asynchronous validation routine."""
        return asyncio.run(self.validate(rl_data))

    # -- cache management ---------------------------------------------------

    @staticmethod
    def merge_caches(pos_cache, neg_cache):
        """Merge positive and negative rollout caches into a unified dict keyed by UID."""
        rollout_dict = {}
        for uid in set(pos_cache.keys()) | set(neg_cache.keys()):
            rollout_dict[uid] = pos_cache.get(uid, []) + neg_cache.get(uid, [])
        return rollout_dict

    def clear_up_data(self):
        """Reset rollout caches and counters for a fresh cycle.

        Fully clear TaskQueue (queue + in-processing) to prevent stale state from prior step.
        """
        self.round_state.clear()
        self._rollout_state.clear()
        self._total_positive = 0
        self._total_negative = 0
        self._total_activate_num = 0
        self.positive_cache.clear()
        self.negative_cache.clear()
        self._turn_counts.clear()
        self._reward_lists.clear()
        self.rewards_by_uid.clear()
        self.datastore.clear()

    # -- parallel executor setup --------------------------------------------

    def _setup_parallel_executor(self):
        """Set up the parallel executor with stored configuration."""
        if self._is_parallel_setup and self.parallel_executor is not None:
            return
        num_workers = self.config["trainer"]["runtime_parallel_num"]
        self.parallel_executor = ParallelRuntimeExecutor(
            data_store=self.datastore,
            num_workers=num_workers,
        )
        self._is_parallel_setup = True
        logger.debug("Parallel executor setup with %d workers", num_workers)

    def configure_parallel_executor(
        self,
        *,
        task_runner=None,
        agent_factory=None,
        task_data_fn=None,
        reward_fn=None,
    ):
        """Inject runtime configuration into the parallel executor."""
        self._setup_parallel_executor()
        if task_runner is not None:
            self.parallel_executor.set_task_runner(task_runner)
        if agent_factory is not None:
            self.parallel_executor.set_agent_factory(agent_factory)
        if task_data_fn is not None:
            self.parallel_executor.set_task_data_fn(task_data_fn)
        if reward_fn is not None:
            self.parallel_executor.set_reward_fn(reward_fn)

    async def _initialize_parallel_processing(self):
        """Initialize and start parallel processing."""
        self._setup_parallel_executor()
        if not self.parallel_executor.is_running():
            await self.parallel_executor.start()
            logger.debug("Parallel executor started")

    async def _setup_parallel(self) -> None:
        """Initialize and start the parallel executor if not already running."""
        await self._initialize_parallel_processing()

    # -- task building ------------------------------------------------------

    @classmethod
    def _infer_batch_size(cls, rl_data) -> int:
        """Infer batch size from the first column length in rl_data."""
        return len(next(iter(rl_data.values())))

    @classmethod
    def _build_task_sample(cls, rl_data, index: int) -> Dict[str, Any]:
        """Extract a single task sample at index from rl_data columns."""
        return {key: rl_data[key][index] for key in rl_data}

    def _build_initial_tasks(self, rl_data) -> Dict[str, RLTask]:
        """Build the initial task dictionary from rl_data."""
        batch_size = self._infer_batch_size(rl_data)
        tasks_dic: Dict[str, RLTask] = {}

        for i in range(batch_size):
            task_id = str(uuid.uuid4())
            rollout_n = self.config["actor_rollout_ref"]["rollout"]["n"]
            for _ in range(rollout_n):
                rollout_n_id = str(uuid.uuid4())
                task_sample = self._build_task_sample(rl_data, i)
                tasks_dic[rollout_n_id] = RLTask(
                    task_id=rollout_n_id,
                    origin_task_id=task_id,
                    task_sample=task_sample,
                    round_num=0,
                )
        return tasks_dic

    # -- round execution ----------------------------------------------------

    async def _submit_tasks_for_round(self, tasks_dic):
        """Submit unfinished tasks for the current round."""
        task_list = list(tasks_dic.values())
        await self._initialize_tasks(task_list)
        logger.debug("Submitted %d tasks for current round", len(task_list))

    async def _initialize_tasks(self, task_list: List[RLTask]):
        """Enqueue tasks into the datastore."""
        for task in task_list:
            task_id = await self.datastore.queue_task(task)
            if task.origin_task_id not in self._rollout_state:
                self._rollout_state[task.origin_task_id] = {
                    "neg": 0,
                    "pos": 0,
                    "finished": False,
                }

    async def _wait_for_tasks_completion(
        self, round_id: int, poll_interval: float = 1
    ):
        """Poll the datastore until all tasks for this round are complete."""
        logger.debug("Waiting for tasks completion in round %d.", round_id)
        collected_data = {}

        while True:
            if self.datastore.is_finished():
                break

            current_data = await self.datastore.get_rollouts()
            if current_data:
                collected_data.update(current_data)
            await asyncio.sleep(poll_interval)

        final_data = await self.datastore.get_rollouts()
        if final_data:
            collected_data.update(final_data)
        logger.info(
            "Round %d: collected %d rollouts", round_id, len(collected_data)
        )
        return collected_data

    async def _run_rounds(self, tasks_dic: Dict[str, RLTask]) -> None:
        """Run rollout rounds until all tasks are finished or max rounds reached."""
        max_round = self.config["trainer"]["rollout_max_round"]

        for round_id in range(max_round):
            if not tasks_dic:
                logger.debug("All tasks finished, ending demon loop")
                break

            logger.info(
                "Round %d: %d tasks", round_id, len(tasks_dic),
            )
            await self._submit_tasks_for_round(tasks_dic)
            collected_mdp = await self._collect_round_mdp(round_id)
            self._update_rollout_state(round_id, collected_mdp)
            tasks_dic, finished_count = self._filter_unfinished_tasks(tasks_dic)
            logger.debug(
                "Round %d: %d finished, %d remaining",
                round_id, finished_count, len(tasks_dic),
            )

    async def _collect_round_mdp(self, round_id: int) -> Dict[str, list]:
        """Collect rollout messages and group by origin_task_id."""
        collected_data = await self._wait_for_tasks_completion(round_id)
        collected_mdp: Dict[str, list] = {}

        for _, rollout_msg in collected_data.items():
            if len(rollout_msg.rollout_info):
                self._turn_counts.append(len(rollout_msg.rollout_info))
                if rollout_msg.reward_list:
                    self._reward_lists.append(rollout_msg.reward_list)
                uid = rollout_msg.origin_task_id
                if uid not in collected_mdp:
                    collected_mdp[uid] = []
                global_r = rollout_msg.global_reward
                if global_r is None and rollout_msg.reward_list:
                    global_r = rollout_msg.reward_list[-1]
                self.rewards_by_uid.setdefault(uid, []).append({
                    "global": global_r,
                    "per_turn": rollout_msg.reward_list or [],
                })

                # whole-trajectory: entire multi-turn conversation as one sample
                # per-turn (default): each turn as a separate sample
                if self.whole_trajectory:
                    collected_mdp[rollout_msg.origin_task_id].extend(
                        self.rollout_encoder.build_whole_trajectory(
                            rollout_msg
                        )
                    )
                else:
                    collected_mdp[rollout_msg.origin_task_id].extend(
                        self.rollout_encoder.build(rollout_msg)
                    )

        if self._persistence is not None:
            for _, rollout_msg in collected_data.items():
                try:
                    await self._persistence.save_rollout(
                        step=getattr(self, '_current_step', 0),
                        task_id=rollout_msg.task_id or "",
                        rollout=rollout_msg,
                        phase="train",
                    )
                except Exception as e:
                    logger.debug("Persistence save_rollout failed: %s", e)

        return collected_mdp

    def _update_rollout_state(self, round_id, collected_mdp):
        """Update caches and completion flags from newly collected rollout results."""
        classifier_func = self.processors_registry.get_classifier(
            self.config["JiuwenRL"]["custom_fn"]["classifier"]
        )
        valid_func = self.processors_registry.get_validator(
            self.config["JiuwenRL"]["custom_fn"]["validator"]
        )
        valid_func = self._bind_keep_param(valid_func)
        active_task = 0

        for task_id, mdp_list in collected_mdp.items():
            pos_rollouts, neg_rollouts = classifier_func(mdp_list)
            active_task += 1
            self._total_activate_num += 1
            self._total_positive += len(pos_rollouts)
            self._total_negative += len(neg_rollouts)

            if neg_rollouts:
                self.negative_cache.setdefault(task_id, []).extend(neg_rollouts)
            if pos_rollouts:
                self.positive_cache.setdefault(task_id, []).extend(pos_rollouts)

            is_finish = valid_func(
                self.positive_cache.get(task_id, []),
                self.negative_cache.get(task_id, []),
            )

            if task_id not in self._rollout_state:
                self._rollout_state[task_id] = {"neg": 0, "pos": 0, "finished": False}
            self._rollout_state[task_id]["finished"] = is_finish
            self._rollout_state[task_id]["pos"] += len(pos_rollouts)
            self._rollout_state[task_id]["neg"] += len(neg_rollouts)

        self.round_state.append(
            {
                "round_id": round_id,
                "active_num_this_round": active_task,
                "_total_activate_num": self._total_activate_num,
            }
        )

    def _filter_unfinished_tasks(
        self, tasks_dic: Dict[str, RLTask]
    ) -> Tuple[Dict[str, RLTask], int]:
        """Filter out finished tasks and increment round numbers."""
        unfinished_tasks: Dict[str, RLTask] = {}
        for task_id, task in tasks_dic.items():
            if (
                task.origin_task_id in self._rollout_state
                and not self._rollout_state[task.origin_task_id]["finished"]
            ):
                task.round_num += 1
                unfinished_tasks[task_id] = task
        finished_count = len(tasks_dic) - len(unfinished_tasks)
        return unfinished_tasks, finished_count

    def _bind_keep_param(self, func):
        """Bind ``final_keep_per_prompt`` if the function accepts it and the value is configured."""
        if self._final_keep_per_prompt is None:
            return func
        import inspect
        sig = inspect.signature(func)
        if "final_keep_per_prompt" in sig.parameters:
            return functools.partial(
                func, final_keep_per_prompt=self._final_keep_per_prompt
            )
        return func

    async def _stop_parallel_executor_if_needed(self) -> None:
        """Stop the parallel executor if it is currently running."""
        if self.parallel_executor and self.parallel_executor.is_running():
            await self.parallel_executor.stop()
            logger.debug("Parallel executor stopped")

    def _build_rl_batch_from_caches(self, device):
        """Sample rollouts from caches and build the RL training batch."""
        sampling_func = self.processors_registry.get_sampler(
            self.config["JiuwenRL"]["custom_fn"]["sampler"]
        )
        sampling_func = self._bind_keep_param(sampling_func)
        pos_rollout_dict, neg_rollout_dict = sampling_func(
            self.positive_cache, self.negative_cache
        )
        merged_dict = self.merge_caches(pos_rollout_dict, neg_rollout_dict)
        rl_batch = self.batch_builder.generate_rl_batch(merged_dict, device)
        return rl_batch, merged_dict
