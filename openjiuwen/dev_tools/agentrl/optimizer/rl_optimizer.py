# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""
RLOptimizer -- user-facing RL training entrypoint.

Provides a simple API to configure, initialize, and run the
Verl-based RL training pipeline.
"""

import os
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional

import ray
from hydra import compose, initialize
from omegaconf import OmegaConf

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import logger
from openjiuwen.dev_tools.agentrl.config.schemas import RLConfig
from openjiuwen.dev_tools.agentrl.coordinator.schemas import RLTask
from openjiuwen.dev_tools.agentrl.monitoring.metrics_tracker import RLMetricsTracker
from openjiuwen.dev_tools.agentrl.rollout_store.base import RolloutPersistence
from openjiuwen.dev_tools.agentrl.rollout_store.file_store import FileRolloutStore
from openjiuwen.dev_tools.agentrl.rollout_store.null_store import NullRolloutStore
from openjiuwen.dev_tools.agentrl.reward.registry import reward_registry
from openjiuwen.dev_tools.agentrl.agent_runtime.agent_factory import build_agent_factory
from openjiuwen.dev_tools.agentrl.optimizer.task_runner import TaskRunner, get_ppo_ray_runtime_env


class RLOptimizer:
    """
    Top-level RL training entrypoint.

    Usage::

        optimizer = RLOptimizer(config)
        optimizer.register_reward(my_reward_fn, name="my_reward")
        optimizer.set_tools([calculator])
        optimizer.set_task_data_fn(my_task_data_fn)
        optimizer.train()
    """

    def __init__(self, config: RLConfig) -> None:
        self.config = config
        self._runner = None
        self._task_runner = None
        self._agent_factory = None
        self._task_data_fn = None
        self._reward_fn_name: Optional[str] = None
        self._reward_fn = None
        self._tools = []
        self._tool_names = []

        timestamp = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")
        self.run_name = f"{config.training.experiment_name}_{timestamp}"
        logger.info(
            "Run name: %s (project: %s)", self.run_name, config.training.project_name
        )

    # -- Main User Configuration Interfaces ---------------------------------------------------
    #
    # Users typically call the following methods in example / business code:
    # - set_tools: register tools (recommended)
    # - set_task_data_fn: configure data row → agent input
    # - set_task_runner: (optional) fully customize task execution logic
    # - register_reward: register reward function
    #
    #

    def set_tools(self, tools: list) -> None:
        """Register tools for the agent."""
        self._tools = tools
        self._tool_names = [
            getattr(t, "name", getattr(t, "__name__", str(t))) for t in tools
        ]

    def set_task_runner(self, task_runner) -> None:
        """Set a custom task_runner: ``async (RLTask) -> RolloutMessage``."""
        self._task_runner = task_runner

    def set_task_data_fn(
        self, fn: Callable[[Dict[str, Any]], Dict[str, Any]]
    ) -> None:
        """Set function to convert dataset rows to agent inputs."""
        self._task_data_fn = fn

    # -- reward registration ------------------------------------------------

    def register_reward(self, fn, name: Optional[str] = None) -> None:
        """Register a reward function in the global reward registry."""
        reward_name = name or fn.__name__
        reward_registry.register(reward_name, fn)
        self._reward_fn_name = reward_name
        self._reward_fn = fn
        logger.info("Registered reward function: %s", reward_name)

    def _get_rollout_reward_fn(self):
        return getattr(self, "_reward_fn", None)

    # -- agent factory override (advanced) -----------------------------------

    def set_agent_factory(self, factory: Callable[[RLTask], Any]) -> None:
        """Override the default AgentFactory.

        For typical use cases, call ``set_tools()`` instead; RLOptimizer
        will build the AgentFactory automatically from config and tools.
        """
        self._agent_factory = factory

    def _resolve_agent_factory(self) -> Optional[Callable]:
        """Return the effective agent factory (custom or built-in)."""
        if self._agent_factory is not None:
            return self._agent_factory
        if self._tools:
            return build_agent_factory(
                self.config.runtime, self._tools, self._tool_names
            )
        return None

    # -- persistence & metrics builders -------------------------------------

    def _build_persistence(self, config: RLConfig) -> RolloutPersistence:
        p_cfg = config.persistence
        if not p_cfg.enabled:
            return NullRolloutStore()
        save_path = os.path.join(p_cfg.save_path, self.run_name)
        return FileRolloutStore(
            save_path=save_path,
            flush_interval=p_cfg.flush_interval,
        )

    def _build_metrics_tracker(self, config: RLConfig) -> RLMetricsTracker:
        return RLMetricsTracker(
            project_name=config.training.project_name,
            experiment_name=self.run_name,
            backends=config.training.logger,
            config=config.model_dump() if hasattr(config, "model_dump") else {},
        )

    # -- Hydra / Ray initialisation -----------------------------------------

    def _compose_hydra_config(self):
        """Compose the full Verl config by merging YAML defaults with RLConfig."""
        train_cfg = self.config.training
        rollout_cfg = self.config.rollout

        with initialize(config_path="../config", version_base=None):
            base_cfg = compose(config_name="config")

        OmegaConf.set_struct(base_cfg, False)

        dynamic = {
            "algorithm": {
                "adv_estimator": train_cfg.algorithm_adv_estimator,
                "use_kl_in_reward": train_cfg.algorithm_use_kl_in_reward,
                "filter_groups": train_cfg.algorithm_filter_groups,
                "norm_adv_by_std_in_grpo": train_cfg.algorithm_norm_adv_by_std_in_grpo,
            },
            "data": {
                "train_files": train_cfg.resolved_train_files,
                "val_files": train_cfg.resolved_val_files,
                "train_batch_size": train_cfg.train_batch_size,
                "gen_batch_size": train_cfg.gen_batch_size,
                "max_prompt_length": train_cfg.max_prompt_length,
                "max_response_length": train_cfg.max_response_length,
                "truncation": train_cfg.truncation,
            },
            "actor_rollout_ref": {
                "model": {
                    "path": train_cfg.model_path,
                },
                "actor": {
                    "ppo_micro_batch_size_per_gpu": train_cfg.micro_batch_size_per_gpu,
                    "optim": {"lr": rollout_cfg.actor_optimizer_lr},
                    "use_kl_loss": rollout_cfg.actor_use_kl_loss,
                    "kl_loss_coef": rollout_cfg.actor_kl_loss_coef,
                    "entropy_coeff": rollout_cfg.actor_entropy_coef,
                    "clip_ratio_low": rollout_cfg.actor_clip_ratio_low,
                    "clip_ratio_high": rollout_cfg.actor_clip_ratio_high,
                    "loss_agg_mode": rollout_cfg.actor_loss_agg_mode,
                },
                "rollout": {
                    "n": rollout_cfg.rollout_n,
                    "log_prob_micro_batch_size_per_gpu": train_cfg.micro_batch_size_per_gpu,
                },
                "ref": {
                    "log_prob_micro_batch_size_per_gpu": train_cfg.micro_batch_size_per_gpu,
                },
            },
            "trainer": {
                "val_before_train": train_cfg.val_before_train,
                "critic_warmup": train_cfg.critic_warmup,
                "logger": train_cfg.logger,
                "project_name": train_cfg.project_name,
                "experiment_name": self.run_name,
                "nnodes": train_cfg.nnodes,
                "save_freq": train_cfg.save_freq,
                "test_freq": train_cfg.test_freq,
                "default_local_dir": train_cfg.save_path,
                "total_epochs": train_cfg.total_epochs,
                "n_gpus_per_node": train_cfg.n_gpus_per_node,
                "runtime_parallel_num": train_cfg.rollout_concurrency,
                "rollout_max_round": rollout_cfg.rollout_max_round,
            },
            "JiuwenRL": {
                "whole_trajectory": train_cfg.whole_trajectory,
            },
        }

        if self.config.ada is not None:
            ada_cfg = self.config.ada
            dynamic["trainer"]["rollout_max_round"] = ada_cfg.rollout_max_round
            dynamic["JiuwenRL"]["custom_fn"] = {
                "classifier": "default_classify_rollouts",
                "validator": "validate_stop_balanced",
                "sampler": "sampling_ada",
            }
            dynamic["JiuwenRL"]["final_keep_per_prompt"] = ada_cfg.final_keep_per_prompt

        return OmegaConf.merge(base_cfg, dynamic)

    def _setup_environment(self) -> None:
        """Set required environment variables for Ray / vLLM / NCCL."""
        train_cfg = self.config.training

        os.environ["HYDRA_FULL_ERROR"] = "1"
        os.environ["VLLM_PREFIX_CACHING"] = "0"
        os.environ["ENABLE_PREFIX_CACHE"] = "false"
        os.environ["TORCHINDUCTOR_COMPILE"] = "0"
        os.environ["TORCHDYNAMO_DISABLE"] = "1"
        os.environ["VLLM_ASCEND_DISABLE_CAMEM"] = "1"
        os.environ["DISABLE_CAMEM_ALLOCATOR"] = "1"
        os.environ["VLLM_DISABLE_COMPILE_CACHE"] = "1"
        os.environ["VLLM_ASCEND_CAMEM_ENABLE"] = "0"
        os.environ["ASCEND_LAUNCHING_BLOCKING"] = "1"
        os.environ["ASCEND_RT_VISIBLE_DEVICES"] = train_cfg.visible_device
        os.environ["VLLM_USE_V1"] = "1"

        for k in ["http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"]:
            os.environ.pop(k, None)
        os.environ["no_proxy"] = "127.0.0.1,localhost"
        os.environ["NO_PROXY"] = "127.0.0.1,localhost"

    @staticmethod
    def _init_ray(cfg) -> None:
        """Initialize the Ray cluster if not already running."""
        if ray.is_initialized():
            return
        default_runtime_env = get_ppo_ray_runtime_env()
        ray_init_kwargs = cfg.ray_kwargs.get("ray_init", {})
        runtime_env_kwargs = ray_init_kwargs.get("runtime_env", {})
        runtime_env = OmegaConf.merge(default_runtime_env, runtime_env_kwargs)
        ray_init_kwargs = OmegaConf.create(
            {**ray_init_kwargs, "runtime_env": runtime_env, "namespace": "JiuwenRL"}
        )
        logger.info("ray init kwargs: %s", ray_init_kwargs)
        ray.init(**OmegaConf.to_container(ray_init_kwargs))

    # -- training lifecycle -------------------------------------------------

    def init_trainer(self) -> None:
        """Initialize the Ray-based training system."""
        cfg = self._compose_hydra_config()
        self._setup_environment()
        self._init_ray(cfg)

        persistence = self._build_persistence(self.config)
        metrics_tracker = self._build_metrics_tracker(self.config)
        agent_factory = self._resolve_agent_factory()

        self._runner = TaskRunner.options(
            name="ppo_task_runner", lifetime="detached"
        ).remote()
        ray.get(
            self._runner.init_trainer.remote(
                cfg,
                task_runner=self._task_runner,
                agent_factory=agent_factory,
                task_data_fn=self._task_data_fn,
                reward_fn=self._get_rollout_reward_fn(),
                metrics_tracker=metrics_tracker,
                persistence=persistence,
            )
        )
        logger.info("Trainer initialized successfully")

    def start_training(self) -> None:
        """Start the training loop on the remote TaskRunner."""
        if self._runner is None:
            try:
                self._runner = ray.get_actor("ppo_task_runner")
            except ValueError as e:
                raise build_error(
                    StatusCode.AGENT_RL_TASK_RUNNER_NOT_INITIALIZED,
                    error_msg="call init_trainer() first",
                    cause=e,
                ) from e
        ray.get(self._runner.start_trainer.remote())
        logger.info("Training completed")

    def stop(self) -> None:
        """Tear down the TaskRunner actor and shutdown Ray."""
        try:
            actor = ray.get_actor("ppo_task_runner")
            ray.kill(actor, no_restart=True)
        except ValueError:
            logger.info("No Ray actor named 'ppo_task_runner' running")
        self._runner = None
        ray.shutdown()
        logger.info("Ray shutdown complete")

    def train(self) -> None:
        """Initialize and run the full training pipeline in one call."""
        self.init_trainer()
        self.start_training()
