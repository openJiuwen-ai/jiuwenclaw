# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""
Unified Pydantic data models for the RL training pipeline.

All modules import data structures from here, eliminating circular
dependencies between the trainer and rollout layers.
"""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel


# ---------------------------------------------------------------------------
# Runtime-side models (trajectory representation)
# ---------------------------------------------------------------------------


class Rollout(BaseModel):
    """
    Single-turn dialogue rollout.

    Format compatible with jiuwen_rl v1:
    - input_prompt["message"]: input message list (OpenAI message format)
    - input_prompt["tools"]:   tool definition list
    - output_response:         LLM output message (content or tool_calls)
    """

    turn_id: Optional[int] = None
    input_prompt: Optional[Dict[str, Any]] = None
    output_response: Optional[Dict[str, Any]] = None
    llm_config: Optional[Dict[str, Any]] = None


class RolloutMessage(BaseModel):
    """
    Complete execution result for a single task, aggregating
    multiple turns and the associated rewards.
    """

    task_id: Optional[str] = None
    origin_task_id: Optional[str] = None
    rollout_id: Optional[str] = None
    start_time: Optional[str] = None
    end_time: Optional[str] = None

    rollout_info: List[Rollout] = []
    reward_list: List[float] = []
    global_reward: Optional[float] = None
    turn_count: int = 0
    round_num: Optional[int] = None


# ---------------------------------------------------------------------------
# Training-side models
# ---------------------------------------------------------------------------


class RLTask(BaseModel):
    """Minimal training task unit."""

    task_id: str
    origin_task_id: str
    task_sample: Dict[str, Any] = {}
    round_num: int = 0


class RolloutWithReward(BaseModel):
    """
    Standard MDP data unit used by the training framework.

    Represents one (input, output, reward) triple at token level
    after tokenisation.
    """

    turn_id: Optional[int] = None
    task_id: Optional[str] = None
    rollout_id: Optional[str] = None

    input_prompt_ids: List[int]
    output_response_ids: List[int]

    reward: Optional[float] = None
    n_turns: Optional[int] = None

    # Per-token loss mask for whole-trajectory mode.
    # 1 = model-generated token (participates in loss),
    # 0 = environment token (excluded from loss).
    loss_mask: Optional[List[int]] = None
