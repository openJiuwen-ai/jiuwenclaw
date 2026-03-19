# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""
TrainingDiagnostics
-------------------

Logs batch health information at various stages of the PPO/GRPO
training pipeline for debugging and monitoring.
"""

import torch

from openjiuwen.core.common.logging import logger


class TrainingDiagnostics:
    """Stateless-ish diagnostics helper that logs batch health information.

    Parameters
    ----------
    tokenizer
        HuggingFace tokenizer used for decoding token ids in
        :meth:`diagnose_batch`.
    """

    def __init__(self, tokenizer):
        self.tokenizer = tokenizer

    # ------------------------------------------------------------------
    # diagnose_batch
    # ------------------------------------------------------------------

    def diagnose_batch(self, batch, global_steps: int):
        """Decode and log training batch data for debugging.

        Prints per-group summary and decoded text for a few sampled groups,
        plus global anomaly checks.
        """
        uids = batch.non_tensor_batch.get("uid")
        if uids is None:
            logger.warning("[diagnose] 'uid' not in non_tensor_batch, skipping")
            return

        input_ids = batch.batch["input_ids"]
        attention_mask = batch.batch["attention_mask"]
        scores = batch.batch["token_level_scores"]
        response_mask = batch.batch.get("response_mask")
        loss_mask = batch.batch.get("actor_loss_mask")
        n_turns_arr = batch.non_tensor_batch.get("n_turns_list")

        prompt_len = batch.batch["prompts"].shape[-1]

        uid_to_indices: dict = {}
        for idx, uid in enumerate(uids):
            uid_to_indices.setdefault(uid, []).append(idx)

        # --- global summary ---
        n_groups = len(uid_to_indices)
        n_samples = len(batch)
        n_zero_resp_mask = 0
        n_zero_loss_mask = 0
        n_no_variance = 0
        reward_list_all = []

        for uid, indices in uid_to_indices.items():
            rewards = []
            for i in indices:
                r = (scores[i] * (response_mask[i] if response_mask is not None
                                  else 1)).sum().item()
                rewards.append(r)
                if response_mask is not None and response_mask[i].sum().item() == 0:
                    n_zero_resp_mask += 1
                if loss_mask is not None and loss_mask[i].sum().item() == 0:
                    n_zero_loss_mask += 1
            reward_list_all.append(rewards)
            if len(set(round(r, 6) for r in rewards)) <= 1:
                n_no_variance += 1

        logger.info(
            "[diagnose] step=%d  groups=%d  samples=%d  "
            "zero_resp_mask=%d  zero_loss_mask=%d  no_reward_variance_groups=%d",
            global_steps, n_groups, n_samples,
            n_zero_resp_mask, n_zero_loss_mask, n_no_variance,
        )

        # --- per-group detail (sample up to 2 groups) ---
        sampled_uids = list(uid_to_indices.keys())[:2]
        for uid in sampled_uids:
            indices = uid_to_indices[uid]
            lines = [f"  [group uid={uid[:8]}  size={len(indices)}]"]

            for i in indices:
                ids = input_ids[i]
                attn = attention_mask[i]

                prompt_part = ids[:prompt_len]
                prompt_attn = attn[:prompt_len]
                resp_part = ids[prompt_len:]
                resp_attn = attn[prompt_len:]

                prompt_active = prompt_part[prompt_attn.bool()].tolist()
                resp_active = resp_part[resp_attn.bool()].tolist()

                prompt_text = self.tokenizer.decode(prompt_active, skip_special_tokens=False)
                resp_text = self.tokenizer.decode(resp_active, skip_special_tokens=False)

                r = (scores[i] * (response_mask[i] if response_mask is not None
                                  else 1)).sum().item()
                resp_mask_sum = int(response_mask[i].sum()) if response_mask is not None else -1
                loss_mask_sum = int(loss_mask[i].sum()) if loss_mask is not None else -1
                n_turns = int(n_turns_arr[i]) if n_turns_arr is not None else -1

                lines.append(
                    f"  [sample {i}] reward={r:.4f}  n_turns={n_turns}  "
                    f"prompt_tokens={len(prompt_active)}  resp_tokens={len(resp_active)}  "
                    f"resp_mask={resp_mask_sum}  loss_mask={loss_mask_sum}"
                )
                lines.append(f"    prompt: {prompt_text}")
                lines.append(f"    response: {resp_text}")

            logger.info("[diagnose]\n%s", "\n".join(lines))

    # ------------------------------------------------------------------
    # diag_after_reward
    # ------------------------------------------------------------------

    @staticmethod
    def diag_after_reward(batch):
        """[DIAG_S1] response_mask, token_level_scores, GRPO group rewards."""
        response_mask = batch.batch.get("response_mask")
        token_scores = batch.batch.get("token_level_scores")
        attn_mask = batch.batch.get("attention_mask")
        n = len(batch)

        resp_sums = response_mask.sum(dim=-1).float()
        attn_sums = attn_mask.sum(dim=-1).float()
        score_sums = token_scores.sum(dim=-1).float()
        logger.info(
            "[DIAG_S1] n_samples=%d  resp_mask: mean=%.1f min=%d max=%d  "
            "attn_mask: mean=%.1f  score_sums: min=%.4f max=%.4f  "
            "has_actor_loss_mask=%s",
            n,
            resp_sums.mean().item(), int(resp_sums.min()), int(resp_sums.max()),
            attn_sums.mean().item(),
            score_sums.min().item(), score_sums.max().item(),
            "actor_loss_mask" in batch.batch,
        )

        uids = batch.non_tensor_batch.get("uid")
        if uids is None:
            return
        uid_to_indices: dict = {}
        for idx, uid in enumerate(uids):
            uid_to_indices.setdefault(uid, []).append(idx)

        group_sizes = [len(v) for v in uid_to_indices.values()]
        n_groups = len(uid_to_indices)
        no_var_count = 0
        for uid, indices in uid_to_indices.items():
            rewards = [score_sums[i].item() for i in indices]
            if len(set(round(r, 4) for r in rewards)) <= 1:
                no_var_count += 1

        logger.info(
            "[DIAG_S1] groups=%d  group_sizes=%s  no_reward_variance=%d/%d",
            n_groups,
            sorted(set(group_sizes)),
            no_var_count, n_groups,
        )

        for uid in list(uid_to_indices.keys())[:2]:
            indices = uid_to_indices[uid]
            rewards = [round(score_sums[i].item(), 4) for i in indices]
            resp_masks = [int(resp_sums[i]) for i in indices]
            logger.info(
                "[DIAG_S1] group=%s size=%d  rewards=%s  resp_masks=%s",
                uid[:8], len(indices), rewards, resp_masks,
            )

    # ------------------------------------------------------------------
    # diag_after_old_log_prob
    # ------------------------------------------------------------------

    @staticmethod
    def diag_after_old_log_prob(batch):
        """[DIAG_S2] old_log_probs distribution — detect -inf/NaN early."""
        old_lp = batch.batch.get("old_log_probs")
        response_mask = batch.batch.get("response_mask")
        if old_lp is None:
            logger.warning("[DIAG_S2] old_log_probs not found in batch!")
            return

        masked = old_lp[response_mask.bool()] if response_mask is not None else old_lp.flatten()
        nan_cnt = torch.isnan(masked).sum().item()
        inf_cnt = torch.isinf(masked).sum().item()
        neginf_cnt = (masked == float("-inf")).sum().item()
        finite = masked[torch.isfinite(masked)]

        logger.info(
            "[DIAG_S2] old_log_probs (on resp tokens): "
            "n=%d  nan=%d  inf=%d  neg_inf=%d  "
            "mean=%.4f  std=%.4f  min=%.4f  max=%.4f  "
            "pct_below_m10=%.2f%%",
            masked.numel(), nan_cnt, inf_cnt, neginf_cnt,
            finite.mean().item() if finite.numel() > 0 else 0,
            finite.std().item() if finite.numel() > 1 else 0,
            finite.min().item() if finite.numel() > 0 else 0,
            finite.max().item() if finite.numel() > 0 else 0,
            (finite < -10).float().mean().item() * 100 if finite.numel() > 0 else 0,
        )

        if nan_cnt > 0 or neginf_cnt > 0:
            logger.warning(
                "[DIAG_S2] PROBLEM: old_log_probs has %d NaN and %d -inf on response tokens! "
                "This will cause NaN in PPO ratio.",
                nan_cnt, neginf_cnt,
            )

        for i in range(min(len(batch), 4)):
            mask_i = response_mask[i].bool()
            lp_i = old_lp[i][mask_i]
            logger.info(
                "[DIAG_S2] sample %d: resp_tokens=%d  lp_mean=%.4f  lp_min=%.4f  lp_max=%.4f",
                i, mask_i.sum().item(),
                lp_i.mean().item(), lp_i.min().item(), lp_i.max().item(),
            )

    # ------------------------------------------------------------------
    # diag_after_advantages
    # ------------------------------------------------------------------

    @staticmethod
    def diag_after_advantages(batch):
        """[DIAG_S3] advantage & token_level_rewards stats."""
        adv = batch.batch.get("advantages")
        token_rewards = batch.batch.get("token_level_rewards")
        response_mask = batch.batch.get("response_mask")

        if adv is None:
            logger.warning("[DIAG_S3] no 'advantages' in batch!")
            return

        adv_flat = adv[response_mask.bool()] if response_mask is not None else adv.flatten()
        nan_count = torch.isnan(adv_flat).sum().item()
        inf_count = torch.isinf(adv_flat).sum().item()
        finite = adv_flat[torch.isfinite(adv_flat)]

        logger.info(
            "[DIAG_S3] advantages: n=%d  nan=%d  inf=%d  "
            "mean=%.6f  std=%.6f  min=%.4f  max=%.4f",
            adv_flat.numel(), nan_count, inf_count,
            finite.mean().item() if finite.numel() > 0 else 0,
            finite.std().item() if finite.numel() > 1 else 0,
            finite.min().item() if finite.numel() > 0 else 0,
            finite.max().item() if finite.numel() > 0 else 0,
        )

        if nan_count > 0 or inf_count > 0:
            logger.warning(
                "[DIAG_S3] PROBLEM: advantages has %d NaN and %d Inf!",
                nan_count, inf_count,
            )

        if token_rewards is not None:
            tr_flat = token_rewards[response_mask.bool()] if response_mask is not None else token_rewards.flatten()
            logger.info(
                "[DIAG_S3] token_level_rewards: nonzero=%d/%d  mean=%.6f  min=%.4f  max=%.4f",
                int((tr_flat != 0).sum()), tr_flat.numel(),
                tr_flat.mean().item(), tr_flat.min().item(), tr_flat.max().item(),
            )

        per_sample = []
        for i in range(min(len(batch), 8)):
            mask_i = response_mask[i].bool() if response_mask is not None \
                else torch.ones(adv.size(-1), dtype=torch.bool)
            a = adv[i][mask_i]
            r = token_rewards[i][mask_i].sum().item() if token_rewards is not None else 0
            per_sample.append(f"s{i}(r={r:.2f},adv={a.mean().item():.4f})")
        logger.info("[DIAG_S3] per_sample: %s", "  ".join(per_sample))

    # ------------------------------------------------------------------
    # diag_after_actor_update
    # ------------------------------------------------------------------

    @staticmethod
    def diag_after_actor_update(metrics):
        """[DIAG_S4] actor update metrics — loss, clip fraction, entropy, approx KL."""
        keys_of_interest = [
            "actor/entropy_loss",
            "actor/pg_loss",
            "actor/actor_loss",
            "actor/pg_clipfrac",
            "actor/approx_kl",
            "actor/grad_norm",
        ]
        parts = []
        for k in keys_of_interest:
            v = metrics.get(k)
            if v is not None:
                parts.append(f"{k.split('/')[-1]}={v:.6f}")
        logger.info("[DIAG_S4] actor_update: %s", "  ".join(parts) if parts else "(no actor metrics found)")

        pg_loss = metrics.get("actor/pg_loss")
        clip_frac = metrics.get("actor/pg_clipfrac")
        approx_kl = metrics.get("actor/approx_kl")

        if pg_loss is not None and (abs(pg_loss) > 10.0):
            logger.warning("[DIAG_S4] PROBLEM: pg_loss=%.4f is very large (>10), possible divergence!", pg_loss)
        if clip_frac is not None and clip_frac > 0.5:
            logger.warning("[DIAG_S4] WARNING: clip_frac=%.4f > 0.5, policy changed too much", clip_frac)
        if approx_kl is not None and approx_kl > 0.1:
            logger.warning("[DIAG_S4] WARNING: approx_kl=%.4f > 0.1, large policy shift", approx_kl)
