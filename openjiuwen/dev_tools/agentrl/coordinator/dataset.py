# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""
AgentDataset
------------

Thin subclass of Verl's RLHFDataset for loading parquet training data.

- Disables ``filter_overlong_prompts`` (agent mode handles truncation itself)
- Adds ``index`` and ``fake_ids`` fields to each row (required by Verl DataProto)

verl is an **optional** dependency -- if not installed the module exposes
``AgentDataset = None`` and ``collate_fn = None``.
"""

from typing import Any, Callable

import torch
from verl.utils.dataset.rl_dataset import RLHFDataset
from verl.utils.dataset.rl_dataset import collate_fn as _verl_collate_fn


class AgentDataset(RLHFDataset):
    """RLHFDataset variant tailored for agent-RL training."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.filter_overlong_prompts = False

    def __getitem__(self, item: int) -> dict:
        """Return a single training sample with index and fake_ids for Verl DataProto."""
        row_dict: dict = self.dataframe[item]
        index = row_dict.get("extra_info", {}).get("index", 0)
        row_dict["index"] = index
        row_dict["fake_ids"] = torch.ones(1, dtype=torch.int)
        return row_dict

collate_fn: Callable = _verl_collate_fn
