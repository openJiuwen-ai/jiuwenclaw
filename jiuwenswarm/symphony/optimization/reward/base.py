"""Reward interfaces.

A :class:`RewardComponent` maps one execution to a scalar (ideally already in
``[0, 1]``). A :class:`RewardModel` combines components into a comparable
:class:`RewardBreakdown` per candidate — see :mod:`.composite` for the default.
Both are async so LLM-backed components (correctness) share the interface with
cheap deterministic ones (latency, token usage).
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from jiuwenswarm.symphony.optimization.models import (
    Execution,
    RewardBreakdown,
    TaskSpec,
)


class RewardComponent(ABC):
    """One reward metric. Implement :meth:`score` returning a scalar in ``[0, 1]``."""

    name: str = "component"

    @abstractmethod
    async def score(self, execution: Execution, task: TaskSpec) -> float:
        """Return this metric's raw score for ``execution`` (higher is better)."""


class RewardModel(ABC):
    """Turns executions into comparable rewards.

    Batch-oriented so implementations may normalize across the candidates of one
    iteration (which is what prevents a single runaway metric from dominating).
    """

    @abstractmethod
    async def evaluate(
        self,
        executions: list[Execution],
        task: TaskSpec,
        drift_scores: dict[str, float],
    ) -> list[RewardBreakdown]:
        """Score every execution; ``drift_scores`` maps candidate_id -> deviation."""


__all__ = ["RewardComponent", "RewardModel"]
