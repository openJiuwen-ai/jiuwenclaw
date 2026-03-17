# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
Memory domain optimizer base class: fixes domain=memory, default_targets=[enabled, max_retries],
unifies filtering and logging semantics. Subclasses implement _backward / _update.
"""

from typing import Dict, List, TYPE_CHECKING

from openjiuwen.agent_evolving.optimizer.base import BaseOptimizer

if TYPE_CHECKING:
    from openjiuwen.core.operator.base import Operator


class MemoryOptimizerBase(BaseOptimizer):
    """
    Memory dimension optimizer base class: optimizes tunables exposed by MemoryCallOperator.
    """

    domain: str = "memory"

    def default_targets(self) -> List[str]:
        return ["enabled", "max_retries"]

    def filter_operators(self, operators: Dict[str, "Operator"], targets: List[str]) -> Dict[str, "Operator"]:
        """Filter Operators exposing memory tunables; logs warning for missing targets."""
        return super().filter_operators(operators, targets)
