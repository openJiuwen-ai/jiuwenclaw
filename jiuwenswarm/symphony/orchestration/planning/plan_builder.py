"""Build and compose concrete orchestration plans from search state."""

from __future__ import annotations

from collections import defaultdict, deque
from typing import Any

from jiuwenswarm.symphony.orchestration.planning.input_matching import (
    consumed_user_artifact_count,
    filled_inputs,
    missing_inputs,
    output_keys,
)
from jiuwenswarm.symphony.orchestration.planning.models import (
    ArtifactRef,
    GroundedQuery,
    OrchestrationPlan,
    PlanStep,
    SearchState,
)
from jiuwenswarm.symphony.orchestration.planning.utils import skill_id


def state_to_plan(
    *,
    state: SearchState,
    grounded: GroundedQuery,
    skill_by_id: dict[str, dict[str, Any]],
    can_feed_edges: list[dict[str, Any]],
) -> OrchestrationPlan:
    available = set(state.available)
    steps: list[PlanStep] = []
    all_missing: list[dict[str, Any]] = []
    produced: set[tuple[str, str]] = set()
    reasons: list[str] = []

    for current_skill_id in state.skill_ids:
        skill = skill_by_id[current_skill_id]
        filled = filled_inputs(skill, grounded.inferred_inputs)
        missing = missing_inputs(
            skill,
            available,
            inferred_inputs=grounded.inferred_inputs,
        )
        all_missing.extend({**item, "skill_id": current_skill_id} for item in missing)
        outputs = output_keys(skill)
        produced.update(outputs)
        available.update(outputs)
        steps.append(
            PlanStep(
                skill_id=current_skill_id,
                name=skill.get("name", current_skill_id),
                inputs=[
                    {"name": item.get("name"), "type": item.get("type")}
                    for item in skill.get("inputs", [])
                ],
                outputs=[
                    {"name": item.get("name"), "type": item.get("type")}
                    for item in skill.get("outputs", [])
                ],
                missing_inputs=missing,
                filled_inputs=filled,
            )
        )
        if current_skill_id in grounded.seed_skill_ids:
            reasons.append(f"{current_skill_id} selected as a seed skill")

    edges = [can_feed_edges[index] for index in state.edges]
    edge_confidence = (
        sum(float(edge.get("confidence") or 0.0) for edge in edges) / len(edges)
        if edges
        else 1.0
    )
    goal_score = plan_goal_score(
        skill_ids=state.skill_ids,
        seed_skill_ids=grounded.seed_skill_ids,
    )
    status = "ready" if not all_missing else "needs_input"
    return OrchestrationPlan(
        steps=steps,
        produced_artifacts=[
            ArtifactRef(name=name, type=type_, source="skill_output")
            for name, type_ in sorted(produced)
        ],
        missing_inputs=all_missing,
        can_feed_edges=[edge_plan_item(edge) for edge in edges],
        goal_score=goal_score,
        edge_confidence=edge_confidence,
        consumed_user_artifacts=consumed_user_artifact_count(
            steps,
            grounded.available_artifacts,
        ),
        status=status,
        reasons=[*reasons, *state.score_reasons][:8],
    )


def build_outgoing_edges(edges: list[dict[str, Any]]) -> dict[str, list[int]]:
    outgoing: dict[str, list[int]] = defaultdict(list)
    for index, edge in enumerate(edges):
        outgoing[skill_id(edge.get("source"))].append(index)
    return outgoing


def build_incoming_edges(edges: list[dict[str, Any]]) -> dict[str, list[int]]:
    incoming: dict[str, list[int]] = defaultdict(list)
    for index, edge in enumerate(edges):
        incoming[skill_id(edge.get("target"))].append(index)
    return incoming


def plan_goal_score(
    *,
    skill_ids: tuple[str, ...],
    seed_skill_ids: tuple[str, ...] = (),
) -> float:
    return sum(
        seed_skill_score(current_skill_id, seed_skill_ids=seed_skill_ids)
        for current_skill_id in skill_ids
    )


def seed_skill_score(skill_id_: str, *, seed_skill_ids: tuple[str, ...]) -> float:
    return 10.0 if skill_id_ in seed_skill_ids else 0.0


def dedupe_plans(plans: list[OrchestrationPlan]) -> list[OrchestrationPlan]:
    deduped: dict[tuple[str, ...], OrchestrationPlan] = {}
    for plan in plans:
        key = tuple(step.skill_id for step in plan.steps)
        existing = deduped.get(key)
        if existing is None or plan.goal_score > existing.goal_score:
            deduped[key] = plan
    return list(deduped.values())


def compose_dag_plans(
    path_plans: list[OrchestrationPlan],
    *,
    max_plans: int,
) -> list[OrchestrationPlan]:
    composed: list[OrchestrationPlan] = []
    composed.extend(compose_overlapping_path_plans(path_plans, max_plans=max_plans))

    groups: dict[str, list[OrchestrationPlan]] = defaultdict(list)
    for plan in path_plans:
        if plan.steps:
            groups[plan.steps[0].skill_id].append(plan)

    for group in groups.values():
        branch_plans = [plan for plan in group if len(plan.steps) > 1]
        if len(branch_plans) < 2:
            continue
        composed.append(compose_plan_group(branch_plans))

    return dedupe_plans([*composed, *path_plans])[: max_plans * 2]


def compose_overlapping_path_plans(
    path_plans: list[OrchestrationPlan],
    *,
    max_plans: int,
) -> list[OrchestrationPlan]:
    output: list[OrchestrationPlan] = []
    seen: set[tuple[str, ...]] = set()
    for left in path_plans:
        left_ids = tuple(step.skill_id for step in left.steps)
        if len(left_ids) < 2:
            continue
        for right in path_plans:
            if left is right:
                continue
            right_ids = tuple(step.skill_id for step in right.steps)
            if len(right_ids) < 2:
                continue
            overlap = path_overlap_size(left_ids, right_ids)
            if overlap <= 0:
                continue
            stitched_ids = (*left_ids, *right_ids[overlap:])
            if len(stitched_ids) <= max(len(left_ids), len(right_ids)):
                continue
            if len(set(stitched_ids)) != len(stitched_ids):
                continue
            if stitched_ids in seen:
                continue
            seen.add(stitched_ids)
            output.append(compose_plan_group([left, right]))
            if len(output) >= max(1, max_plans):
                return output
    return output


def path_overlap_size(
    left: tuple[str, ...],
    right: tuple[str, ...],
) -> int:
    max_overlap = min(len(left), len(right)) - 1
    for size in range(max_overlap, 0, -1):
        if left[-size:] == right[:size]:
            return size
    return 0


def compose_plan_group(plans: list[OrchestrationPlan]) -> OrchestrationPlan:
    steps_by_id: dict[str, PlanStep] = {}
    edges_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    produced_by_key: dict[tuple[str, str], ArtifactRef] = {}
    missing_by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
    reasons: list[str] = []

    for plan in plans:
        for step in plan.steps:
            steps_by_id.setdefault(step.skill_id, step)
        for edge in plan.can_feed_edges:
            key = (
                edge_endpoint_id(edge, "source"),
                edge_endpoint_id(edge, "target"),
            )
            if key[0] and key[1]:
                existing = edges_by_key.get(key)
                if (
                    existing is None
                    or edge_confidence_value(edge) > edge_confidence_value(existing)
                ):
                    edges_by_key[key] = edge
        for artifact in plan.produced_artifacts:
            produced_by_key.setdefault(artifact.key, artifact)
        for item in plan.missing_inputs:
            key = (
                str(item.get("skill_id") or ""),
                str(item.get("name") or ""),
                str(item.get("type") or "unknown"),
            )
            missing_by_key.setdefault(key, dict(item))
        for reason in plan.reasons:
            if reason not in reasons:
                reasons.append(reason)

    ordered_step_ids = topological_step_ids(
        set(steps_by_id),
        list(edges_by_key.values()),
    )
    steps = []
    for current_skill_id in ordered_step_ids:
        step = steps_by_id.get(current_skill_id)
        if step is not None:
            steps.append(step)
    missing_inputs_list = list(missing_by_key.values())
    edge_confidence = (
        sum(float(edge.get("confidence") or 0.0) for edge in edges_by_key.values())
        / len(edges_by_key)
        if edges_by_key
        else 1.0
    )
    return OrchestrationPlan(
        steps=steps,
        produced_artifacts=list(produced_by_key.values()),
        missing_inputs=missing_inputs_list,
        can_feed_edges=list(edges_by_key.values()),
        goal_score=sum(plan.goal_score for plan in plans),
        edge_confidence=edge_confidence,
        consumed_user_artifacts=max(plan.consumed_user_artifacts for plan in plans),
        status="ready" if not missing_inputs_list else "needs_input",
        reasons=reasons[:8],
    )


def edge_endpoint_id(edge: dict[str, Any], side: str) -> str:
    if side == "source":
        return skill_id(edge.get("source_id") or edge.get("source"))
    return skill_id(edge.get("target_id") or edge.get("target"))


def edge_confidence_value(edge: dict[str, Any]) -> float:
    return float(edge.get("confidence") or 0.0)


def topological_step_ids(
    skill_ids: set[str],
    edges: list[dict[str, Any]],
) -> list[str]:
    incoming = {current_skill_id: 0 for current_skill_id in skill_ids}
    outgoing: dict[str, list[str]] = defaultdict(list)
    for edge in edges:
        source_id = str(edge.get("source_id") or "")
        target_id = str(edge.get("target_id") or "")
        if source_id not in skill_ids or target_id not in skill_ids:
            continue
        outgoing[source_id].append(target_id)
        incoming[target_id] += 1

    queue = deque(
        sorted(
            current_skill_id
            for current_skill_id, count in incoming.items()
            if count == 0
        )
    )
    ordered: list[str] = []
    while queue:
        current_skill_id = queue.popleft()
        ordered.append(current_skill_id)
        for target_id in sorted(outgoing.get(current_skill_id, [])):
            incoming[target_id] -= 1
            if incoming[target_id] == 0:
                queue.append(target_id)
    ordered.extend(sorted(skill_ids - set(ordered)))
    return ordered


def plan_stages(
    steps: list[PlanStep],
    edges: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    step_by_id = {step.skill_id: step for step in steps}
    remaining = set(step_by_id)
    incoming: dict[str, set[str]] = {
        current_skill_id: set() for current_skill_id in remaining
    }
    for edge in edges:
        source_id = str(edge.get("source_id") or "")
        target_id = str(edge.get("target_id") or "")
        if source_id in remaining and target_id in remaining:
            incoming[target_id].add(source_id)

    stages = []
    completed: set[str] = set()
    while remaining:
        ready = sorted(
            current_skill_id
            for current_skill_id in remaining
            if incoming[current_skill_id] <= completed
        )
        if not ready:
            ready = sorted(remaining)
        stages.append(
            {
                "stage": len(stages) + 1,
                "skills": [
                    step_by_id[current_skill_id].to_dict()
                    for current_skill_id in ready
                ],
            }
        )
        completed.update(ready)
        remaining.difference_update(ready)
    return stages


def edge_plan_item(edge: dict[str, Any]) -> dict[str, Any]:
    evidence = edge.get("evidence") or {}
    supporting_fields = evidence.get("supporting_fields") or {}
    return {
        "source_id": skill_id(edge.get("source")),
        "target_id": skill_id(edge.get("target")),
        "confidence": edge.get("confidence"),
        "method": edge.get("method"),
        "port_mappings": supporting_fields.get("port_mappings", [])[:5],
        "source_outputs": supporting_fields.get("source_outputs", [])[:3],
        "target_inputs": supporting_fields.get("target_inputs", [])[:3],
        "reasons": evidence.get("reasons", [])[:3],
    }
