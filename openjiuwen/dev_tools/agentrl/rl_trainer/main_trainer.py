# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""
MainTrainer
-----------

Training loop coordinator that orchestrates:
- VerlTrainingExecutor (PPO training)
- TrainingCoordinator (rollout generation and data assembly)
- DataLoaders for training and validation data
- BackendProxy (stable LLM inference URL for agents)
- Checkpointing, validation, and metrics logging
"""

import asyncio
import traceback
from typing import Optional

import numpy as np
from omegaconf import DictConfig
from torchdata.stateful_dataloader import StatefulDataLoader
from tqdm import tqdm

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import logger

from openjiuwen.dev_tools.agentrl.coordinator.training_coordinator import TrainingCoordinator
from openjiuwen.dev_tools.agentrl.monitoring.metrics_tracker import (
    RLMetricsTracker,
    TrainingStepMetrics,
)
from openjiuwen.dev_tools.agentrl.proxy.backend_proxy import BackendProxy
from openjiuwen.dev_tools.agentrl.rollout_store.base import RolloutPersistence


def _create_rl_sampler(data_config, dataset):
    """Create a sampler for the RL dataset based on coFnfiguration."""
    from torch.utils.data import RandomSampler, SequentialSampler

    sampler_type = data_config.get("sampler", "random")
    if sampler_type == "sequential":
        return SequentialSampler(dataset)
    return RandomSampler(dataset)


class MainTrainer:
    """
    Training loop coordinator.

    Wires together VerlTrainingExecutor, TrainingCoordinator, BackendProxy,
    and DataLoaders to run the full RL training cycle.
    """

    def __init__(
        self,
        rl_trainer,
        config: DictConfig,
        collate_fn=None,
        train_sampler=None,
        *,
        task_runner=None,
        agent_factory=None,
        task_data_fn=None,
        reward_fn=None,
        metrics_tracker: Optional[RLMetricsTracker] = None,
        persistence: Optional[RolloutPersistence] = None,
    ):
        self.rl_trainer = rl_trainer
        self.config = config
        self._metrics_tracker = metrics_tracker
        self._persistence = persistence
        self._agent_factory = agent_factory

        self.train_dataset = rl_trainer.train_dataset
        self.val_dataset = rl_trainer.val_dataset

        self.training_coordinator = TrainingCoordinator(
            config, rl_trainer.tokenizer, persistence=persistence
        )

        self.training_coordinator.configure_parallel_executor(
            task_runner=task_runner,
            agent_factory=agent_factory,
            task_data_fn=task_data_fn,
            reward_fn=reward_fn,
        )

        # -- BackendProxy (auto-assigned free port) -------------------------
        self._proxy = BackendProxy(
            llm_timeout_seconds=config.get("JiuwenRL", {}).get(
                "llm_timeout_seconds", 30_000
            )
        )
        self._proxy_started = False

        # -- DataLoaders ----------------------------------------------------
        num_workers = self.config.data.get("dataloader_num_workers", 0)
        gen_batch_size = self.config.data.get(
            "gen_batch_size", self.config.data.get("train_batch_size", 32)
        )

        if train_sampler is None:
            train_sampler = _create_rl_sampler(self.config.data, self.train_dataset)

        self.train_dataloader = StatefulDataLoader(
            dataset=self.train_dataset,
            batch_size=gen_batch_size,
            num_workers=num_workers,
            drop_last=True,
            collate_fn=collate_fn,
            sampler=train_sampler,
        )

        val_batch_size = len(self.val_dataset) if self.val_dataset else 1
        self.val_dataloader = (
            StatefulDataLoader(
                dataset=self.val_dataset,
                batch_size=val_batch_size,
                num_workers=num_workers,
                shuffle=self.config.data.get("validation_shuffle", False),
                drop_last=False,
                collate_fn=collate_fn,
            )
            if self.val_dataset
            else None
        )

    # -- proxy management ---------------------------------------------------

    @property
    def proxy_url(self) -> str:
        """Return the proxy URL (available after proxy starts)."""
        return self._proxy.url

    def _ensure_proxy_started(self) -> None:
        """Start the BackendProxy daemon thread if not already running."""
        if not self._proxy_started:
            self._proxy.start_sync()
            self._proxy_started = True
            if hasattr(self._agent_factory, "proxy_url"):
                self._agent_factory.proxy_url = self._proxy.url
            logger.info("BackendProxy started at %s", self._proxy.url)

    def update_backends(self, servers) -> None:
        """Update backend vLLM server list on the proxy."""
        self._ensure_proxy_started()
        self._proxy.update_backend_servers(servers)
        logger.info("Update backends success: %s", servers)

    # -- validation ---------------------------------------------------------

    def validate(self):
        """Run validation pass and return metrics."""
        if self.val_dataloader is None:
            logger.info("No validation dataset configured, skipping validation")
            return None

        server_addresses = self.rl_trainer.wake_up_rollout()
        self.update_backends(server_addresses)

        if len(self.val_dataloader) != 1:
            raise build_error(
                StatusCode.AGENT_RL_VALIDATION_DATASET_INVALID,
                error_msg="validation dataloader must yield exactly one batch, check val_batch_size config",
            )

        validation_rl_data = next(iter(self.val_dataloader))
        validation_metrics = self.training_coordinator.validate_sync(validation_rl_data)

        logger.info(
            "Global step %d validation result: %s",
            self.rl_trainer.global_steps,
            validation_metrics,
        )

        if self._metrics_tracker:
            scalar_metrics = {
                k: v for k, v in validation_metrics.items()
                if isinstance(v, (int, float))
            }
            self._metrics_tracker.log_validation(
                self.rl_trainer.global_steps, scalar_metrics
            )

        if self._persistence:
            try:
                asyncio.run(
                    self._persistence.save_step_summary(
                        self.rl_trainer.global_steps, validation_metrics
                    )
                )
            except Exception as e:
                logger.warning(
                    "Failed to persist validation summary (step %d): %s",
                    self.rl_trainer.global_steps, e,
                )

        self.rl_trainer.sleep_rollout()
        return validation_metrics

    # -- main training loop -------------------------------------------------

    def fit(self):
        """Main training loop with progress tracking and validation."""
        if hasattr(self.rl_trainer, "setup_logger"):
            self.rl_trainer.setup_logger()

        self.rl_trainer.global_steps = 0

        if hasattr(self.rl_trainer, "load_checkpoint"):
            self.rl_trainer.load_checkpoint()

        total_training_steps = (
            len(self.train_dataloader)
            * self.config.trainer.get("total_epochs", 1)
        )

        progress_bar = tqdm(
            total=total_training_steps,
            initial=self.rl_trainer.global_steps,
            desc="Training Progress",
        )

        if self.config.trainer.get("val_before_train", False):
            logger.info("Validate before training.")
            self.validate()

        consecutive_zero_reward_steps = 0

        total_epochs = self.config.trainer.get("total_epochs", 1)
        for epoch in range(total_epochs):
            for batch_dict in self.train_dataloader:
                try:
                    logger.info(
                        "Training Started at step %d.",
                        self.rl_trainer.global_steps,
                    )

                    origin_batch = self.rl_trainer.get_rl_format_data(batch_dict)

                    progress_bar.update(1)
                    self.rl_trainer.global_steps += 1

                    is_last_step = (
                        self.rl_trainer.global_steps >= total_training_steps
                    )

                    server_addresses = self.rl_trainer.wake_up_rollout()
                    self.update_backends(server_addresses)

                    device = batch_dict["fake_ids"].device

                    assembled_batch, non_tensor_dict = (
                        self.training_coordinator.run_demon_loop_sync(
                            rl_data=batch_dict,
                            device=device,
                            step=self.rl_trainer.global_steps,
                        )
                    )

                    batch = self.rl_trainer.get_rl_format_data(assembled_batch)
                    batch.non_tensor_batch.update(non_tensor_dict)

                    self.rl_trainer.sleep_rollout()

                    metrics = self.rl_trainer.train_step(origin_batch, batch)

                    # Validation
                    test_freq = self.config.trainer.get("test_freq", 0)
                    if test_freq > 0 and (
                        is_last_step
                        or self.rl_trainer.global_steps % test_freq == 0
                    ):
                        self.validate()

                    # Checkpointing
                    save_freq = self.config.trainer.get("save_freq", 0)
                    if save_freq > 0 and (
                        is_last_step
                        or self.rl_trainer.global_steps % save_freq == 0
                    ):
                        self.rl_trainer.save_checkpoint()

                    # -- Rollout stats logging ------------------------------
                    rewards_by_uid = self.training_coordinator.rewards_by_uid
                    all_globals = []
                    for entries in rewards_by_uid.values():
                        for entry in entries:
                            if entry["global"] is not None:
                                all_globals.append(entry["global"])
                    avg_reward = (
                        float(np.mean(all_globals)) if all_globals else 0.0
                    )
                    n_training_samples = getattr(
                        self.training_coordinator,
                        "last_training_sample_count",
                        len(all_globals),
                    ) or len(all_globals)

                    for uid, entries in rewards_by_uid.items():
                        rollout_strs = []
                        for entry in entries:
                            rollout_strs.append(
                                f"global={entry['global']}, per_turn={entry['per_turn']}"
                            )
                        logger.info(
                            "Step %d uid=%s  rollouts:\n  %s",
                            self.rl_trainer.global_steps,
                            uid,
                            "\n  ".join(rollout_strs),
                        )
                    logger.info(
                        "Step %d reward_mean=%.4f  n_uids=%d  n_rollouts=%d  n_training_samples=%d",
                        self.rl_trainer.global_steps,
                        avg_reward,
                        len(rewards_by_uid),
                        len(all_globals),
                        n_training_samples,
                    )

                    # Rollout stats (total_rollouts = training samples in current step, post-split granularity)
                    rollout_stats = {}
                    if all_globals:
                        rollout_stats = {
                            "rollout/reward_mean": float(np.mean(all_globals)),
                            "rollout/reward_std": float(np.std(all_globals)),
                            "rollout/reward_max": float(max(all_globals)),
                            "rollout/reward_min": float(min(all_globals)),
                            "rollout/total_rollouts": n_training_samples,
                            "rollout/unique_prompts": len(rewards_by_uid),
                        }

                    if self._metrics_tracker:
                        self._metrics_tracker.log_rollout_stats(
                            step=self.rl_trainer.global_steps,
                            rewards_by_uid=rewards_by_uid,
                            total_training_samples=n_training_samples,
                        )
                        self._metrics_tracker.log_reward_distribution(
                            step=self.rl_trainer.global_steps,
                            rewards=all_globals,
                        )

                    # Reward collapse warning
                    if avg_reward <= 0:
                        consecutive_zero_reward_steps += 1
                    else:
                        consecutive_zero_reward_steps = 0

                    if consecutive_zero_reward_steps >= 3:
                        logger.warning(
                            "*** REWARD COLLAPSE WARNING *** "
                            "avg_reward <= 0 for %d consecutive steps (step %d). "
                            "The model may be degenerating. "
                            "avg_turns=%.2f, reward_mean=%.4f",
                            consecutive_zero_reward_steps,
                            self.rl_trainer.global_steps,
                            self.training_coordinator.last_avg_turn_count,
                            avg_reward,
                        )

                    # -- Enrich metrics with training context & rollout stats
                    metrics.update({
                        "training/global_step": self.rl_trainer.global_steps,
                        "training/epoch": epoch,
                        "training/avg_conversation_turns": self.training_coordinator.last_avg_turn_count,
                        "training/rollout_reward_mean": avg_reward,
                        "training/consecutive_zero_reward_steps": consecutive_zero_reward_steps,
                    })
                    metrics.update(rollout_stats)

                    # -- Training step metrics logging ----------------------
                    if self._metrics_tracker:
                        self._metrics_tracker.log_training_step(
                            TrainingStepMetrics(
                                step=self.rl_trainer.global_steps,
                                epoch=epoch,
                                verl_metrics=metrics,
                                avg_turns=self.training_coordinator.last_avg_turn_count,
                                reward_mean=avg_reward,
                                consecutive_zero_reward_steps=consecutive_zero_reward_steps,
                            )
                        )
                    else:
                        if hasattr(self.rl_trainer, "log_metrics"):
                            self.rl_trainer.log_metrics(
                                metrics, self.rl_trainer.global_steps
                            )

                    # -- Persistence ----------------------------------------
                    if self._persistence:
                        try:
                            asyncio.run(
                                self._persistence.save_step_summary(
                                    self.rl_trainer.global_steps, metrics
                                )
                            )
                        except Exception as e:
                            logger.warning(
                                "Failed to persist step summary (step %d): %s",
                                self.rl_trainer.global_steps,
                                e,
                            )

                    if is_last_step:
                        progress_bar.close()
                        if self._metrics_tracker:
                            self._metrics_tracker.finish()
                        logger.info(
                            "Training finished at step %d.",
                            self.rl_trainer.global_steps,
                        )
                        return

                except (IndexError, ValueError) as e:
                    logger.warning(
                        "Empty or invalid batch at step %d, skipping this step: %s\n%s",
                        self.rl_trainer.global_steps,
                        e,
                        traceback.format_exc(),
                    )

                except Exception as e:
                    logger.error(
                        "Unexpected exception, save checkpoint and exit: %s", e
                    )
                    self.rl_trainer.save_checkpoint()
                    raise
