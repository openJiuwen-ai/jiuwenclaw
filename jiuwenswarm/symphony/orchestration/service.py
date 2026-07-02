"""Online orchestration planning service APIs."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

from jiuwenswarm.symphony.config import SymphonyOrchestrationConfig
from jiuwenswarm.symphony.llm import LLMConfig
from jiuwenswarm.symphony.orchestration.artifacts import (
    filter_disabled_score_artifacts,
    load_score_artifacts,
)
from jiuwenswarm.symphony.orchestration.execution_graph import build_execution_graph
from jiuwenswarm.symphony.orchestration.planning.fast import FastOneShotPlanner
from jiuwenswarm.symphony.orchestration.planning.utils import clamp


async def plan_from_score(
    score_dir: str | Path,
    query: str,
    llm_config: LLMConfig | None = None,
    *,
    top_k: int = 3,
    max_depth: int = 4,
    min_edge_confidence: float = 0.7,
    llm_client: Any | None = None,
    ranker: Any | None = None,
    orchestration_config: SymphonyOrchestrationConfig | None = None,
    candidate_skill_ids: Sequence[str] | None = None,
    disabled_skill_names: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Run online planning from an existing Symphony score."""

    if orchestration_config is not None:
        top_k = orchestration_config.top_k
        min_edge_confidence = orchestration_config.min_edge_confidence
    mode = orchestration_config.mode if orchestration_config is not None else "fast"

    artifacts = filter_disabled_score_artifacts(
        load_score_artifacts(score_dir),
        disabled_skill_names,
    )
    if mode != "fast":
        raise ValueError(f"Unsupported orchestration mode: {mode}")

    selected_candidate_skill_ids, skill_retrieval = _input_candidate_summary(
        candidate_skill_ids,
        known_skill_ids=set(artifacts.skill_by_id),
    )
    result = await FastOneShotPlanner(
        artifacts,
        llm_config=llm_config,
        llm_client=llm_client,
        min_edge_confidence=clamp(min_edge_confidence),
        top_k=max(1, int(top_k)),
        candidate_skill_ids=selected_candidate_skill_ids,
    ).plan(query)
    result["skill_retrieval"] = skill_retrieval
    result["execution_graph"] = build_execution_graph(result, artifacts)
    return result


def _input_candidate_summary(
    values: Sequence[str] | None,
    *,
    known_skill_ids: set[str],
) -> tuple[tuple[str, ...] | None, dict[str, Any]]:
    normalized = _normalize_candidate_skill_ids(values)
    selected = tuple(
        current_skill_id
        for current_skill_id in normalized
        if current_skill_id in known_skill_ids
    )
    fallback_reason = ""
    if values is None:
        fallback_reason = "candidate_skill_ids not provided"
    elif not normalized:
        fallback_reason = "candidate_skill_ids is empty"
    elif not selected:
        fallback_reason = "candidate_skill_ids did not match current score"

    return selected or None, {
        "source": "input",
        "used": bool(selected),
        "candidate_skill_ids": list(selected),
        "candidate_count": len(selected),
        "fallback_reason": fallback_reason,
    }


def _normalize_candidate_skill_ids(values: Sequence[str] | None) -> tuple[str, ...]:
    if values is None:
        return ()
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        current_skill_id = str(value or "").strip()
        if not current_skill_id or current_skill_id in seen:
            continue
        seen.add(current_skill_id)
        output.append(current_skill_id)
    return tuple(output)
