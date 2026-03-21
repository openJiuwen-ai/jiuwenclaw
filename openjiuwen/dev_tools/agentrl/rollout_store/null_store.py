# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""
NullRolloutStore
----------------

No-op implementation of RolloutPersistence used when persistence is disabled.
All methods silently succeed without performing any I/O.
"""

from typing import Any, Dict, List

from openjiuwen.dev_tools.agentrl.coordinator.schemas import RolloutMessage
from openjiuwen.dev_tools.agentrl.rollout_store.base import RolloutPersistence


class NullRolloutStore(RolloutPersistence):
    """No-op persistence -- all methods are silent no-ops."""

    async def save_rollout(
        self, step: int, task_id: str, rollout: RolloutMessage,
        *, phase: str = "train",
    ) -> None:
        """No-op: no persistence when disabled."""
        pass



    async def save_step_summary(self, step: int, metrics: Dict[str, Any]) -> None:
        """No-op: no persistence when disabled."""
        pass

    async def query_rollouts(
        self, filters: Dict[str, Any], limit: int = 100
    ) -> List[Dict[str, Any]]:
        """Return empty list: no data is stored."""
        return []

    async def close(self) -> None:
        """No-op: no resources to release."""
        pass
