"""Data models for orchestration planning."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class ArtifactRef:
    """A normalized runtime artifact available to the orchestrator."""

    name: str
    type: str = "unknown"
    source: str = "user_query"

    @property
    def key(self) -> tuple[str, str]:
        return (self.name, self.type)

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "type": self.type, "source": self.source}


@dataclass(frozen=True)
class InferredInput:
    """A skill input value inferred from the user request."""

    skill_id: str
    name: str
    type: str = "unknown"
    value: Any = None
    source: str = "llm_grounding"

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.skill_id, self.name, self.type)

    def to_dict(self) -> dict[str, Any]:
        return {
            "skill_id": self.skill_id,
            "name": self.name,
            "type": self.type,
            "value": self.value,
            "source": self.source,
        }


@dataclass(frozen=True)
class GroundedQuery:
    """User query grounded into known artifacts and seed Skills."""

    query: str
    available_artifacts: list[ArtifactRef]
    inferred_inputs: list[InferredInput] = field(default_factory=list)
    seed_skill_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "available_artifacts": [
                artifact.to_dict() for artifact in self.available_artifacts
            ],
            "inferred_inputs": [
                inferred_input.to_dict() for inferred_input in self.inferred_inputs
            ],
            "seed_skill_ids": list(self.seed_skill_ids),
        }


@dataclass(frozen=True)
class PlanStep:
    """One Skill call in a candidate orchestration plan."""

    skill_id: str
    name: str
    inputs: list[dict[str, Any]]
    outputs: list[dict[str, Any]]
    missing_inputs: list[dict[str, Any]] = field(default_factory=list)
    filled_inputs: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "skill_id": self.skill_id,
            "name": self.name,
            "inputs": self.inputs,
            "outputs": self.outputs,
            "missing_inputs": self.missing_inputs,
            "filled_inputs": self.filled_inputs,
        }


@dataclass(frozen=True)
class OrchestrationPlan:
    """A candidate Skill orchestration plan."""

    steps: list[PlanStep]
    produced_artifacts: list[ArtifactRef]
    missing_inputs: list[dict[str, Any]]
    can_feed_edges: list[dict[str, Any]]
    goal_score: float
    edge_confidence: float
    consumed_user_artifacts: int
    status: str
    reasons: list[str]

    def to_dict(self) -> dict[str, Any]:
        from jiuwenswarm.symphony.orchestration.planning.plan_builder import (
            plan_stages,
        )

        return {
            "status": self.status,
            "goal_score": round(self.goal_score, 3),
            "edge_confidence": round(self.edge_confidence, 3),
            "consumed_user_artifacts": self.consumed_user_artifacts,
            "stages": plan_stages(self.steps, self.can_feed_edges),
            "steps": [
                {"step": index + 1, **step.to_dict()}
                for index, step in enumerate(self.steps)
            ],
            "produced_artifacts": [
                artifact.to_dict() for artifact in self.produced_artifacts
            ],
            "missing_inputs": self.missing_inputs,
            "can_feed_edges": self.can_feed_edges,
            "reasons": self.reasons,
        }


class GroundingClient(Protocol):
    """Minimal JSON completion interface used by Skill orchestration."""

    async def complete_json_async(
        self,
        *,
        system_prompt: str,
        user_content: str,
        timeout: int | None = None,
        error_context: str = "LLM",
    ) -> str:
        ...


@dataclass(frozen=True)
class SearchState:
    """Internal forward-search state."""

    skill_ids: tuple[str, ...]
    available: frozenset[tuple[str, str]]
    edges: tuple[int, ...]
    score_reasons: tuple[str, ...] = ()
