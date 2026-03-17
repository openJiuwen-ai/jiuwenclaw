# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
Updater protocol definition.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Protocol, Union, TYPE_CHECKING

from openjiuwen.agent_evolving.trajectory.types import Trajectory, Updates

if TYPE_CHECKING:
    from openjiuwen.core.operator.base import Operator


class Updater(Protocol):
    """
    Core convergence point: Unifies "single-dimension optimizer" and
    "multi-dimensional attribution + allocation" into one interface.

    Trainer doesn't care about implementation details, only:
    (trajectories, evaluated_cases) -> Updates or candidate set List[Updates]
    """

    def bind(self, operators: Dict[str, "Operator"], targets: Optional[List[str]] = None, **config: Any) -> int:
        """
        Bind operators and filter optimizable ones. Returns count; 0 triggers
        soft-exit at Trainer (logs error and returns agent directly).
        """
        ...

    def requires_forward_data(self) -> bool:
        """
        Whether this updater needs framework to execute forward on train_cases.

        Returns False for black-box optimizers that generate/execute/evaluate
        internally (e.g., tool_optimizer, data-self-generation).
        """
        ...

    def update(
        self,
        trajectories: List[Trajectory],
        evaluated_cases: List[Any],
        config: Dict[str, Any],
    ) -> Union[Updates, List[Updates]]:
        ...

    def get_state(self) -> Dict[str, Any]:
        ...

    def load_state(self, state: Dict[str, Any]) -> None:
        ...
