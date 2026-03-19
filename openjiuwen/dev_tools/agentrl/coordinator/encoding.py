# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""
RolloutEncoder — encodes RolloutMessage into token-level training samples.

Uses ``tokenizer.apply_chat_template`` for accurate prompt/response splitting.
"""

import random
from typing import List

from openjiuwen.core.common.logging import logger
from openjiuwen.dev_tools.agentrl.coordinator.schemas import (
    RolloutMessage,
    RolloutWithReward,
)


class RolloutEncoder:
    """
    Encodes RolloutMessage objects into RolloutWithReward training samples.

    Supports two modes:
    - **per-turn**: each dialogue turn becomes a separate training sample
    - **whole-trajectory**: the entire multi-turn conversation is one sample
      with a loss_mask marking model-generated vs environment tokens
    """

    def __init__(self, tokenizer) -> None:
        self.tokenizer = tokenizer

    # -- per-turn mode ------------------------------------------------------

    def build(self, rolloutmsg: RolloutMessage) -> List[RolloutWithReward]:
        """Build rollout training samples (per-turn mode).

        All turns receive the same global_reward to ensure GRPO sees
        proper reward variance across rollouts rather than artificial
        variance across turns within the same rollout.
        """
        total_turns = len(rolloutmsg.rollout_info)
        should_log = random.random() < 0.05
        ground_truth = ""
        if rolloutmsg.rollout_info:
            ground_truth = (rolloutmsg.rollout_info[0].input_prompt or {}).get(
                "ground_truth", ""
            )

        global_reward = rolloutmsg.global_reward
        if global_reward is None:
            global_reward = (
                rolloutmsg.reward_list[-1]
                if rolloutmsg.reward_list
                else 0.0
            )

        if should_log or random.random() < 0.1:
            logger.info(
                "[DIAG_DATA] build: rollout_id=%s  total_turns=%d  "
                "global_reward=%.4f  reward_list=%s  "
                "origin_task_id=%s",
                rolloutmsg.rollout_id,
                total_turns,
                global_reward,
                [round(r, 4) for r in (rolloutmsg.reward_list or [])],
                rolloutmsg.origin_task_id,
            )

        res_list = []
        for i, rollout in enumerate(rolloutmsg.rollout_info):
            try:
                res_list.append(
                    self._build_single_turn(
                        rollout,
                        i,
                        global_reward,
                        rolloutmsg.origin_task_id,
                        rolloutmsg.rollout_id,
                        should_log=should_log,
                        total_turns=total_turns,
                        ground_truth=ground_truth,
                    )
                )
            except Exception as e:
                logger.warning(
                    "Error in apply_chat_template: %s", e
                )
                logger.warning(
                    "The rolloutmsg is %s", rolloutmsg.model_dump_json()
                )
                return []
        return res_list

    def _build_single_turn(
        self, rollout, turn_id, reward, task_id, rollout_id,
        *, should_log=False, total_turns=1, ground_truth=""
    ) -> RolloutWithReward:
        """Build a single input-output rollout sample."""
        input_messages = rollout.input_prompt["message"]
        output_messages = [rollout.output_response]
        full_messages = input_messages + output_messages
        tools_info = rollout.input_prompt.get("tools", [])

        full_text = self.tokenizer.apply_chat_template(
            full_messages,
            tokenize=False,
            add_generation_prompt=False,
            tools=tools_info or None,
        )
        prompt_text = self.tokenizer.apply_chat_template(
            input_messages,
            tokenize=False,
            add_generation_prompt=True,
            tools=tools_info or None,
        )
        output_text = full_text[len(prompt_text):]

        input_prompt_ids = self.tokenizer.encode(
            prompt_text, add_special_tokens=False
        )
        output_response_ids = self.tokenizer.encode(
            output_text, add_special_tokens=False
        )

        if should_log:
            logger.info(
                "[turn %d/%d] prompt_len=%d  output_len=%d  reward=%.2f"
                "\nground_truth:\n%s\nprompt:\n%s\nresponse:\n%s",
                turn_id + 1, total_turns,
                len(input_prompt_ids), len(output_response_ids),
                reward if reward is not None else 0.0,
                ground_truth or "(N/A)",
                prompt_text,
                output_text,
            )
        return RolloutWithReward(
            turn_id=turn_id,
            task_id=task_id,
            rollout_id=rollout_id,
            input_prompt_ids=input_prompt_ids,
            output_response_ids=output_response_ids,
            reward=reward,
            n_turns=total_turns,
        )

    # -- whole-trajectory mode ----------------------------------------------

    def build_whole_trajectory(
        self, rolloutmsg: RolloutMessage
    ) -> List[RolloutWithReward]:
        """Build a single whole-trajectory training sample from all turns.

        Concatenates the full multi-turn conversation into one sample:
        - prompt  = initial [system, user]
        - response = the rest (assistant + tool_response + assistant + ...)
        - loss_mask: 1 on model-generated tokens, 0 on environment tokens
        """
        if not rolloutmsg.rollout_info:
            return []

        if len(rolloutmsg.rollout_info) == 1:
            return self.build(rolloutmsg)

        try:
            return [self._build_whole_trajectory_impl(rolloutmsg)]
        except Exception as e:
            logger.warning("Error in build_whole_trajectory: %s", e)
            logger.warning(
                "Falling back to per-turn build for rollout %s",
                rolloutmsg.rollout_id,
            )
            return self.build(rolloutmsg)

    def _build_whole_trajectory_impl(
        self, rolloutmsg: RolloutMessage
    ) -> RolloutWithReward:
        """Internal implementation of whole-trajectory sample construction."""
        last_turn = rolloutmsg.rollout_info[-1]
        all_messages = last_turn.input_prompt["message"] + [
            last_turn.output_response
        ]
        tools_info = rolloutmsg.rollout_info[0].input_prompt.get("tools", [])

        initial_messages = rolloutmsg.rollout_info[0].input_prompt["message"]
        prompt_text = self.tokenizer.apply_chat_template(
            initial_messages,
            tokenize=False,
            add_generation_prompt=True,
            tools=tools_info or None,
        )

        full_text = self.tokenizer.apply_chat_template(
            all_messages,
            tokenize=False,
            add_generation_prompt=False,
            tools=tools_info or None,
        )
        response_text = full_text[len(prompt_text):]

        prompt_ids = self.tokenizer.encode(
            prompt_text, add_special_tokens=False
        )
        response_ids = self.tokenizer.encode(
            response_text, add_special_tokens=False
        )
        n_prompt = len(prompt_ids)

        # Compute per-token loss_mask: 1 for model-generated, 0 for environment
        loss_mask = [0] * len(response_ids)

        for rollout in rolloutmsg.rollout_info:
            msgs_before = rollout.input_prompt["message"]
            text_before = self.tokenizer.apply_chat_template(
                msgs_before,
                tokenize=False,
                add_generation_prompt=True,
                tools=tools_info or None,
            )
            n_before = len(
                self.tokenizer.encode(text_before, add_special_tokens=False)
            )

            msgs_after = msgs_before + [rollout.output_response]
            text_after = self.tokenizer.apply_chat_template(
                msgs_after,
                tokenize=False,
                add_generation_prompt=False,
                tools=tools_info or None,
            )
            n_after = len(
                self.tokenizer.encode(text_after, add_special_tokens=False)
            )

            start = n_before - n_prompt
            end = n_after - n_prompt
            for i in range(max(0, start), min(len(loss_mask), end)):
                loss_mask[i] = 1

        reward = rolloutmsg.global_reward
        if reward is None:
            reward = (
                rolloutmsg.reward_list[-1]
                if rolloutmsg.reward_list
                else 0.0
            )

        ground_truth = (rolloutmsg.rollout_info[0].input_prompt or {}).get(
            "ground_truth", ""
        )
        should_log = random.random() < 0.05
        if should_log:
            mask_sum = sum(loss_mask)
            logger.info(
                "[whole-traj %d turns] prompt_len=%d  resp_len=%d  "
                " model_tokens=%d/%d  reward=%.2f"
                "\nground_truth:\n%s\nprompt:\n%s\nresponse:\n%s",
                len(rolloutmsg.rollout_info),
                len(prompt_ids), len(response_ids),
                mask_sum, len(loss_mask),
                float(reward),
                ground_truth or "(N/A)",
                prompt_text,
                response_text,
            )

        return RolloutWithReward(
            turn_id=0,
            task_id=rolloutmsg.origin_task_id,
            rollout_id=rolloutmsg.rollout_id,
            input_prompt_ids=prompt_ids,
            output_response_ids=response_ids,
            reward=float(reward),
            loss_mask=loss_mask,
            n_turns=len(rolloutmsg.rollout_info),
        )

