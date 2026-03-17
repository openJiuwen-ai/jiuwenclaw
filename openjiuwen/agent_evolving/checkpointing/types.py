# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass
class EvolveCheckpoint:
    """
    Training checkpoint (for resume).

    Design principles:
    - Centers on Operator.get_state/load_state, avoids tight coupling with specific Optimizer implementations.
    - Saves training progress and best info, supports interrupted training resumption.
    """

    version: str
    run_id: str
    step: Dict[str, int]                         # epoch/batch/global_step etc.
    best: Dict[str, Any]                         # best_score etc.
    seed: Optional[int]
    operators_state: Dict[str, Dict[str, Any]]   # operator_name/operator_id -> state
    updater_state: Dict[str, Any]               # Unified: update updater internal state (can be empty)
    searcher_state: Dict[str, Any]               # Parameter searcher state (can be empty)
    last_metrics: Dict[str, Any]                 # Recent epoch summary (can be empty)

