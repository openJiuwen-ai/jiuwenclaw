# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""
TaskRunner -- Ray remote actor that orchestrates training initialization and execution.

Also contains Ray runtime environment helpers used by RLOptimizer.
"""

import json
import os
from typing import Optional

import ray
from omegaconf import OmegaConf
from ray._private.runtime_env.constants import RAY_JOB_CONFIG_JSON_ENV_VAR
from verl.single_controller.ray import RayWorkerGroup
from verl.trainer.main_ppo import create_rl_sampler
from verl.trainer.ppo.ray_trainer import ResourcePoolManager, Role
from verl.trainer.ppo.reward import load_reward_manager
from verl.utils import hf_processor, hf_tokenizer
from verl.utils.fs import copy_to_local
from verl.workers.fsdp_workers import (
    ActorRolloutRefWorker,
    AsyncActorRolloutRefWorker,
    CriticWorker,
)

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import logger

from openjiuwen.dev_tools.agentrl.coordinator.dataset import AgentDataset
from openjiuwen.dev_tools.agentrl.rl_trainer.main_trainer import MainTrainer
from openjiuwen.dev_tools.agentrl.rl_trainer.verl_executor import VerlTrainingExecutor


# ---------------------------------------------------------------------------
# Ray runtime env defaults
# ---------------------------------------------------------------------------

_AGENT_RL_PARENT_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..")
)

_AGENT_CORE_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "..")
)

_PPO_RAY_RUNTIME_ENV = {
    "env_vars": {
        "TOKENIZERS_PARALLELISM": "true",
        "NCCL_DEBUG": "WARN",
        "VLLM_LOGGING_LEVEL": "WARN",
        "VLLM_ALLOW_RUNTIME_LORA_UPDATING": "true",
        "CUDA_DEVICE_MAX_CONNECTIONS": "1",
        "NCCL_CUMEM_ENABLE": "0",
        "VLLM_ASCEND_ENABLE_NZ": "0",
    },
}


def get_ppo_ray_runtime_env() -> dict:
    """Inherit Ray Job working_dir and merge custom env vars."""
    working_dir = (
        json.loads(os.environ.get(RAY_JOB_CONFIG_JSON_ENV_VAR, "{}"))
        .get("runtime_env", {})
        .get("working_dir", None)
    )
    env_vars = _PPO_RAY_RUNTIME_ENV["env_vars"].copy()

    existing_pp = os.environ.get("PYTHONPATH", "")
    path_parts = [_AGENT_CORE_DIR, _AGENT_RL_PARENT_DIR]
    if existing_pp:
        path_parts.append(existing_pp)
    env_vars["PYTHONPATH"] = ":".join(path_parts)

    runtime_env: dict = {
        "env_vars": env_vars,
        **({"working_dir": None} if working_dir is None else {}),
    }
    for key in list(runtime_env["env_vars"].keys()):
        if key == "PYTHONPATH":
            continue
        if os.environ.get(key) is not None:
            runtime_env["env_vars"].pop(key, None)
    return runtime_env


# ---------------------------------------------------------------------------
# TaskRunner -- Ray remote actor
# ---------------------------------------------------------------------------


@ray.remote(num_cpus=1)
class TaskRunner:
    """Ray remote actor that orchestrates training initialization and execution."""

    def __init__(self):
        self.main_trainer: Optional[MainTrainer] = None

    # -- component initialisation helpers -----------------------------------

    @classmethod
    def _init_model_components(cls, config):
        """Load tokenizer and processor from model path."""
        local_path = copy_to_local(config.actor_rollout_ref.model.path)
        trust_remote_code = config.data.get("trust_remote_code", False)
        tokenizer = hf_tokenizer(local_path, trust_remote_code=trust_remote_code)
        processor = hf_processor(local_path, use_fast=True)
        return tokenizer, processor

    @classmethod
    def _init_worker_mapping(cls, config):
        """Build role-to-Ray-worker mapping based on strategy (fsdp/fsdp2) and rollout mode."""
        strategy = config.actor_rollout_ref.actor.strategy
        if strategy not in {"fsdp", "fsdp2"}:
            raise build_error(
                StatusCode.AGENT_RL_STRATEGY_NOT_SUPPORTED,
                strategy=strategy,
            )
        is_async = config.actor_rollout_ref.rollout.mode == "async"
        actor_rollout_cls = (
            AsyncActorRolloutRefWorker if is_async else ActorRolloutRefWorker
        )
        role_worker_mapping = {
            Role.ActorRollout: ray.remote(actor_rollout_cls),
            Role.Critic: ray.remote(CriticWorker),
            Role.RefPolicy: ray.remote(actor_rollout_cls),
        }
        return role_worker_mapping, RayWorkerGroup

    @classmethod
    def _init_resource_pools(cls, config):
        """Create ResourcePoolManager for GPU allocation across workers."""
        global_pool_id = "global_pool"
        resource_pool_spec = {
            global_pool_id: [config.trainer.n_gpus_per_node] * config.trainer.nnodes,
        }
        mapping = {
            Role.ActorRollout: global_pool_id,
            Role.Critic: global_pool_id,
            Role.RefPolicy: global_pool_id,
        }
        return ResourcePoolManager(
            resource_pool_spec=resource_pool_spec,
            mapping=mapping,
        )

    @classmethod
    def _init_reward_functions(cls, config, tokenizer):
        """Load training and validation reward managers from config."""
        reward_kwargs = config.reward_model.get("reward_kwargs", {})
        reward_fn = load_reward_manager(
            config, tokenizer, num_examine=0, **reward_kwargs
        )
        val_reward_fn = load_reward_manager(
            config, tokenizer, num_examine=1, **reward_kwargs
        )
        return reward_fn, val_reward_fn

    @classmethod
    def _init_datasets_and_sampler(cls, config, tokenizer, processor):
        """Create AgentDataset instances and RL sampler for train/val data."""
        from verl.utils.dataset.rl_dataset import collate_fn

        train_dataset = AgentDataset(
            data_files=config.data.train_files,
            tokenizer=tokenizer,
            processor=processor,
            config=config.data,
        )
        val_dataset = AgentDataset(
            data_files=config.data.val_files,
            tokenizer=tokenizer,
            processor=processor,
            config=config.data,
        )
        train_sampler = create_rl_sampler(config.data, train_dataset)
        return train_dataset, val_dataset, collate_fn, train_sampler

    # -- public methods called via Ray .remote() ----------------------------

    def init_trainer(
        self,
        config,
        *,
        task_runner=None,
        agent_factory=None,
        task_data_fn=None,
        reward_fn=None,
        metrics_tracker=None,
        persistence=None,
    ):
        """Initialize all trainer components and Ray workers."""
        logger.info(OmegaConf.to_container(config, resolve=True))
        OmegaConf.resolve(config)

        tokenizer, processor = self._init_model_components(config)
        role_worker_mapping, ray_worker_group_cls = self._init_worker_mapping(config)
        resource_pool_manager = self._init_resource_pools(config)
        verl_reward_fn, val_reward_fn = self._init_reward_functions(config, tokenizer)
        (
            train_dataset,
            val_dataset,
            collate_fn,
            train_sampler,
        ) = self._init_datasets_and_sampler(config, tokenizer, processor)

        verl_trainer = VerlTrainingExecutor(
            config=config,
            tokenizer=tokenizer,
            processor=processor,
            role_worker_mapping=role_worker_mapping,
            resource_pool_manager=resource_pool_manager,
            ray_worker_group_cls=ray_worker_group_cls,
            reward_fn=verl_reward_fn,
            val_reward_fn=val_reward_fn,
            train_dataset=train_dataset,
            val_dataset=val_dataset,
            collate_fn=collate_fn,
            train_sampler=train_sampler,
        )
        verl_trainer.init_workers()

        self.main_trainer = MainTrainer(
            rl_trainer=verl_trainer,
            config=config,
            collate_fn=collate_fn,
            train_sampler=train_sampler,
            task_runner=task_runner,
            agent_factory=agent_factory,
            task_data_fn=task_data_fn,
            reward_fn=reward_fn,
            metrics_tracker=metrics_tracker,
            persistence=persistence,
        )

    def start_trainer(self):
        """Start the MainTrainer training loop. Must call init_trainer() first."""
        if self.main_trainer is None:
            raise build_error(
                StatusCode.AGENT_RL_TRAINER_NOT_INITIALIZED,
                error_msg="call init_trainer() before start_trainer()",
            )
        self.main_trainer.fit()

