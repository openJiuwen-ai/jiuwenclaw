"""Bidirectional beam planner for Symphony score orchestration."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Callable, Protocol, Sequence

from jiuwenswarm.symphony.llm import LLMConfig, create_llm_client, llm_usage_context
from jiuwenswarm.symphony.orchestration.artifacts import ScoreArtifacts
from jiuwenswarm.symphony.orchestration.planning.fast import FAST_PLANNER_MAX_SKILLS
from jiuwenswarm.symphony.orchestration.planning.models import (
    GroundedQuery,
    OrchestrationPlan,
    SearchState,
)
from jiuwenswarm.symphony.orchestration.planning.plan_builder import (
    build_incoming_edges,
    build_outgoing_edges,
    state_to_plan,
)
from jiuwenswarm.symphony.orchestration.planning.utils import skill_id

SEMANTIC_SCORE_THRESHOLD = 0.5
DEFAULT_MAX_CONCURRENT_JUDGES = 3

BEAM_JUDGE_SYSTEM_PROMPT = """You are Symphony's bidirectional beam expansion judge.
Return strict JSON only.

You receive:
- The user's query.
- The current Skill being expanded.
- A search direction: forward or backward.
- Candidate Skill expansions connected by existing can_feed edges.

Task:
- Score each candidate from 0.0 to 1.0 for usefulness to the user's query.
- Use the provided can_feed evidence only.
- Do not invent Skills, inputs, outputs, or edge relationships.

Schema:
{
  "judgements": [
    {"candidate_id": "candidate-id", "score": 0.0, "reason": "short reason"}
  ]
}
"""


@dataclass(frozen=True)
class BeamState:
    """One bidirectional beam search state."""

    skill_ids: tuple[str, ...]
    edge_indices: tuple[int, ...]
    available: frozenset[tuple[str, str]]
    semantic_scores: tuple[float, ...]
    score_reasons: tuple[str, ...]
    seed_skill_ids: tuple[str, ...]
    directions: frozenset[str]

    @property
    def depth(self) -> int:
        return len(self.skill_ids)

    @property
    def head(self) -> str:
        return self.skill_ids[0]

    @property
    def tail(self) -> str:
        return self.skill_ids[-1]

    def to_search_state(self) -> SearchState:
        return SearchState(
            skill_ids=self.skill_ids,
            available=self.available,
            edges=self.edge_indices,
            score_reasons=self.score_reasons,
        )


@dataclass(frozen=True)
class NeighborExpansion:
    """A single adjacent Skill candidate to be judged."""

    candidate_id: str
    state: BeamState
    direction: str
    current_skill_id: str
    candidate_skill_id: str
    edge_index: int
    edge_key: str


@dataclass(frozen=True)
class SemanticJudgement:
    """LLM judgement for one neighbor expansion."""

    candidate_id: str
    score: float
    reason: str


class SemanticJudgementCache:
    """Per-plan semantic judgement cache keyed by query, Skill, and edge."""

    def __init__(self) -> None:
        self._items: dict[tuple[str, str, str], SemanticJudgement] = {}
        self.hits = 0
        self.misses = 0

    def get(
        self,
        query_signature: str,
        expansion: NeighborExpansion,
    ) -> SemanticJudgement | None:
        key = self.key_for(query_signature, expansion)
        cached = self._items.get(key)
        if cached is None:
            self.misses += 1
            return None
        self.hits += 1
        return cached

    def set(
        self,
        query_signature: str,
        expansion: NeighborExpansion,
        judgement: SemanticJudgement,
    ) -> None:
        self._items[self.key_for(query_signature, expansion)] = judgement

    @staticmethod
    def key_for(
        query_signature: str,
        expansion: NeighborExpansion,
    ) -> tuple[str, str, str]:
        return (
            query_signature,
            expansion.candidate_skill_id,
            expansion.edge_key,
        )


class BeamJudgeQueue:
    """Run grouped LLM judgement tasks with a global concurrency limit."""

    def __init__(
        self,
        *,
        planner: BidirectionalBeamPlannerProtocol,
        max_concurrent_judges: int = DEFAULT_MAX_CONCURRENT_JUDGES,
    ) -> None:
        self.planner = planner
        self._semaphore = asyncio.Semaphore(max(1, int(max_concurrent_judges)))
        self.llm_call_count = 0
        self.failures: list[str] = []

    async def judge_groups(
        self,
        query: str,
        groups: list[list[NeighborExpansion]],
    ) -> dict[str, SemanticJudgement]:
        tasks = [
            asyncio.create_task(self._judge_group(query, group))
            for group in groups
            if group
        ]
        if not tasks:
            return {}

        output: dict[str, SemanticJudgement] = {}
        for result in await asyncio.gather(*tasks):
            output.update(result)
        return output

    async def _judge_group(
        self,
        query: str,
        group: list[NeighborExpansion],
    ) -> dict[str, SemanticJudgement]:
        async with self._semaphore:
            self.llm_call_count += 1
            try:
                return await self.planner.judge_expansions(query, group)
            except Exception as exc:
                candidates = ", ".join(item.candidate_id for item in group[:5])
                self.failures.append(
                    f"beam judge failed for candidates [{candidates}]: {exc}"
                )
                return {}


class BidirectionalBeamPlannerProtocol(Protocol):
    async def judge_expansions(
        self,
        query: str,
        expansions: list[NeighborExpansion],
    ) -> dict[str, SemanticJudgement]:
        ...


class BidirectionalBeamPlanner:
    """Build candidate plans through semantic bidirectional beam search."""

    def __init__(
        self,
        artifacts: ScoreArtifacts,
        *,
        llm_config: LLMConfig | None,
        llm_client: Any | None,
        min_edge_confidence: float,
        top_k: int,
        max_depth: int,
        candidate_skill_ids: Sequence[str] | None = None,
        max_concurrent_judges: int = DEFAULT_MAX_CONCURRENT_JUDGES,
    ) -> None:
        self.artifacts = artifacts
        self.llm_config = llm_config
        self.llm_client = llm_client
        self.min_edge_confidence = min_edge_confidence
        self.top_k = max(1, int(top_k))
        self.max_depth = max(1, int(max_depth))
        self.max_concurrent_judges = max(1, int(max_concurrent_judges))
        self.skill_by_id = artifacts.skill_by_id
        self.candidate_skill_ids = self._normalize_candidate_skill_ids(
            candidate_skill_ids,
            known_skill_ids=set(self.skill_by_id),
        )
        self.eligible_edges = self._sorted_eligible_edges()
        self.outgoing_edges = build_outgoing_edges(self.eligible_edges)
        self.incoming_edges = build_incoming_edges(self.eligible_edges)
        self.cache = SemanticJudgementCache()

    async def plan(self, query: str) -> dict[str, Any]:
        client = self._client()
        del client
        query_signature = _query_signature(query)
        frontier = self._initial_states()
        all_states = list(frontier)
        judge_queue = BeamJudgeQueue(
            planner=self,
            max_concurrent_judges=self.max_concurrent_judges,
        )

        for _round in range(max(0, self.max_depth - 1)):
            groups, cached = self._expansion_groups(query_signature, frontier)
            if not groups and not cached:
                break
            fresh = await judge_queue.judge_groups(query, groups)
            fresh = self._apply_judgement_aliases(fresh)
            for candidate_id, judgement in fresh.items():
                expansion = self._expansion_by_id(candidate_id)
                if expansion is not None:
                    self.cache.set(query_signature, expansion, judgement)
            judgements = {**cached, **fresh}
            next_states = self._apply_judgements(judgements)
            if not next_states:
                break
            all_states = self._rank_and_trim(
                self._merge_states(next_states),
                limit=self.top_k,
            )
            frontier = self._rank_and_trim(
                self._merge_states(next_states),
                limit=self.top_k,
            )

        plans = self._plans_from_states(all_states)[: self.top_k]
        recommended_plans = [self._plan_payload(plan) for plan in plans]
        status = recommended_plans[0]["status"] if recommended_plans else "no_plan"
        reason = recommended_plans[0].get("reason", "") if recommended_plans else ""
        unique_skill_ids = {
            current_skill_id
            for state in all_states
            for current_skill_id in state.skill_ids
        }
        return {
            "query": query,
            "score_dir": str(self.artifacts.score_dir),
            "planning_mode": "bidirectional_beam",
            "llm_call_count": judge_queue.llm_call_count,
            "candidate_skill_count": len(unique_skill_ids),
            "candidate_edge_count": len(self.eligible_edges),
            "plans": recommended_plans,
            "recommended_plans": recommended_plans,
            "ranking_mode": "bidirectional_beam",
            "decision": {
                "mode": "bidirectional_beam",
                "strategy": "semantic_bidirectional_beam",
                "validated_count": len(recommended_plans),
                "candidate_count": len(unique_skill_ids),
                "judge_cache_hits": self.cache.hits,
                "judge_cache_misses": self.cache.misses,
            },
            "validation": {
                "valid": True,
                "details": judge_queue.failures[:10],
            },
            "status": status,
            "reason": reason,
        }

    async def judge_expansions(
        self,
        query: str,
        expansions: list[NeighborExpansion],
    ) -> dict[str, SemanticJudgement]:
        if not expansions:
            return {}

        first = expansions[0]
        current_skill = self.skill_by_id.get(first.current_skill_id, {})
        payload = {
            "query": query,
            "direction": first.direction,
            "current_skill": self._skill_payload(current_skill),
            "state": {
                "skill_ids": list(first.state.skill_ids),
                "depth": first.state.depth,
                "directions": sorted(first.state.directions),
            },
            "candidates": [
                self._candidate_payload(expansion)
                for expansion in expansions
            ],
        }
        with llm_usage_context("orchestration", "bidirectional_beam_judgement"):
            raw = await self._client().complete_json_async(
                system_prompt=BEAM_JUDGE_SYSTEM_PROMPT,
                user_content=json.dumps(payload, ensure_ascii=False),
                error_context="Symphony bidirectional beam judgement",
            )
        data = json.loads(raw)
        items = data.get("judgements") if isinstance(data, dict) else data
        if not isinstance(items, list):
            return {}

        known_ids = {expansion.candidate_id for expansion in expansions}
        judgements: dict[str, SemanticJudgement] = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            candidate_id = str(item.get("candidate_id") or "").strip()
            if candidate_id not in known_ids:
                continue
            score = _clamp_score(item.get("score"))
            judgements[candidate_id] = SemanticJudgement(
                candidate_id=candidate_id,
                score=score,
                reason=str(item.get("reason") or "").strip(),
            )
        return judgements

    def _client(self) -> Any:
        if self.llm_client is not None:
            return self.llm_client
        if self.llm_config is None:
            raise ValueError(
                "beam Symphony planning requires llm_config or llm_client."
            )
        return create_llm_client(self.llm_config)

    def _initial_states(self) -> list[BeamState]:
        seeds = self.candidate_skill_ids or self._default_seed_skill_ids()
        states = [
            BeamState(
                skill_ids=(current_skill_id,),
                edge_indices=(),
                available=frozenset(),
                semantic_scores=(1.0,),
                score_reasons=(f"{current_skill_id} selected as a seed skill",),
                seed_skill_ids=tuple(seeds),
                directions=frozenset({"seed"}),
            )
            for current_skill_id in seeds
            if current_skill_id in self.skill_by_id
        ]
        return self._rank_and_trim(states, limit=self.top_k)

    def _default_seed_skill_ids(self) -> tuple[str, ...]:
        output: list[str] = []
        seen: set[str] = set()
        for edge in self.eligible_edges:
            for key in ("source", "target"):
                current_skill_id = skill_id(edge.get(key))
                if (
                    current_skill_id
                    and current_skill_id in self.skill_by_id
                    and current_skill_id not in seen
                ):
                    seen.add(current_skill_id)
                    output.append(current_skill_id)
                if len(output) >= FAST_PLANNER_MAX_SKILLS:
                    return tuple(output)
        if output:
            return tuple(output)
        for skill in self.artifacts.skills[:FAST_PLANNER_MAX_SKILLS]:
            current_skill_id = str(skill.get("id") or "").strip()
            if current_skill_id:
                output.append(current_skill_id)
        return tuple(output)

    def _expansion_groups(
        self,
        query_signature: str,
        states: list[BeamState],
    ) -> tuple[list[list[NeighborExpansion]], dict[str, SemanticJudgement]]:
        groups: list[list[NeighborExpansion]] = []
        cached: dict[str, SemanticJudgement] = {}
        current_expansions: dict[str, NeighborExpansion] = {}
        pending_keys: dict[tuple[str, str, str], str] = {}
        aliases: dict[str, list[str]] = defaultdict(list)
        for state in states:
            for direction, current_skill_id in (
                ("forward", state.tail),
                ("backward", state.head),
            ):
                expansions = self._neighbor_expansions(
                    state=state,
                    direction=direction,
                    current_skill_id=current_skill_id,
                )
                misses = []
                for expansion in expansions:
                    current_expansions[expansion.candidate_id] = expansion
                    judgement = self.cache.get(query_signature, expansion)
                    if judgement is None:
                        misses.append(expansion)
                    else:
                        cached[expansion.candidate_id] = judgement
                if misses:
                    deduped_misses = []
                    for expansion in misses:
                        key = self.cache.key_for(query_signature, expansion)
                        existing_candidate_id = pending_keys.get(key)
                        if existing_candidate_id is not None:
                            aliases[existing_candidate_id].append(
                                expansion.candidate_id
                            )
                            continue
                        pending_keys[key] = expansion.candidate_id
                        deduped_misses.append(expansion)
                    if deduped_misses:
                        groups.append(deduped_misses)
        self._current_expansions = current_expansions
        self._current_expansion_aliases = aliases
        return groups, cached

    def _apply_judgement_aliases(
        self,
        judgements: dict[str, SemanticJudgement],
    ) -> dict[str, SemanticJudgement]:
        output = dict(judgements)
        for source_id, alias_ids in getattr(
            self,
            "_current_expansion_aliases",
            {},
        ).items():
            judgement = judgements.get(source_id)
            if judgement is None:
                continue
            for alias_id in alias_ids:
                output[alias_id] = SemanticJudgement(
                    candidate_id=alias_id,
                    score=judgement.score,
                    reason=judgement.reason,
                )
        return output

    def _neighbor_expansions(
        self,
        *,
        state: BeamState,
        direction: str,
        current_skill_id: str,
    ) -> list[NeighborExpansion]:
        if state.depth >= self.max_depth:
            return []
        edge_indices = (
            self.outgoing_edges.get(current_skill_id, [])
            if direction == "forward"
            else self.incoming_edges.get(current_skill_id, [])
        )
        expansions = []
        for edge_index in edge_indices:
            edge = self.eligible_edges[edge_index]
            candidate_skill_id = (
                skill_id(edge.get("target"))
                if direction == "forward"
                else skill_id(edge.get("source"))
            )
            if (
                not candidate_skill_id
                or candidate_skill_id not in self.skill_by_id
                or candidate_skill_id in state.skill_ids
            ):
                continue
            expansions.append(
                NeighborExpansion(
                    candidate_id=(
                        f"{direction}:{edge_index}:"
                        f"{_state_signature(state)}:{candidate_skill_id}"
                    ),
                    state=state,
                    direction=direction,
                    current_skill_id=current_skill_id,
                    candidate_skill_id=candidate_skill_id,
                    edge_index=edge_index,
                    edge_key=_edge_key(edge),
                )
            )
        return expansions

    def _apply_judgements(
        self,
        judgements: dict[str, SemanticJudgement],
    ) -> list[BeamState]:
        output = []
        for candidate_id, judgement in judgements.items():
            if judgement.score < SEMANTIC_SCORE_THRESHOLD:
                continue
            expansion = self._expansion_by_id(candidate_id)
            if expansion is None:
                continue
            state = expansion.state
            if expansion.direction == "forward":
                skill_ids = (*state.skill_ids, expansion.candidate_skill_id)
                edge_indices = (*state.edge_indices, expansion.edge_index)
            else:
                skill_ids = (expansion.candidate_skill_id, *state.skill_ids)
                edge_indices = (expansion.edge_index, *state.edge_indices)
            output.append(
                BeamState(
                    skill_ids=skill_ids,
                    edge_indices=edge_indices,
                    available=state.available,
                    semantic_scores=(*state.semantic_scores, judgement.score),
                    score_reasons=_append_unique(
                        state.score_reasons,
                        judgement.reason,
                    ),
                    seed_skill_ids=state.seed_skill_ids,
                    directions=state.directions | {expansion.direction},
                )
            )
        return output

    def _expansion_by_id(self, candidate_id: str) -> NeighborExpansion | None:
        return getattr(self, "_current_expansions", {}).get(candidate_id)

    def _plans_from_states(self, states: list[BeamState]) -> list[OrchestrationPlan]:
        state_plans = [
            (
                state,
                state_to_plan(
                    state=state.to_search_state(),
                    grounded=GroundedQuery(
                        query="",
                        available_artifacts=[],
                        seed_skill_ids=state.seed_skill_ids,
                    ),
                    skill_by_id=self.skill_by_id,
                    can_feed_edges=self.eligible_edges,
                ),
            )
            for state in states
            if state.skill_ids
        ]
        state_plans.sort(
            key=lambda item: (
                -self._state_score(item[0]),
                len(item[1].missing_inputs),
                len(item[1].steps),
                tuple(step.skill_id for step in item[1].steps),
            )
        )
        return [plan for _state, plan in state_plans]

    def _merge_states(self, states: list[BeamState]) -> list[BeamState]:
        deduped = self._dedupe_states(states)
        merged = list(deduped)
        for groups in (
            _group_states(deduped, lambda state: state.tail),
            _group_states(deduped, lambda state: state.head),
            _group_states(
                deduped,
                lambda state: ",".join(sorted(state.skill_ids)),
            ),
        ):
            for group in groups:
                if len(group) < 2:
                    continue
                merged_state = self._merge_state_group(group)
                if merged_state is not None:
                    merged.append(merged_state)
        return self._dedupe_states(merged)

    def _merge_state_group(self, states: list[BeamState]) -> BeamState | None:
        skill_ids = {current for state in states for current in state.skill_ids}
        edge_indices = tuple(
            sorted(
                {
                    edge_index
                    for state in states
                    for edge_index in state.edge_indices
                }
            )
        )
        ordered = self._topological_order(skill_ids, edge_indices)
        if ordered is None:
            return None
        seed_skill_ids = tuple(
            dict.fromkeys(
                current
                for state in states
                for current in state.seed_skill_ids
            )
        )
        semantic_scores = tuple(
            score for state in states for score in state.semantic_scores
        )
        reasons = tuple(
            dict.fromkeys(
                reason
                for state in states
                for reason in state.score_reasons
                if reason
            )
        )
        directions = frozenset(
            direction
            for state in states
            for direction in state.directions
        )
        return BeamState(
            skill_ids=tuple(ordered),
            edge_indices=edge_indices,
            available=frozenset(),
            semantic_scores=semantic_scores or (1.0,),
            score_reasons=reasons[:8],
            seed_skill_ids=seed_skill_ids,
            directions=directions,
        )

    def _topological_order(
        self,
        skill_ids: set[str],
        edge_indices: tuple[int, ...],
    ) -> list[str] | None:
        incoming = {current_skill_id: 0 for current_skill_id in skill_ids}
        outgoing: dict[str, list[str]] = defaultdict(list)
        for edge_index in edge_indices:
            edge = self.eligible_edges[edge_index]
            source_id = skill_id(edge.get("source"))
            target_id = skill_id(edge.get("target"))
            if source_id not in skill_ids or target_id not in skill_ids:
                continue
            outgoing[source_id].append(target_id)
            incoming[target_id] += 1

        ready = sorted(
            current_skill_id
            for current_skill_id, count in incoming.items()
            if count == 0
        )
        ordered = []
        while ready:
            current_skill_id = ready.pop(0)
            ordered.append(current_skill_id)
            for target_id in sorted(outgoing.get(current_skill_id, [])):
                incoming[target_id] -= 1
                if incoming[target_id] == 0:
                    ready.append(target_id)
                    ready.sort()
        if len(ordered) != len(skill_ids):
            return None
        return ordered

    def _dedupe_states(self, states: list[BeamState]) -> list[BeamState]:
        deduped: dict[tuple[tuple[str, ...], tuple[int, ...]], BeamState] = {}
        for state in states:
            key = (state.skill_ids, tuple(sorted(state.edge_indices)))
            existing = deduped.get(key)
            if (
                existing is None
                or self._state_score(state) > self._state_score(existing)
            ):
                deduped[key] = state
        return list(deduped.values())

    def _rank_and_trim(
        self,
        states: list[BeamState],
        *,
        limit: int,
    ) -> list[BeamState]:
        return sorted(
            self._dedupe_states(states),
            key=lambda state: (
                -self._state_score(state),
                self._state_missing_count(state),
                len(state.skill_ids),
                state.skill_ids,
            ),
        )[:limit]

    def _state_score(self, state: BeamState) -> float:
        plan = self._state_plan(state)
        semantic_score = (
            sum(state.semantic_scores) / len(state.semantic_scores)
            if state.semantic_scores
            else 0.0
        )
        edge_confidence = self._state_edge_confidence(state)
        input_coverage = self._input_coverage(plan)
        seed_hit = 1.0 if set(state.skill_ids) & set(state.seed_skill_ids) else 0.0
        depth_penalty = max(0, len(state.skill_ids) - 1)
        return (
            0.45 * semantic_score
            + 0.25 * edge_confidence
            + 0.20 * input_coverage
            + 0.10 * seed_hit
            - 0.03 * depth_penalty
        )

    def _state_plan(self, state: BeamState) -> OrchestrationPlan:
        return state_to_plan(
            state=state.to_search_state(),
            grounded=GroundedQuery(
                query="",
                available_artifacts=[],
                seed_skill_ids=state.seed_skill_ids,
            ),
            skill_by_id=self.skill_by_id,
            can_feed_edges=self.eligible_edges,
        )

    def _state_missing_count(self, state: BeamState) -> int:
        return len(self._state_plan(state).missing_inputs)

    def _state_edge_confidence(self, state: BeamState) -> float:
        if not state.edge_indices:
            return 1.0
        values = [
            float(self.eligible_edges[index].get("confidence") or 0.0)
            for index in state.edge_indices
        ]
        return sum(values) / len(values)

    @staticmethod
    def _input_coverage(plan: OrchestrationPlan) -> float:
        required = 0
        for step in plan.steps:
            required += sum(
                1 for item in step.inputs if item.get("required", True)
            )
        if required <= 0:
            return 1.0
        missing = len(plan.missing_inputs)
        return max(0.0, min(1.0, (required - missing) / required))

    def _candidate_payload(self, expansion: NeighborExpansion) -> dict[str, Any]:
        edge = self.eligible_edges[expansion.edge_index]
        return {
            "candidate_id": expansion.candidate_id,
            "skill": self._skill_payload(
                self.skill_by_id[expansion.candidate_skill_id]
            ),
            "edge": self._edge_payload(edge),
        }

    @staticmethod
    def _skill_payload(skill: dict[str, Any]) -> dict[str, Any]:
        current_skill_id = str(skill.get("id") or "")
        return {
            "id": current_skill_id,
            "name": str(skill.get("name") or current_skill_id),
            "description": str(skill.get("description") or "")[:800],
        }

    @staticmethod
    def _edge_payload(edge: dict[str, Any]) -> dict[str, Any]:
        evidence = (
            edge.get("evidence")
            if isinstance(edge.get("evidence"), dict)
            else {}
        )
        return {
            "source_id": skill_id(edge.get("source")),
            "target_id": skill_id(edge.get("target")),
            "confidence": edge.get("confidence"),
            "method": edge.get("method"),
            "evidence": evidence,
        }

    def _sorted_eligible_edges(self) -> list[dict[str, Any]]:
        filtered_edges = []
        for edge in self.artifacts.graph.get("edges", []):
            edge_confidence = float(edge.get("confidence") or 0.0)
            source_id = skill_id(edge.get("source"))
            target_id = skill_id(edge.get("target"))
            if (
                edge.get("type") == "can_feed"
                and edge_confidence >= self.min_edge_confidence
                and source_id in self.skill_by_id
                and target_id in self.skill_by_id
            ):
                filtered_edges.append(edge)
        return sorted(
            filtered_edges,
            key=lambda item: (
                -float(item.get("confidence") or 0.0),
                skill_id(item.get("source")),
                skill_id(item.get("target")),
            ),
        )

    def _plan_payload(self, plan: OrchestrationPlan) -> dict[str, Any]:
        payload = plan.to_dict()
        title = "Symphony beam plan"
        if plan.steps:
            title = " -> ".join(step.name for step in plan.steps[:4])
        reason = "; ".join(plan.reasons[:3])
        payload.update(
            {
                "title": title,
                "reason": reason,
                "plan_classification": (
                    "executable"
                    if plan.status == "ready"
                    else "structurally_valid_but_incomplete"
                ),
                "connectivity_trace": (
                    ["can_feed"] if plan.can_feed_edges else []
                ),
                "source": "bidirectional_beam",
            }
        )
        return payload

    @staticmethod
    def _normalize_candidate_skill_ids(
        values: Sequence[str] | None,
        *,
        known_skill_ids: set[str],
    ) -> tuple[str, ...] | None:
        if values is None:
            return None
        output = []
        seen = set()
        for value in values:
            current_skill_id = str(value or "").strip()
            if (
                not current_skill_id
                or current_skill_id in seen
                or current_skill_id not in known_skill_ids
            ):
                continue
            seen.add(current_skill_id)
            output.append(current_skill_id)
        return tuple(output) if output else None


def _query_signature(query: str) -> str:
    normalized = " ".join(str(query or "").strip().lower().split())
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()


def _state_signature(state: BeamState) -> str:
    payload = {
        "skills": state.skill_ids,
        "edges": state.edge_indices,
        "directions": sorted(state.directions),
    }
    return hashlib.sha1(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:10]


def _edge_key(edge: dict[str, Any]) -> str:
    source_id = skill_id(edge.get("source"))
    target_id = skill_id(edge.get("target"))
    payload = {
        "source_id": source_id,
        "target_id": target_id,
        "method": edge.get("method"),
        "confidence": edge.get("confidence"),
        "evidence": edge.get("evidence"),
    }
    digest = hashlib.sha1(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        ).encode("utf-8")
    ).hexdigest()[:12]
    return f"{source_id}->{target_id}:{digest}"


def _clamp_score(value: Any) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, parsed))


def _append_unique(values: tuple[str, ...], value: str) -> tuple[str, ...]:
    text = str(value or "").strip()
    if not text or text in values:
        return values
    return (*values, text)


def _group_states(
    states: list[BeamState],
    key_fn: Callable[[BeamState], str],
) -> list[list[BeamState]]:
    grouped: dict[str, list[BeamState]] = defaultdict(list)
    for state in states:
        grouped[str(key_fn(state))].append(state)
    return list(grouped.values())
