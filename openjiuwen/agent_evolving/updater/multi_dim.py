# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from __future__ import annotations

from abc import abstractmethod
from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from openjiuwen.agent_evolving.trajectory.types import Trajectory, Updates
    from openjiuwen.core.operator.base import Operator


class MultiDimUpdater:
    """
    Multi-dimensional update updater: Internally handles "attribution/allocation"
    (distributes bad case signals to multiple operators), then runs corresponding
    dimension optimizer for attributed operators, merges Updates applied uniformly
    by Trainer.

    **Consistency (user-facing stable interface, recommended)**
    - Dimensions only divided by Operator domain: `llm` / `tool` / `memory`
      (correspond to LLMCall/ToolCallOperator/MemoryCallOperator).
    - Users only need to configure `domain_optimizers: Dict[domain, optimizer]`
      (compatible parameter name `optimizers`); each domain allows only one
      optimizer. A workflow may have multiple LLMCall/ToolCall/MemoryCall, but
      all operators in same domain are managed by same optimizer to avoid
      conflicts.
    - Attribution/allocation algorithm is internal to MultiDimUpdater
      (replaceable in future); users don't need to provide selector.
    """

    def __init__(
        self,
        *,
        domain_optimizers: Optional[Dict[str, Any]] = None,
    ) -> None:
        self._domain_optimizers: Dict[str, Any] = domain_optimizers or {}

    @abstractmethod
    def bind(self, operators: Dict[str, "Operator"], targets: List[str] | None = None, **config: Any) -> int:
        ...

    def requires_forward_data(self) -> bool:
        """Check if any domain optimizer requires forward data.
        
        Default: returns True if any optimizer in domain_optimizers needs forward data.
        Subclass may override for custom logic.
        """
        for opt in self._domain_optimizers.values():
            requires = getattr(opt, "requires_forward_data", None)
            if callable(requires) and requires():
                return True
        return False

    @abstractmethod
    def update(
        self,
        trajectories: List["Trajectory"],
        evaluated_cases: List[Any],
        config: Dict[str, Any],
    ) -> "Updates":
        ...

    @abstractmethod
    def get_state(self) -> Dict[str, Any]:
        ...

    @abstractmethod
    def load_state(self, state: Dict[str, Any]) -> None:
        ...
