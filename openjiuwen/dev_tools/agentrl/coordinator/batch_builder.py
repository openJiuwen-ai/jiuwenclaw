# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""
RLBatchBuilder
--------------

Utility class for converting RolloutWithReward objects into Verl-compatible
TensorDict batches with proper padding, attention masks, and token-level scores.
"""

from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import torch
from tensordict import TensorDict

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import logger
from openjiuwen.dev_tools.agentrl.coordinator.schemas import RolloutWithReward


class RLBatchBuilder:
    """
    Converts rollout sequences into padded token IDs, attention masks, rewards,
    and metadata components required for RL batch construction.

    Provides utilities for:
    - Left/right padding of prompts and responses
    - Token-level reward score construction
    - Component generation from RolloutWithReward objects
    - Assembly of full tensor batches for Verl training
    """

    def __init__(
        self,
        max_prompt_length: int,
        pad_token_id: int,
        max_response_length: int,
    ) -> None:
        self.max_prompt_length = max_prompt_length
        self.pad_token_id = pad_token_id
        self.max_response_length = max_response_length

    @staticmethod
    def get_left_padded_ids_and_attention_mask(
        ids: List[int], max_length: int, pad_token_id: int
    ) -> Tuple[List[int], List[int]]:
        """Left-pads the input ID sequence to a fixed length."""
        seq_len = len(ids)

        if seq_len >= max_length:
            trimmed = ids[-max_length:]
            attention_mask = [1] * max_length
            return trimmed, attention_mask

        pad_len = max_length - seq_len
        padded_ids = [pad_token_id] * pad_len + ids
        attention_mask = [0] * pad_len + [1] * seq_len
        return padded_ids, attention_mask

    @staticmethod
    def get_right_padded_ids_and_attention_mask(
        ids: List[int], max_length: int, pad_token_id: int
    ) -> Tuple[List[int], List[int]]:
        """Right-pads the input ID sequence to a fixed length."""
        seq_len = len(ids)

        if seq_len >= max_length:
            trimmed = ids[:max_length]
            attention_mask = [1] * max_length
            return trimmed, attention_mask

        pad_len = max_length - seq_len
        padded_ids = ids + [pad_token_id] * pad_len
        attention_mask = [1] * seq_len + [0] * pad_len
        return padded_ids, attention_mask

    @staticmethod
    def create_token_level_scores(
        attention_mask: torch.Tensor,
        position_ids: torch.Tensor,
        scores: torch.Tensor,
        response_length: int,
    ) -> torch.Tensor:
        """
        Generates token-level reward scores by assigning each transition's
        reward to its final valid token position.
        """
        n_transition = attention_mask.size(0)
        token_scores = torch.zeros_like(attention_mask, dtype=scores.dtype)

        eos_positions = torch.argmax(position_ids * attention_mask, dim=-1)
        token_scores[torch.arange(n_transition), eos_positions] = scores

        return token_scores[:, -response_length:]

    @classmethod
    def _init_components(cls) -> Dict[str, List[Any]]:
        """Initialize component containers."""
        return {
            "input_ids": [],
            "input_attention_mask": [],
            "response_ids": [],
            "response_attention_mask": [],
            "rewards": [],
            "turn_indices": [],
            "is_drop": [],
            "data_ids": [],
            "loss_masks": [],
            "n_turns_list": [],
        }

    @classmethod
    def _truncate_prompt_and_response(
        cls,
        prompt_ids: List[int],
        response_ids: List[int],
        max_prompt_length: int,
        max_response_length: int,
    ) -> Tuple[List[int], List[int], bool]:
        """Truncate prompt/response if needed, return is_drop flag."""
        is_drop = len(prompt_ids) > max_prompt_length
        if is_drop:
            prompt_ids = prompt_ids[:max_prompt_length]

        if len(response_ids) > max_response_length:
            response_ids = response_ids[:max_response_length]

        return prompt_ids, response_ids, is_drop

    @classmethod
    def _append_component_item(
        cls,
        components: Dict[str, List[Any]],
        item: Dict[str, Any],
        data_id: Any,
    ) -> None:
        """Append one component item to the components dict."""
        components["input_ids"].append(item["input_ids"])
        components["input_attention_mask"].append(item["input_attention_mask"])
        components["response_ids"].append(item["response_ids"])
        components["response_attention_mask"].append(item["response_attention_mask"])
        components["rewards"].append(item["rewards"])
        components["turn_indices"].append(item["turn_indices"])
        components["is_drop"].append(item["is_drop"])
        components["data_ids"].append(data_id)
        components["loss_masks"].append(item.get("loss_mask"))
        components["n_turns_list"].append(item.get("n_turns", 0))

    def assemble_tensor_batch(
        self, components: Dict[str, List], device: Any
    ) -> TensorDict:
        """
        Assembles padded input, response, masks, and computed token-level
        scores into a structured TensorDict batch for model training.
        """
        if torch is None or TensorDict is None:
            raise build_error(
                StatusCode.AGENT_RL_DEPENDENCY_INIT_FAILED,
                error_msg="torch and tensordict are required for RLBatchBuilder.assemble_tensor_batch",
            )

        n_transition = len(components["input_ids"])

        input_ids = torch.LongTensor(components["input_ids"]).to(device)
        input_mask = torch.LongTensor(components["input_attention_mask"]).to(device)
        response_ids = torch.LongTensor(components["response_ids"]).to(device)
        response_mask = torch.LongTensor(components["response_attention_mask"]).to(device)

        seq_ids = torch.cat([input_ids, response_ids], dim=-1)
        attention_mask = torch.cat([input_mask, response_mask], dim=-1)
        position_ids = torch.clamp(torch.cumsum(attention_mask, dim=-1) - 1, min=0)

        scores = torch.tensor(components["rewards"], dtype=torch.bfloat16).to(device)
        token_scores = self.create_token_level_scores(
            attention_mask, position_ids, scores, response_ids.size(-1)
        )

        # --- [DIAG_S0] Batch assembly: EOS position & reward placement ---
        prompt_len = input_ids.size(-1)
        resp_len = response_ids.size(-1)
        eos_pos = torch.argmax(position_ids * attention_mask, dim=-1)
        resp_attn_sums = response_mask.sum(dim=-1)
        reward_in_resp = token_scores.sum(dim=-1)
        problems = []
        for i in range(min(n_transition, 4)):
            eos_in_resp = eos_pos[i].item() >= prompt_len
            resp_active = resp_attn_sums[i].item()
            reward_sum = reward_in_resp[i].item()
            original_reward = scores[i].item()
            reward_ok = abs(reward_sum - original_reward) < 1e-3
            logger.info(
                "[DIAG_S0] sample %d: eos_abs_pos=%d  prompt_len=%d  "
                "eos_in_response=%s  resp_active_tokens=%d/%d  "
                "reward_in_scores=%.4f  original_reward=%.4f  match=%s",
                i, eos_pos[i].item(), prompt_len,
                eos_in_resp, resp_active, resp_len,
                reward_sum, original_reward, reward_ok,
            )
            if not eos_in_resp:
                problems.append(
                    f"sample {i}: eos_pos={eos_pos[i].item()} < prompt_len={prompt_len}, "
                    "reward in PROMPT part"
                )
            if not reward_ok:
                problems.append(
                    f"sample {i}: reward mismatch {reward_sum:.4f} != {original_reward:.4f}"
                )
        if problems:
            logger.warning("[DIAG_S0] PROBLEMS FOUND:\n  %s", "\n  ".join(problems))
        else:
            logger.info("[DIAG_S0] OK: all %d checked samples have correct EOS & reward placement",
                        min(n_transition, 4))

        td_dict = {
            "prompts": input_ids,
            "responses": response_ids,
            "input_ids": seq_ids,
            "attention_mask": attention_mask,
            "position_ids": position_ids,
            "is_drop_mask": torch.BoolTensor(components["is_drop"]).to(device),
            "token_level_scores": token_scores.contiguous(),
        }

        # whole-trajectory: embed per-token loss_mask;
        # verl_executor intersects with response_mask to exclude env tokens from gradient.
        has_loss_masks = (
            components.get("loss_masks")
            and any(m is not None for m in components["loss_masks"])
        )
        if has_loss_masks:
            # No loss_mask (per-turn mode): fill with all 1s
            filled = []
            resp_len = response_ids.size(-1)
            for m in components["loss_masks"]:
                filled.append(m if m is not None else [1] * resp_len)
            td_dict["actor_loss_mask"] = (
                torch.LongTensor(filled).to(device)
            )

        return TensorDict(td_dict, batch_size=n_transition)

    def generate_rl_batch(
        self, rollout_dict: Dict[str, List[RolloutWithReward]], device: Any
    ) -> Tuple[TensorDict, Dict[str, Any]]:
        """
        Builds the full RL training batch by generating components, assembling
        tensor structures, and returning both tensor and non-tensor metadata.
        """
        components = self.generate_components(
            rollout_dict, self.max_prompt_length, self.max_response_length
        )
        if len(components["input_ids"]) == 0:
            logger.warning(
                "generate_rl_batch: 0 samples collected after rollout, "
                "skipping tensor assembly to avoid empty-tensor crash."
            )
            raise build_error(
                StatusCode.AGENT_RL_ROLLOUT_BATCH_EXECUTION_ERROR,
                error_msg="0 samples collected after rollout",
            )
        assembled_batch = self.assemble_tensor_batch(components, device)
        # verl's DataProto.chunk() asserts that non_tensor_batch values are
        # np.ndarray, so we convert plain Python lists here.
        non_tensor_dict = {
            "data_id_list": np.array(components["data_ids"], dtype=object),
            "turn_index_list": np.array(components["turn_indices"], dtype=np.int64),
            "n_turns_list": np.array(components["n_turns_list"], dtype=np.int64),
        }
        return assembled_batch, non_tensor_dict

    def generate_components(
        self,
        rollout_dict: Dict[str, List[RolloutWithReward]],
        max_prompt_length: int,
        max_response_length: int,
    ) -> Dict[str, List[Any]]:
        """
        Converts rollout sequences into padded token IDs, attention masks,
        rewards, and metadata components required for RL batch construction.
        """
        components = self._init_components()
        truncation_count = 0

        for task_id, rollout_list in rollout_dict.items():
            truncation_count += self._process_rollout_list(
                components=components,
                data_id=task_id,
                rollout_list=rollout_list,
                max_prompt_length=max_prompt_length,
                max_response_length=max_response_length,
            )

        components["truncation_count"] = truncation_count
        logger.info(
            f"Processed {len(components['input_ids'])} samples, truncated {truncation_count}"
        )
        return components

    def _process_rollout_list(
        self,
        components: Dict[str, List[Any]],
        data_id: Any,
        rollout_list: List[RolloutWithReward],
        max_prompt_length: int,
        max_response_length: int,
    ) -> int:
        """Process a list of rollouts for one task.

        All turns share origin_task_id as GRPO group key. This lets GRPO contrast
        "used tool -> correct" vs "no tool -> wrong" across turns. Splitting by
        turn would zero out advantages when all rollouts have identical rewards.
        """
        truncation_count = 0
        for rollout in rollout_list:
            item, truncated = self._build_one_component_item(
                rollout=rollout,
                max_prompt_length=max_prompt_length,
                max_response_length=max_response_length,
            )
            self._append_component_item(components, item, data_id=data_id)
            if truncated:
                truncation_count += 1
        return truncation_count

    def _build_one_component_item(
        self,
        rollout: RolloutWithReward,
        max_prompt_length: int,
        max_response_length: int,
    ) -> Tuple[Dict[str, Any], bool]:
        """Build one component item from a RolloutWithReward."""
        prompt_ids, response_ids, is_drop = self._truncate_prompt_and_response(
            prompt_ids=rollout.input_prompt_ids,
            response_ids=rollout.output_response_ids,
            max_prompt_length=max_prompt_length,
            max_response_length=max_response_length,
        )

        padded_prompt, prompt_mask = self.get_left_padded_ids_and_attention_mask(
            prompt_ids, max_prompt_length, self.pad_token_id
        )
        padded_response, response_mask = self.get_right_padded_ids_and_attention_mask(
            response_ids, max_response_length, self.pad_token_id
        )

        # Handle loss_mask (whole-trajectory mode)
        padded_loss_mask: Optional[List[int]] = None
        if rollout.loss_mask is not None:
            raw_mask = rollout.loss_mask
            # Truncate to match response_ids
            if len(raw_mask) > max_response_length:
                raw_mask = raw_mask[:max_response_length]
            # Right-pad to max_response_length (padding positions masked as 0)
            pad_len = max_response_length - len(raw_mask)
            padded_loss_mask = raw_mask + [0] * pad_len

        item = {
            "input_ids": padded_prompt,
            "input_attention_mask": prompt_mask,
            "response_ids": padded_response,
            "response_attention_mask": response_mask,
            "rewards": rollout.reward if rollout.reward is not None else 0.0,
            "turn_indices": rollout.turn_id if rollout.turn_id is not None else 0,
            "is_drop": is_drop,
            "loss_mask": padded_loss_mask,
            "n_turns": rollout.n_turns if rollout.n_turns is not None else 0,
        }
        truncated = len(rollout.output_response_ids) > max_response_length
        return item, truncated
