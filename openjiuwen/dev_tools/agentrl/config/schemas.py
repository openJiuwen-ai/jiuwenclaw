# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""
Pydantic configuration schemas for RL training.
"""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class PersistenceConfig(BaseModel):
    """Rollout persistence configuration."""
    enabled: bool = False
    save_path: str = None
    flush_interval: int = 100
    save_rollouts: bool = True
    save_step_summaries: bool = True


class TrainingConfig(BaseModel):
    """
    Training configuration covering data, model, algorithm, and Verl trainer params.
    """

    # --- project / experiment ---
    project_name: str = Field(default="OpenJiuwenAgentRL")
    experiment_name: str = Field(default="grpo_experiment")

    # --- data paths ---
    train_data_path: Optional[str] = None
    val_data_path: Optional[str] = None
    # aliases for backward compat
    train_files: Optional[str] = None
    val_files: Optional[str] = None

    # --- model ---
    model_path: str = None
    save_path: str = None

    # --- algorithm ---
    algorithm_adv_estimator: str = "grpo"
    algorithm_use_kl_in_reward: bool = False
    algorithm_filter_groups: bool = False
    algorithm_norm_adv_by_std_in_grpo: bool = True
    whole_trajectory: bool = False

    # --- training control ---
    epoch_num: int = 2
    total_epochs: int = 2  # alias
    save_freq: int = 20
    test_freq: int = 20
    train_batch_size: int = 32
    rollout_concurrency: int = 40

    # --- hardware ---
    visible_device: str = "0,1,2,3"
    nnodes: int = 1
    n_gpus_per_node: int = 4
    micro_batch_size_per_gpu: int = 4

    # --- sequence lengths ---
    max_prompt_length: int = 3072
    max_response_length: int = 3072
    truncation: str = "truncate"

    # --- validation ---
    val_before_train: bool = True

    # --- trainer / logging ---
    critic_warmup: int = 0
    logger: List[str] = Field(default_factory=lambda: ["tensorboard"])
    log_rollout_details: bool = True
    log_reward_distribution: bool = False

    # --- low-level overrides for Verl config (optional) ---
    verl_extra: Dict[str, Any] = Field(default_factory=dict)
    verl_config_path: Optional[str] = Field(default=None)

    @property
    def resolved_train_files(self) -> Optional[str]:
        """Return train data path, preferring train_files over train_data_path."""
        return self.train_files or self.train_data_path

    @property
    def resolved_val_files(self) -> Optional[str]:
        """Return validation data path, preferring val_files over val_data_path."""
        return self.val_files or self.val_data_path


class RolloutConfig(BaseModel):
    """Rollout / actor optimizer configuration."""

    actor_optimizer_lr: float = 1e-6
    actor_use_kl_loss: bool = False
    actor_kl_loss_coef: float = 0.02
    actor_entropy_coef: float = 0.0
    actor_clip_ratio_low: float = 0.2
    actor_clip_ratio_high: float = 0.3
    actor_loss_agg_mode: str = "seq-mean-token-mean"
    rollout_n: int = 8


class AgentRuntimeConfig(BaseModel):
    """Runtime hyper-parameters for the agent / inference."""

    system_prompt: Any = "You are a helpful assistant."
    temperature: float = 0.7
    top_p: float = 0.9
    max_new_tokens: int = 512
    presence_penalty: float = 0.0
    frequency_penalty: float = 0.0


class AdaConfig(BaseModel):
    """Parameters for the Ada rollout variant.

    When ``RLConfig.ada`` is set, Ada is enabled: ``trainer.rollout_max_round`` is taken
    from ``rollout_max_round`` below, and the custom classifier / validator / sampler
    (including ``validate_stop_balanced`` and ``sampling_ada``) are wired in.
    """

    rollout_max_round: int = 2
    final_keep_per_prompt: int = 8


class RLConfig(BaseModel):
    """Top level RL configuration."""

    training: TrainingConfig
    rollout: RolloutConfig = Field(default_factory=RolloutConfig)
    runtime: AgentRuntimeConfig = Field(default_factory=AgentRuntimeConfig)
    persistence: PersistenceConfig = Field(default_factory=PersistenceConfig)
    ada: Optional[AdaConfig] = Field(
        default=None,
        description="If provided, enable Ada rollout variant. Omit to use default rollout.",
    )
