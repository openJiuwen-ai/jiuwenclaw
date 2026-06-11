"""Online orchestration planning service APIs."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from jiuwenswarm.symphony.config import SymphonyOrchestrationConfig
from jiuwenswarm.symphony.llm import LLMConfig
from jiuwenswarm.symphony.orchestration import load_score_artifacts
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
) -> dict[str, Any]:
    """Run online planning from an existing Symphony score."""

    if orchestration_config is not None:
        top_k = orchestration_config.top_k
        max_depth = orchestration_config.max_depth
        min_edge_confidence = orchestration_config.min_edge_confidence
    artifacts = load_score_artifacts(score_dir)

    if orchestration_config is not None and orchestration_config.mode != "fast":
        raise ValueError(f"Unsupported orchestration mode: {orchestration_config.mode}")

    result = await FastOneShotPlanner(
        artifacts,
        llm_config=llm_config,
        llm_client=llm_client,
        min_edge_confidence=clamp(min_edge_confidence),
        top_k=max(1, int(top_k)),
    ).plan(query)
    result["execution_graph"] = build_execution_graph(result, artifacts)
    return result
