"""Input and artifact matching helpers for orchestration plans."""

from __future__ import annotations

from typing import Any, Iterable

from jiuwenswarm.symphony.orchestration.planning.models import (
    ArtifactRef,
    InferredInput,
    PlanStep,
)


def input_coverage_score(
    skill: dict[str, Any],
    available: Iterable[tuple[str, str]],
    *,
    inferred_inputs: list[InferredInput] | None = None,
) -> float:
    required = [
        (str(item.get("name")), str(item.get("type") or "unknown"))
        for item in skill.get("inputs", [])
        if item.get("required", True) and item.get("name")
    ]
    if not required:
        return 1.0
    available_set = set(available)
    inferred_set = inferred_input_keys(str(skill.get("id") or ""), inferred_inputs or [])
    matched = 0
    for item in required:
        if artifact_matches(item, available_set) or inferred_input_matches(
            item,
            inferred_set,
        ):
            matched += 1
    return matched / len(required)


def consumed_user_artifact_count(
    steps: list[PlanStep],
    artifacts: list[ArtifactRef],
) -> int:
    explicit = {
        artifact.key
        for artifact in artifacts
        if artifact.source != "implicit_query"
    }
    if not steps or not explicit:
        return 0
    consumed = 0
    for item in steps[0].inputs:
        expected = (str(item.get("name")), str(item.get("type") or "unknown"))
        if artifact_matches(expected, explicit):
            consumed += 1
    return consumed


def missing_inputs(
    skill: dict[str, Any],
    available: Iterable[tuple[str, str]],
    *,
    inferred_inputs: list[InferredInput] | None = None,
) -> list[dict[str, Any]]:
    available_set = set(available)
    inferred_set = inferred_input_keys(str(skill.get("id") or ""), inferred_inputs or [])
    missing = []
    for item in skill.get("inputs", []):
        if not item.get("required", True):
            continue
        expected = (str(item.get("name")), str(item.get("type") or "unknown"))
        if not artifact_matches(expected, available_set) and not inferred_input_matches(
            expected,
            inferred_set,
        ):
            missing.append({"name": expected[0], "type": expected[1]})
    return missing


def filled_inputs(
    skill: dict[str, Any],
    inferred_inputs: list[InferredInput],
) -> list[dict[str, Any]]:
    skill_id_ = str(skill.get("id") or "")
    input_keys = {
        (str(item.get("name")), str(item.get("type") or "unknown"))
        for item in skill.get("inputs", [])
        if item.get("name")
    }
    filled = []
    for inferred_input in inferred_inputs:
        if inferred_input.skill_id != skill_id_:
            continue
        if inferred_input_matches((inferred_input.name, inferred_input.type), input_keys):
            filled.append(inferred_input.to_dict())
    return filled


def inferred_input_keys(
    skill_id_: str,
    inferred_inputs: list[InferredInput],
) -> set[tuple[str, str]]:
    return {
        (item.name, item.type)
        for item in inferred_inputs
        if item.skill_id == skill_id_
    }


def inferred_input_matches(
    expected: tuple[str, str],
    inferred: set[tuple[str, str]],
) -> bool:
    name, type_ = expected
    return any(
        candidate_name == name
        and (
            candidate_type == type_
            or candidate_type == "unknown"
            or type_ == "unknown"
        )
        for candidate_name, candidate_type in inferred
    )


def artifact_matches(
    expected: tuple[str, str],
    available: set[tuple[str, str]],
) -> bool:
    name, type_ = expected
    if expected in available:
        return True
    return any(
        candidate_name == name and (candidate_type == "unknown" or type_ == "unknown")
        for candidate_name, candidate_type in available
    )


def edge_feeds_missing_inputs(
    *,
    edge: dict[str, Any],
    source: dict[str, Any],
    target: dict[str, Any],
    missing_keys: set[tuple[str, str]],
) -> bool:
    if not missing_keys:
        return False
    evidence = edge.get("evidence") or {}
    supporting_fields = evidence.get("supporting_fields") or {}
    target_input_names = {
        str(item)
        for item in supporting_fields.get("target_inputs", [])
        if str(item).strip()
    }
    target_input_names.update(
        str(mapping.get("target_input"))
        for mapping in supporting_fields.get("port_mappings", [])
        if isinstance(mapping, dict) and mapping.get("target_input")
    )
    missing_names = {name for name, _ in missing_keys}
    if target_input_names & missing_names:
        return True

    source_outputs = output_keys(source)
    target_inputs = {
        (str(item.get("name")), str(item.get("type") or "unknown"))
        for item in target.get("inputs", [])
        if item.get("name")
    }
    return any(
        missing_key in target_inputs
        and artifact_matches(missing_key, set(source_outputs))
        for missing_key in missing_keys
    )


def output_keys(skill: dict[str, Any]) -> frozenset[tuple[str, str]]:
    return frozenset(
        (str(item.get("name")), str(item.get("type") or "unknown"))
        for item in skill.get("outputs", [])
        if item.get("name")
    )
