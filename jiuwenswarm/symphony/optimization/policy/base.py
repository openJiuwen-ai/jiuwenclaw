"""Policy interface — generates candidate prompts from task + history + memory."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from jiuwenswarm.symphony.optimization.models import PromptCandidate, PromptRecord, TaskSpec
from jiuwenswarm.symphony.optimization.policy.history import OptimizationHistory


@dataclass
class PolicyRequest:
    """Inputs to one round of candidate generation."""

    task: TaskSpec
    history: OptimizationHistory
    num_candidates: int
    iteration: int = 1
    temperature: float = 0.9
    similar_records: list[PromptRecord] = field(default_factory=list)


class PromptPolicy(ABC):
    """Produces candidate system prompts. The strategy should evolve with history."""

    @abstractmethod
    async def generate(self, request: PolicyRequest) -> list[PromptCandidate]:
        ...


__all__ = ["PolicyRequest", "PromptPolicy"]
