"""Drift judge interface.

Semantic drift protection approximates a KL constraint with an LLM judge: given
the original objective and a candidate prompt, return a deviation score in
``[0, 1]`` (0 = faithful, 1 = the prompt has repurposed the task). The optimizer
subtracts ``drift_penalty * deviation`` from the reward.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from jiuwenswarm.symphony.optimization.models import PromptCandidate


class DriftJudge(ABC):
    @abstractmethod
    async def deviation(self, original_objective: str, candidate: PromptCandidate) -> float:
        """Return semantic deviation of ``candidate`` from ``original_objective`` in [0, 1]."""


class NullDriftJudge(DriftJudge):
    """No-op judge (always 0.0) — useful for tests and drift-insensitive tasks."""

    async def deviation(self, original_objective: str, candidate: PromptCandidate) -> float:
        return 0.0


__all__ = ["DriftJudge", "NullDriftJudge"]
