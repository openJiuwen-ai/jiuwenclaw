# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""
RLMetricsTracker
----------------

Wraps verl's Tracking to provide structured logging for RL-specific
metrics (rollout stats, reward distributions, conversation turns, etc.).

Supported backends (via verl Tracking):
tensorboard, wandb, mlflow, swanlab, clearml, file
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import numpy as np

from openjiuwen.core.common.logging import logger


@dataclass
class TrainingStepMetrics:
    """Encapsulates all metrics for a single training step log entry."""

    step: int
    epoch: int
    verl_metrics: Dict[str, Any]
    avg_turns: float
    reward_mean: float
    consecutive_zero_reward_steps: int


class RLMetricsTracker:
    """Structured metrics tracker for RL training.

    Uses verl's Tracking as the unified logging backend.
    """

    def __init__(
        self,
        project_name: str,
        experiment_name: str,
        backends: List[str],
        config: Optional[Dict[str, Any]] = None,
    ) -> None:
        self._backends = backends
        self._tracking = None
        self._initialized = False
        self._init_kwargs = {
            "project_name": project_name,
            "experiment_name": experiment_name,
            "default_backend": backends,
            "config": config,
        }

    def _ensure_initialized(self) -> None:
        """Lazily initialize the underlying verl Tracking backend."""
        if self._initialized:
            return
        self._initialized = True
        try:
            from verl.utils.tracking import Tracking
            self._tracking = Tracking(**self._init_kwargs)
            logger.info(
                "RLMetricsTracker initialized with backends: %s", self._backends
            )
        except ImportError:
            logger.warning(
                "verl.utils.tracking not available, metrics logging disabled"
            )

    # -- core log method ------------------------------------------------------

    def log_step(self, step: int, metrics: Dict[str, Any]) -> None:
        """Log a dict of scalar metrics at the given step."""
        self._ensure_initialized()
        if self._tracking is not None:
            self._tracking.log(data=metrics, step=step)

    # -- structured logging helpers -------------------------------------------

    def log_training_step(self, data: TrainingStepMetrics) -> None:
        """Log a complete training step with RL-augmented metrics."""
        metrics = dict(data.verl_metrics)
        metrics.update({
            "training/global_step": data.step,
            "training/epoch": data.epoch,
            "training/avg_conversation_turns": data.avg_turns,
            "training/rollout_reward_mean": data.reward_mean,
            "training/consecutive_zero_reward_steps": data.consecutive_zero_reward_steps,
        })
        self.log_step(data.step, metrics)

    def log_rollout_stats(
        self,
        step: int,
        rewards_by_uid: Dict[str, List[Dict[str, Any]]],
        total_positive: int = 0,
        total_negative: int = 0,
        total_training_samples: Optional[int] = None,
    ) -> None:
        """Log structured rollout statistics.

        total_training_samples: Number of training samples used in the current step
            (granularity after splitting, e.g. per-turn count when using per-turn).
            If provided, used for rollout/total_rollouts; otherwise uses trajectory
            count len(all_rewards).
        """
        all_rewards = []
        for entries in rewards_by_uid.values():
            for entry in entries:
                val = entry.get("global")
                if val is not None:
                    all_rewards.append(val)

        if not all_rewards:
            return

        total = total_positive + total_negative
        n_rollouts = (
            total_training_samples
            if total_training_samples is not None
            else len(all_rewards)
        )
        rollout_metrics = {
            "rollout/reward_mean": float(np.mean(all_rewards)),
            "rollout/reward_std": float(np.std(all_rewards)),
            "rollout/reward_max": float(max(all_rewards)),
            "rollout/reward_min": float(min(all_rewards)),
            "rollout/positive_ratio": total_positive / max(total, 1),
            "rollout/total_rollouts": n_rollouts,
            "rollout/unique_prompts": len(rewards_by_uid),
        }
        self.log_step(step, rollout_metrics)

    def log_reward_distribution(self, step: int, rewards: List[float]) -> None:
        """Log reward distribution histogram (WandB only)."""
        self._ensure_initialized()
        if self._tracking is None or not rewards:
            return
        tracking_loggers = getattr(self._tracking, "logger", {})
        if "wandb" in tracking_loggers:
            try:
                import wandb
                wandb.log(
                    {"rollout/reward_hist": wandb.Histogram(rewards)},
                    step=step,
                )
            except Exception as e:
                logger.debug("Failed to log WandB reward histogram: %s", e)

    def log_validation(self, step: int, val_metrics: Dict[str, Any]) -> None:
        """Log validation metrics."""
        self.log_step(step, val_metrics)

    def finish(self) -> None:
        """Clean up tracking resources."""
        if self._tracking is not None and hasattr(self._tracking, "finish"):
            self._tracking.finish()
