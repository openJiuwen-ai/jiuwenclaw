import asyncio
import json

from jiuwenswarm.symphony.orchestration.artifacts import ScoreArtifacts
from jiuwenswarm.symphony.orchestration.planning.beam import (
    BidirectionalBeamPlanner,
)


class _FakeBeamLLM:
    def __init__(self, scores: dict[str, float], *, delay: float = 0.0) -> None:
        self.scores = scores
        self.delay = delay
        self.calls = []
        self.active = 0
        self.max_active = 0

    async def complete_json_async(self, **kwargs):
        self.calls.append(kwargs)
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            payload = json.loads(kwargs["user_content"])
            return json.dumps(
                {
                    "judgements": [
                        {
                            "candidate_id": candidate["candidate_id"],
                            "score": self.scores.get(candidate["skill"]["id"], 0.9),
                            "reason": f"{candidate['skill']['id']} is useful",
                        }
                        for candidate in payload["candidates"]
                    ]
                }
            )
        finally:
            self.active -= 1


async def test_beam_batches_outgoing_neighbors_and_filters_low_scores(tmp_path):
    artifacts = _artifacts(
        tmp_path,
        edges=[
            _edge("skill-a", "skill-b", confidence=0.91),
            _edge("skill-a", "skill-c", confidence=0.88),
            _edge("skill-a", "skill-d", confidence=0.2),
        ],
    )
    llm = _FakeBeamLLM({"skill-b": 0.9, "skill-c": 0.4})

    result = await _planner(
        artifacts,
        llm,
        top_k=2,
        max_depth=2,
        candidate_skill_ids=["skill-a"],
    ).plan("compose an alpha plan")

    assert result["planning_mode"] == "bidirectional_beam"
    assert result["llm_call_count"] == 1
    payload = json.loads(llm.calls[0]["user_content"])
    assert payload["direction"] == "forward"
    assert [item["skill"]["id"] for item in payload["candidates"]] == [
        "skill-b",
        "skill-c",
    ]
    assert _plan_signatures(result) == {("skill-a", "skill-b")}


async def test_beam_scores_incoming_neighbors_for_backward_expansion(tmp_path):
    artifacts = _artifacts(
        tmp_path,
        edges=[
            _edge("skill-a", "skill-c", confidence=0.91),
            _edge("skill-b", "skill-c", confidence=0.89),
        ],
    )
    llm = _FakeBeamLLM({"skill-a": 0.8, "skill-b": 0.9})

    result = await _planner(
        artifacts,
        llm,
        top_k=2,
        max_depth=2,
        candidate_skill_ids=["skill-c"],
    ).plan("prepare the final output")

    payload = json.loads(llm.calls[0]["user_content"])
    assert payload["direction"] == "backward"
    assert {item["skill"]["id"] for item in payload["candidates"]} == {
        "skill-a",
        "skill-b",
    }
    assert _plan_signatures(result) == {
        ("skill-a", "skill-c"),
        ("skill-b", "skill-c"),
    }


async def test_beam_reuses_same_round_judgement_for_duplicate_skill_edge(tmp_path):
    artifacts = _artifacts(
        tmp_path,
        edges=[
            _edge("skill-a", "skill-shared", confidence=0.91),
            _edge("skill-c", "skill-shared", confidence=0.9),
            _edge("skill-shared", "skill-d", confidence=0.89),
        ],
    )
    llm = _FakeBeamLLM({"skill-shared": 0.9, "skill-d": 0.95})

    result = await _planner(
        artifacts,
        llm,
        top_k=2,
        max_depth=3,
        candidate_skill_ids=["skill-a", "skill-c"],
    ).plan("finish through the shared skill")

    assert result["llm_call_count"] == 3
    assert len(llm.calls) == 3
    final_payload = json.loads(llm.calls[-1]["user_content"])
    assert [item["skill"]["id"] for item in final_payload["candidates"]] == [
        "skill-d"
    ]
    assert result["decision"]["judge_cache_misses"] >= 3
    assert any("skill-d" in signature for signature in _plan_signatures(result))


async def test_beam_limits_concurrent_judge_requests(tmp_path):
    artifacts = _artifacts(
        tmp_path,
        edges=[
            _edge("skill-a", "skill-b", confidence=0.91),
            _edge("skill-c", "skill-d", confidence=0.9),
            _edge("skill-e", "skill-f", confidence=0.89),
        ],
    )
    llm = _FakeBeamLLM(
        {"skill-b": 0.9, "skill-d": 0.9, "skill-f": 0.9},
        delay=0.02,
    )

    result = await _planner(
        artifacts,
        llm,
        top_k=3,
        max_depth=2,
        candidate_skill_ids=["skill-a", "skill-c", "skill-e"],
    ).plan("run several independent branches")

    assert result["llm_call_count"] == 3
    assert llm.max_active == 3


async def test_beam_merges_converging_paths_into_dag_plan(tmp_path):
    artifacts = _artifacts(
        tmp_path,
        edges=[
            _edge("skill-a", "skill-c", confidence=0.91),
            _edge("skill-b", "skill-c", confidence=0.9),
        ],
    )
    llm = _FakeBeamLLM({"skill-c": 0.9})

    result = await _planner(
        artifacts,
        llm,
        top_k=3,
        max_depth=2,
        candidate_skill_ids=["skill-a", "skill-b"],
    ).plan("merge two preparation skills")

    assert ("skill-a", "skill-b", "skill-c") in _plan_signatures(result)
    merged = next(
        plan for plan in result["recommended_plans"]
        if tuple(step["skill_id"] for step in plan["steps"])
        == ("skill-a", "skill-b", "skill-c")
    )
    assert {
        (edge["source_id"], edge["target_id"])
        for edge in merged["can_feed_edges"]
    } == {("skill-a", "skill-c"), ("skill-b", "skill-c")}


def _planner(
    artifacts: ScoreArtifacts,
    llm: _FakeBeamLLM,
    *,
    top_k: int,
    max_depth: int,
    candidate_skill_ids: list[str],
) -> BidirectionalBeamPlanner:
    return BidirectionalBeamPlanner(
        artifacts,
        llm_config=None,
        llm_client=llm,
        min_edge_confidence=0.7,
        top_k=top_k,
        max_depth=max_depth,
        candidate_skill_ids=candidate_skill_ids,
    )


def _artifacts(
    tmp_path,
    *,
    edges: list[dict[str, object]],
) -> ScoreArtifacts:
    skill_ids = sorted(
        {
            endpoint
            for edge in edges
            for endpoint in (edge["source"], edge["target"])
        }
    )
    return ScoreArtifacts(
        score_dir=tmp_path,
        manifest={},
        skills=[
            {
                "id": current_skill_id,
                "name": current_skill_id.replace("-", " ").title(),
                "description": f"{current_skill_id} description",
                "inputs": [{"name": "input", "type": "text", "required": True}],
                "outputs": [{"name": current_skill_id, "type": "text"}],
            }
            for current_skill_id in skill_ids
        ],
        graph={"edges": edges},
        lookup={},
    )


def _edge(
    source: str,
    target: str,
    *,
    confidence: float,
) -> dict[str, object]:
    return {
        "type": "can_feed",
        "source": source,
        "target": target,
        "confidence": confidence,
        "method": "llm",
        "evidence": {
            "reasons": [f"{source} feeds {target}"],
            "supporting_fields": {
                "source_outputs": [source],
                "target_inputs": ["input"],
            },
        },
    }


def _plan_signatures(result: dict[str, object]) -> set[tuple[str, ...]]:
    return {
        tuple(step["skill_id"] for step in plan["steps"])
        for plan in result["recommended_plans"]
    }
