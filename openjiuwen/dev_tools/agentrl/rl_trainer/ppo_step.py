# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""
PPO Training Step Pipeline
--------------------------

Shared PPO/GRPO training step orchestration. Used by VerlTrainingExecutor
and compatible trainers that implement the executor interface.

Pipeline: baseline -> reward -> old_log_prob -> ref -> values -> adv ->
          filter_groups -> data_alignment -> balance -> critic -> actor -> metrics
"""

import random
from contextlib import contextmanager
from typing import Dict, TYPE_CHECKING

import torch
from codetiming import Timer

from openjiuwen.core.common.logging import logger

if TYPE_CHECKING:
    from .verl_executor import VerlTrainingExecutor


@contextmanager
def timer(name: str, timing_raw: Dict[str, float]):
    """Context manager for timing code execution."""
    with Timer(name=name, logger=None) as t:
        yield
    if name not in timing_raw:
        timing_raw[name] = 0
    timing_raw[name] += t.last


def _run_data_alignment(executor, batch, metrics, timing_raw):
    """Drop long prompts and floor batch to mini_batch_size."""
    if "is_drop_mask" in batch.batch:
        keep_indices = (
            (~batch.batch["is_drop_mask"])
            .nonzero(as_tuple=True)[0]
        )
        metrics["training/n_triplets_prompt_too_long"] = (
            batch.batch["is_drop_mask"].shape[0]
            - keep_indices.shape[0]
        )
        batch = batch[keep_indices]
        logger.info(
            "Dropped %d samples with long prompts",
            metrics["training/n_triplets_prompt_too_long"],
        )
    mini_batch_size = executor.mini_batch_size
    n_transition = len(batch)
    if n_transition % mini_batch_size != 0:
        random_indices = list(range(n_transition))
        random.shuffle(random_indices)
        batch.reorder(
            torch.tensor(random_indices, dtype=torch.int32)
        )
        n_remained = n_transition // mini_batch_size * mini_batch_size
        batch = batch[list(range(n_remained))]
        metrics["training/n_triplets_dropped_remainder"] = (
            n_transition - n_remained
        )
        logger.info(
            "Data alignment: %d -> %d samples",
            n_transition,
            n_remained,
        )
    else:
        metrics["training/n_triplets_dropped_remainder"] = 0
    return batch


def _clamp_nonfinite(batch):
    """Clamp NaN/Inf in key tensors to 0 before model update."""
    for key in ("advantages", "old_log_probs", "token_level_rewards"):
        if key in batch.batch:
            t = batch.batch[key]
            if not torch.isfinite(t).all():
                bad = (~torch.isfinite(t)).sum().item()
                logger.warning(
                    "NaN/Inf detected in '%s' (%d values) before model update, "
                    "clamping to 0 to protect model weights",
                    key, bad,
                )
                batch.batch[key] = torch.where(
                    torch.isfinite(t), t, torch.zeros_like(t)
                )


def run_ppo_step(
    executor: "VerlTrainingExecutor",
    origin_batch,
    batch,
) -> dict:
    """
    Run the full PPO training step.

    Executor must implement: compute_baseline, compute_reward, compute_old_log_prob,
    compute_reference_log_prob, compute_values, compute_advantages, filter_effective_groups,
    balance_batch, update_critic, update_actor, process_metrics. Optional: _diagnostics.
    """
    metrics = {}
    timing_raw = {}

    logger.info("Starting training step...")
    with timer("step", timing_raw):
        with timer("gen_max", timing_raw):
            batch = executor.compute_baseline(origin_batch, batch)
        with timer("reward", timing_raw):
            batch = executor.compute_reward(batch, metrics)

        diagnostics = getattr(executor, "_diagnostics", None)
        if diagnostics:
            diagnostics.diagnose_batch(batch, executor.global_steps)
            diagnostics.diag_after_reward(batch)

        with timer("old_log_prob", timing_raw):
            batch = executor.compute_old_log_prob(batch, metrics)
        if diagnostics:
            diagnostics.diag_after_old_log_prob(batch)

        with timer("ref", timing_raw):
            batch = executor.compute_reference_log_prob(batch)
        with timer("values", timing_raw):
            batch = executor.compute_values(batch)
        with timer("adv", timing_raw):
            batch = executor.compute_advantages(batch, metrics)
        if diagnostics:
            diagnostics.diag_after_advantages(batch)

        with timer("filter_groups", timing_raw):
            batch = executor.filter_effective_groups(batch, metrics)

        logger.info("Before data alignment, Batch size: %d", len(batch))
        with timer("data_alignment", timing_raw):
            batch = _run_data_alignment(executor, batch, metrics, timing_raw)

        if len(batch) == 0:
            logger.warning(
                "Batch is empty after data alignment, skipping this training step"
            )
            metrics["training/skipped_empty_batch"] = 1
            return metrics

        _clamp_nonfinite(batch)

        with timer("balance_batch", timing_raw):
            executor.balance_batch(batch, metrics)
        with timer("update_critic", timing_raw):
            executor.update_critic(batch, metrics)
        with timer("update_actor", timing_raw):
            executor.update_actor(batch, metrics)

    metrics = executor.process_metrics(batch, metrics, timing_raw)
    return metrics
