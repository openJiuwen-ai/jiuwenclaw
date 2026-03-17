# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
LLMCall domain optimizer base class: fixes domain=llm, default_targets=[system_prompt, user_prompt],
unifies filtering and logging semantics. Subclasses implement _backward / _update for prompt optimization.
"""

from typing import Dict, List, TYPE_CHECKING

from openjiuwen.core.foundation.prompt import PromptTemplate
from openjiuwen.agent_evolving.optimizer.base import BaseOptimizer

if TYPE_CHECKING:
    from openjiuwen.core.operator.base import Operator


class LLMCallOptimizerBase(BaseOptimizer):
    """
    Base class for LLMCall dimension optimizers: only optimizes Operators exposing system_prompt/user_prompt.
    """

    domain: str = "llm"

    def default_targets(self) -> List[str]:
        return ["system_prompt", "user_prompt"]

    def filter_operators(self, operators: Dict[str, "Operator"], targets: List[str]) -> Dict[str, "Operator"]:
        """Filter Operators exposing prompt-type tunables; logs warning for missing targets."""
        return super().filter_operators(operators, targets)

    def _is_target_frozen(self, op: "Operator", target: str) -> bool:
        """Check if target is frozen based on get_tunables."""
        return target not in op.get_tunables()

    def _get_prompt_template(self, op: "Operator", target: str) -> PromptTemplate:
        """Get PromptTemplate for target from operator.get_state()."""
        state = op.get_state()
        content = state.get(target, "")
        return PromptTemplate(content=content)
