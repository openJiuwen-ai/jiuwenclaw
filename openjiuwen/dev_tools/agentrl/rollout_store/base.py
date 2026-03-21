# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""
RolloutPersistence
------------------

Abstract interface for persisting rollout trajectories
and per-step summaries to local file storage.
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List

from openjiuwen.dev_tools.agentrl.coordinator.schemas import RolloutMessage


class RolloutPersistence(ABC):
    """Abstract rollout persistence interface."""

    @abstractmethod
    async def save_rollout(
        self, step: int, task_id: str, rollout: RolloutMessage,
        *, phase: str = "train",
    ) -> None:
        """Persist a single rollout with its complete trajectory.

        Args:
            step: Current training step.
            task_id: Task identifier.
            rollout: The rollout message to persist.
            phase: ``"train"`` or ``"val"`` -- determines the output sub-directory.
        """



    @abstractmethod
    async def save_step_summary(self, step: int, metrics: Dict[str, Any]) -> None:
        """Persist per-step training summary metrics."""

    @abstractmethod
    async def query_rollouts(
        self, filters: Dict[str, Any], limit: int = 100
    ) -> List[Dict[str, Any]]:
        """Query historical rollouts by filters (for analysis/debugging)."""

    @abstractmethod
    async def close(self) -> None:
        """Release connections and clean up resources."""
