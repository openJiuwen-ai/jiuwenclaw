# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""
VerlTrainingExecutor
--------------------

Directly extends verl's RayPPOTrainer (no executor ABC) to provide
the full PPO/GRPO training step pipeline including:
- baseline computation (REMAX)
- reward computation
- old log prob / reference log prob / value computation
- advantage estimation
- actor / critic updates
- metrics logging and checkpointing

The ``train_step`` delegates to the shared ``run_ppo_step`` pipeline.
"""

from copy import deepcopy

import torch
from omegaconf import OmegaConf
from tensordict import TensorDict
from verl import DataProto
from verl.protocol import pad_dataproto_to_divisor, unpad_dataproto
from verl.trainer.ppo.core_algos import agg_loss
from verl.trainer.ppo.metric_utils import compute_data_metrics, compute_throughout_metrics
from verl.trainer.ppo.ray_trainer import (
    AdvantageEstimator,
    RayPPOTrainer,
    apply_kl_penalty,
    compute_advantage,
    compute_response_mask,
)
from verl.utils.metric import reduce_metrics
from verl.utils.tracking import Tracking

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import logger
from openjiuwen.dev_tools.agentrl.rl_trainer.ppo_step import run_ppo_step
from openjiuwen.dev_tools.agentrl.monitoring.training_logger import TrainingDiagnostics


class VerlTrainingExecutor(RayPPOTrainer):
    """Verl training executor directly extending RayPPOTrainer."""

    def __init__(
        self,
        config,
        tokenizer,
        processor,
        role_worker_mapping,
        resource_pool_manager,
        ray_worker_group_cls,
        reward_fn,
        val_reward_fn,
        train_dataset,
        val_dataset,
        collate_fn,
        train_sampler,
    ):
        super().__init__(
            config=config,
            tokenizer=tokenizer,
            processor=processor,
            role_worker_mapping=role_worker_mapping,
            resource_pool_manager=resource_pool_manager,
            ray_worker_group_cls=ray_worker_group_cls,
            reward_fn=reward_fn,
            val_reward_fn=val_reward_fn,
            train_dataset=train_dataset,
            val_dataset=val_dataset,
            collate_fn=collate_fn,
            train_sampler=train_sampler,
        )

        self.mini_batch_size = config.actor_rollout_ref.actor.ppo_mini_batch_size
        self.global_steps = 0
        # Auto-set by RayPPOTrainer based on role_worker_mapping; log for confirmation.
        if self.use_reference_policy:
            logger.info("Reference policy ENABLED for KL computation")
        else:
            logger.warning(
                "Reference policy NOT available. KL penalty will be ineffective. "
                "Model may drift far from initial policy during training."
            )
        self.pad_size = None
        self._diagnostics = TrainingDiagnostics(tokenizer)

    # -- rollout lifecycle ---------------------------------------------------

    def sleep_rollout(self):
        """Puts the asynchronous rollout manager into a sleep state."""
        self.async_rollout_manager.sleep()

    def wake_up_rollout(self):
        """Wakes up rollout and returns the active server addresses."""
        self.async_rollout_manager.wake_up()
        return self.async_rollout_manager.server_addresses

    # -- logging / checkpointing --------------------------------------------

    def setup_logger(self):
        """Initializes the experiment logger."""
        self.logger = Tracking(
            project_name=self.config.trainer.project_name,
            experiment_name=self.config.trainer.experiment_name,
            default_backend=self.config.trainer.logger,
            config=OmegaConf.to_container(self.config, resolve=True),
        )
        return self.logger

    def log_metrics(self, metrics, step):
        """Logs the given metrics at the specified global step."""
        if self.logger:
            self.logger.log(data=metrics, step=step)

    def save_checkpoint(self):
        """Saves the current training state."""
        super()._save_checkpoint()

    def load_checkpoint(self):
        """Restores training state from latest checkpoint."""
        super()._load_checkpoint()

    # -- training sub-steps -------------------------------------------------

    def compute_baseline(self, origin_batch, batch):
        """Optionally computes a REMAX-style baseline."""
        if self.config.algorithm.adv_estimator == AdvantageEstimator.REMAX:
            remax_input = deepcopy(origin_batch)
            remax_input.meta_info["do_sample"] = False
            remax_output = self.actor_rollout_wg.generate_sequences(remax_input)
            batch = batch.union(remax_output)
            r_baseline = self.reward_fn(batch)
            r_baseline = r_baseline.sum(dim=-1)
            batch.pop(batch_keys=list(remax_output.batch.keys()))
            batch.batch["reward_baselines"] = r_baseline
            del remax_input, remax_output
        return batch

    def compute_reward(self, batch, metrics):
        """Prepares response masks and optionally computes RM scores."""
        batch.non_tensor_batch["uid"] = batch.non_tensor_batch["data_id_list"]
        resp_mask = compute_response_mask(batch)

        # whole-trajectory: actor_loss_mask marks model tokens (1) vs env tokens (0), intersected with response_mask.
        if "actor_loss_mask" in batch.batch:
            resp_mask = resp_mask * batch.batch["actor_loss_mask"]
            logger.debug(
                "actor_loss_mask applied: %d -> %d active tokens",
                int(compute_response_mask(batch).sum()),
                int(resp_mask.sum()),
            )

        batch.batch["response_mask"] = resp_mask
        batch.meta_info["global_token_num"] = torch.sum(
            batch.batch["attention_mask"], dim=-1
        ).tolist()
        if self.use_rm:
            rm_scores = self.rm_wg.compute_rm_score(batch)
            batch = batch.union(rm_scores)
        return batch

    def compute_old_log_prob(self, batch, metrics):
        """Computes the actor's log probabilities and entropy."""
        batch, unpad_at = pad_dataproto_to_divisor(
            batch, self.actor_rollout_wg.world_size
        )
        self.pad_size = unpad_at
        prior_log_prob = self.actor_rollout_wg.compute_log_prob(batch)
        entropy_mat = prior_log_prob.batch["entropys"]
        resp_mask = batch.batch["response_mask"]

        # whole-trajectory: env tokens have log_prob=-inf, causing NaN in ratio=exp(log_prob - old_log_prob).
        # Clamp masked positions to 0 so ratio=exp(0)=1, eliminating NaN.
        if "actor_loss_mask" in batch.batch:
            inv_mask = (resp_mask == 0).float()
            prior_log_prob.batch["old_log_probs"] = torch.where(
                inv_mask.bool(),
                torch.zeros_like(prior_log_prob.batch["old_log_probs"]),
                prior_log_prob.batch["old_log_probs"],
            )
            entropy_mat = torch.where(
                inv_mask.bool(),
                torch.zeros_like(entropy_mat),
                entropy_mat,
            )

        agg_mode = self.config.actor_rollout_ref.actor.loss_agg_mode
        ent_loss = agg_loss(
            loss_mat=entropy_mat,
            loss_mask=resp_mask,
            loss_agg_mode=agg_mode,
        )
        prior_metrics = {"actor/entropy_loss": ent_loss.detach().item()}
        metrics.update(prior_metrics)

        prior_log_prob.batch.pop("entropys")
        batch = batch.union(prior_log_prob)
        return batch

    def compute_reference_log_prob(self, batch):
        """Computes reference policy log probabilities."""
        if self.use_reference_policy:
            if getattr(self, "ref_in_actor", False):
                ref_probs = self.actor_rollout_wg.compute_ref_log_prob(batch)
            else:
                ref_probs = self.ref_policy_wg.compute_ref_log_prob(batch)
            batch = batch.union(ref_probs)
        return batch

    def compute_values(self, batch):
        """Computes critic value estimates."""
        if self.use_critic:
            v_estimates = self.critic_wg.compute_values(batch)
            batch = batch.union(v_estimates)
        return batch

    def compute_advantages(self, batch, metrics):
        """Unpads batch, optionally applies KL penalty, computes advantages."""
        batch = unpad_dataproto(batch, pad_size=self.pad_size)
        use_kl = (
            hasattr(self.config.algorithm, "use_kl_in_reward")
            and self.config.algorithm.use_kl_in_reward
        )
        if use_kl and "ref_log_prob" not in batch.batch:
            logger.warning(
                "use_kl_in_reward is True but 'ref_log_prob' is missing from batch "
                "(reference policy may not be configured). Falling back to no KL penalty."
            )
            use_kl = False

        if use_kl:
            batch, kl_metrics = apply_kl_penalty(
                batch,
                kl_ctrl=getattr(self, "kl_ctrl_in_reward", None),
                kl_penalty=self.config.algorithm.kl_penalty,
            )
            metrics.update(kl_metrics)
        else:
            batch.batch["token_level_rewards"] = batch.batch["token_level_scores"]

        batch = compute_advantage(
            batch,
            adv_estimator=self.config.algorithm.adv_estimator,
            gamma=self.config.algorithm.gamma,
            lam=self.config.algorithm.lam,
            num_repeat=self.config.actor_rollout_ref.rollout.n,
            norm_adv_by_std_in_grpo=self.config.algorithm.get(
                "norm_adv_by_std_in_grpo", True
            ),
            config=self.config.algorithm,
        )

        # whole-trajectory fallback: identical group rewards -> std=0 -> NaN advantages; clamp to 0.
        if "advantages" in batch.batch:
            adv_tensor = batch.batch["advantages"]
            nan_count = torch.isnan(adv_tensor).sum().item()
            inf_count = torch.isinf(adv_tensor).sum().item()
            if nan_count > 0 or inf_count > 0:
                logger.warning(
                    "Advantages contain %d NaN and %d Inf values, clamping to 0",
                    nan_count, inf_count,
                )
                batch.batch["advantages"] = torch.where(
                    torch.isfinite(adv_tensor), adv_tensor, torch.zeros_like(adv_tensor)
                )
                metrics["training/advantage_nan_count"] = nan_count
                metrics["training/advantage_inf_count"] = inf_count

        return batch

    def filter_effective_groups(self, batch, metrics):
        """Filter GRPO groups: keep only groups that have reward variance AND
        contain at least one positive sample that used tool calling (n_turns >= 2).

        Groups are identified by ``uid`` (non_tensor_batch) so that incomplete
        rollout sets (fewer than rollout_n samples for a prompt) are handled
        correctly.
        """
        if not self.config.algorithm.get("filter_groups", False):
            return batch

        data_ids = batch.non_tensor_batch.get("uid")
        if data_ids is None:
            logger.warning("filter_effective_groups: 'uid' not found, skipping filter")
            return batch

        turn_counts = batch.non_tensor_batch.get("n_turns_list")

        score_mat = batch.batch["token_level_scores"]
        resp_mask = batch.batch["response_mask"]
        per_sample_reward = (score_mat * resp_mask).sum(dim=-1)  # [n_samples]

        id_to_idx: dict = {}
        for idx, did in enumerate(data_ids):
            id_to_idx.setdefault(did, []).append(idx)

        kept_idx = []
        n_groups = len(id_to_idx)
        n_no_variance = 0
        n_singleton = 0
        n_no_positive_with_tool = 0

        for did, idx_list in id_to_idx.items():
            group_rewards = per_sample_reward[idx_list]
            if len(idx_list) < 2:
                n_singleton += 1
                continue
            if group_rewards.std().item() < 1e-6:
                n_no_variance += 1
                continue
            has_positive_with_tool = False
            if turn_counts is not None:
                for i in idx_list:
                    if per_sample_reward[i].item() >= 1.0 - 1e-6 and int(turn_counts[i]) >= 2:
                        has_positive_with_tool = True
                        break
            if not has_positive_with_tool:
                n_no_positive_with_tool += 1
                continue
            kept_idx.extend(idx_list)

        n_kept = n_groups - n_no_variance - n_singleton - n_no_positive_with_tool
        metrics["training/filter_groups_total"] = n_groups
        metrics["training/filter_groups_kept"] = n_kept
        metrics["training/filter_groups_no_variance"] = n_no_variance
        metrics["training/filter_groups_singleton"] = n_singleton
        metrics["training/filter_groups_no_positive_with_tool"] = n_no_positive_with_tool

        if len(kept_idx) < len(batch):
            kept_idx.sort()
            batch = batch[kept_idx]
            logger.info(
                "Group filter: kept %d/%d groups, %d samples "
                "(dropped %d no-variance, %d no-positive-with-tool, %d singleton)",
                n_kept, n_groups, len(kept_idx),
                n_no_variance, n_no_positive_with_tool, n_singleton,
            )
        else:
            logger.info("Group filter: all %d groups effective, none dropped", n_groups)

        return batch

    def update_critic(self, batch, metrics):
        """Updates the critic network."""
        if self.use_critic:
            critic_result = self.critic_wg.update_critic(batch)
            critic_metrics = reduce_metrics(critic_result.meta_info["metrics"])
            metrics.update(critic_metrics)
        return metrics

    def update_actor(self, batch, metrics):
        """Updates the actor policy after critic warmup."""
        if self.config.trainer.critic_warmup <= self.global_steps:
            actor_result = self.actor_rollout_wg.update_actor(batch)
            actor_metrics = reduce_metrics(actor_result.meta_info["metrics"])
            metrics.update(actor_metrics)
            self._diagnostics.diag_after_actor_update(metrics)
        return metrics

    def process_metrics(self, batch, metrics, timing_raw):
        """Augments metrics with data statistics and throughput."""
        metrics.update(
            compute_data_metrics(batch=batch, use_critic=self.use_critic)
        )
        gpu_count = self.resource_pool_manager.get_n_gpus()
        metrics.update(
            compute_throughout_metrics(
                batch=batch, timing_raw=timing_raw, n_gpus=gpu_count
            )
        )
        return metrics

    def balance_batch(self, batch, metrics):
        """Optionally rebalances the batch."""
        if (
            hasattr(self.config.trainer, "balance_batch")
            and self.config.trainer.balance_batch
        ):
            super()._balance_batch(batch, metrics=metrics)

    # -- data format conversion ---------------------------------------------

    def get_rl_format_data(self, batch_dict):
        """Convert batch data to DataProto format for verl training."""
        if isinstance(batch_dict, TensorDict):
            return DataProto(batch=batch_dict)
        elif isinstance(batch_dict, dict):
            return DataProto.from_single_dict(batch_dict)
        else:
            raise build_error(
                StatusCode.AGENT_RL_BATCH_DATA_TYPE_INVALID,
                data_type=str(type(batch_dict)),
            )

    # -- main training step ---------------------------------------------------

    def train_step(self, origin_batch, batch):
        """Main training step delegated to shared PPO pipeline."""
        return run_ppo_step(self, origin_batch, batch)
