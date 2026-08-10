"""Convergence detection for the optimization loop.

Stops the loop when further iterations are unlikely to help, via any of:
  * no improvement greater than ``threshold`` over the last ``window`` iterations;
  * rolling variance of recent rewards below ``threshold`` (rewards have flattened);
  * a caller-supplied target reward is reached.
Max-iterations is enforced by the loop itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from statistics import pvariance


@dataclass
class ConvergenceState:
    converged: bool = False
    reason: str = ""
    best: float = 0.0
    moving_average: float = 0.0
    variance: float = 0.0


@dataclass
class ConvergenceDetector:
    threshold: float = 0.01
    window: int = 3
    target: float | None = None
    _rewards: list[float] = field(default_factory=list)

    def update(self, reward: float) -> ConvergenceState:
        self._rewards.append(reward)
        rewards = self._rewards
        best = max(rewards)
        recent = rewards[-self.window:]
        moving_average = sum(recent) / len(recent)
        variance = pvariance(recent) if len(recent) > 1 else 0.0

        state = ConvergenceState(
            best=best, moving_average=moving_average, variance=variance
        )

        if self.target is not None and best >= self.target:
            state.converged = True
            state.reason = f"target reward {self.target:.3f} reached"
            return state

        # Need a full window of history before declaring a plateau.
        if len(rewards) >= self.window + 1:
            prior_best = max(rewards[: -self.window])
            if best - prior_best <= self.threshold:
                state.converged = True
                state.reason = (
                    f"no improvement > {self.threshold:.3f} over last {self.window} iterations"
                )
                return state

        if len(recent) >= self.window and variance <= self.threshold**2:
            state.converged = True
            state.reason = f"reward variance {variance:.5f} below plateau threshold"
            return state

        return state


__all__ = ["ConvergenceDetector", "ConvergenceState"]
