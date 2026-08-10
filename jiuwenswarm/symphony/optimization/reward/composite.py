"""Composite reward: weighted sum of components with anti-reward-hacking guards.

Guards applied per candidate:
  * **min-correctness gate** — a candidate scoring below the correctness floor cannot
    win by trading quality for latency/tokens; its reward is capped at its correctness.
  * **drift penalty** — semantic deviation from the objective is subtracted.
  * **hidden-set consistency** — if visible correctness far exceeds held-out correctness,
    the candidate is overfitting and is penalized.

Built-in components already return bounded ``[0, 1]`` absolute scores, so their weighted
sum is comparable across iterations (which is what lets reward *improve over time*).
Cross-candidate min-max normalization is therefore opt-in (``normalize=True``) and intended
only for custom, unbounded metrics — enabling it collapses each iteration's best candidate
toward 1.0 and hides cross-iteration progress.
"""

from __future__ import annotations

import asyncio
import logging

from jiuwenswarm.symphony.optimization.models import (
    Execution,
    RewardBreakdown,
    TaskSpec,
)
from jiuwenswarm.symphony.optimization.reward.base import RewardComponent, RewardModel
from jiuwenswarm.symphony.optimization.reward.components import CorrectnessReward
from jiuwenswarm.symphony.optimization.reward.normalize import clamp01, min_max_normalize

LOGGER = logging.getLogger(__name__)

_OVERFIT_MARGIN = 0.25
_OVERFIT_PENALTY = 0.5


class CompositeReward(RewardModel):
    """Weighted combination of :class:`RewardComponent` instances."""

    def __init__(
        self,
        components: list[RewardComponent],
        weights: dict[str, float],
        *,
        min_correctness: float = 0.5,
        drift_penalty: float = 0.5,
        normalize: bool = False,
        correctness_name: str = "correctness",
    ) -> None:
        # Keep only components that carry a positive weight — a zero weight means
        # "disabled" and we skip the (possibly expensive) computation entirely.
        self._components = [c for c in components if weights.get(c.name, 0.0) > 0.0]
        self._weights = dict(weights)
        self._min_correctness = clamp01(min_correctness)
        self._drift_penalty = max(0.0, drift_penalty)
        self._normalize = normalize
        self._correctness_name = correctness_name
        self._correctness = next(
            (c for c in self._components if isinstance(c, CorrectnessReward)), None
        )

    async def evaluate(
        self,
        executions: list[Execution],
        task: TaskSpec,
        drift_scores: dict[str, float],
    ) -> list[RewardBreakdown]:
        if not executions:
            return []

        # 1. raw component scores: raw[name] -> [score per execution]
        raw: dict[str, list[float]] = {}
        for component in self._components:
            raw[component.name] = await asyncio.gather(
                *(component.score(ex, task) for ex in executions)
            )

        # 2. optional per-iteration min-max normalization
        norm = {
            name: (min_max_normalize(values) if self._normalize else list(values))
            for name, values in raw.items()
        }

        # 3. hidden-set correctness for overfitting detection (once per candidate)
        hidden_correctness = await self._hidden_correctness(executions, task)

        total_weight = sum(self._weights.get(c.name, 0.0) for c in self._components) or 1.0

        breakdowns: list[RewardBreakdown] = []
        for idx, ex in enumerate(executions):
            components = {name: norm[name][idx] for name in norm}
            raw_components = {name: raw[name][idx] for name in raw}
            weighted = sum(
                self._weights.get(name, 0.0) * value for name, value in components.items()
            )
            score = weighted / total_weight

            correctness = raw_components.get(self._correctness_name, 1.0)
            notes: list[str] = []

            # min-correctness gate
            gated = False
            if self._correctness is not None and correctness < self._min_correctness:
                gated = True
                score = min(score, correctness)
                notes.append(
                    f"correctness {correctness:.2f} < gate {self._min_correctness:.2f}: reward capped"
                )

            # hidden-set overfitting check
            hid = hidden_correctness.get(ex.candidate.candidate_id)
            if hid is not None and correctness - hid > _OVERFIT_MARGIN:
                gap = correctness - hid
                penalty = _OVERFIT_PENALTY * gap
                score = max(0.0, score - penalty)
                notes.append(
                    f"overfitting: visible {correctness:.2f} vs hidden {hid:.2f} (-{penalty:.2f})"
                )

            # drift penalty
            drift = clamp01(drift_scores.get(ex.candidate.candidate_id, 0.0))
            drift_applied = self._drift_penalty * drift
            if drift_applied > 0:
                score = max(0.0, score - drift_applied)
                notes.append(f"drift {drift:.2f} penalty -{drift_applied:.2f}")

            if ex.error:
                score = 0.0
                notes.append(f"execution error: {ex.error}")

            breakdowns.append(
                RewardBreakdown(
                    score=clamp01(score),
                    components=components,
                    raw_components=raw_components,
                    correctness=correctness,
                    drift=drift,
                    drift_penalty_applied=drift_applied,
                    gated=gated,
                    notes=notes,
                )
            )
        return breakdowns

    async def _hidden_correctness(
        self, executions: list[Execution], task: TaskSpec
    ) -> dict[str, float]:
        if self._correctness is None or not task.hidden_cases:
            return {}
        result: dict[str, float] = {}
        scores = await asyncio.gather(
            *(
                self._correctness.correctness_on(ex.hidden_results, task)
                for ex in executions
            )
        )
        for ex, sc in zip(executions, scores):
            result[ex.candidate.candidate_id] = sc
        return result


__all__ = ["CompositeReward"]
