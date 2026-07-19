import asyncio
import json

from jiuwenswarm.symphony.orchestration.artifacts import ScoreArtifacts
from jiuwenswarm.symphony.orchestration.planning.beam import (
    BeamState,
    BidirectionalBeamPlanner,
    SubtreeCacheEntry,
    _required_output_types_from_query,
)


class _FakeBeamLLM:
    def __init__(
        self,
        scores: dict[str, float],
        *,
        delay: float = 0.0,
        rerank_response: dict[str, object] | str | None = None,
    ) -> None:
        self.scores = scores
        self.delay = delay
        self.rerank_response = rerank_response or {"selected_plan_index": 1}
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
            if "candidate_plans" in payload:
                if isinstance(self.rerank_response, str):
                    return self.rerank_response
                return json.dumps(self.rerank_response)
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

    @property
    def judge_calls(self):
        return [
            call
            for call in self.calls
            if "candidates" in json.loads(call["user_content"])
        ]

    @property
    def rerank_calls(self):
        return [
            call
            for call in self.calls
            if "candidate_plans" in json.loads(call["user_content"])
        ]


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
    assert result["recommended_plans"][0]["title"] == "compose an alpha plan"
    assert result["beam_search"]["seed_skill_ids"] == ["skill-a"]
    assert result["beam_search"]["round_index"] == 1
    assert result["beam_search"]["events"][0]["event"] == "started"
    assert result["beam_search"]["events"][0]["payload"]["graph"]["nodes"] == [
        {
            "id": "skill-a",
            "label": "Skill A",
            "status": "seed",
            "seed": True,
            "direction": "seed",
        }
    ]
    assert result["beam_search"]["rounds"][0]["selected_count"] == 1
    assert result["beam_search"]["rounds"][0]["rejected_count"] == 1
    assert result["beam_search"]["rounds"][0]["retained_paths"][0]["skill_ids"] == [
        "skill-a",
        "skill-b",
    ]
    candidates = result["beam_search"]["rounds"][0]["candidates"]
    assert [item["candidate_skill_id"] for item in candidates] == [
        "skill-b",
        "skill-c",
    ]
    assert {item["status"] for item in candidates} == {"selected", "rejected"}
    assert all("score" not in item and "reason" not in item for item in candidates)
    graph_nodes = {
        item["id"]: item
        for item in result["beam_search"]["graph"]["nodes"]
    }
    assert graph_nodes["skill-a"]["status"] == "final"
    assert graph_nodes["skill-a"]["seed"] is True
    assert graph_nodes["skill-b"]["status"] == "final"
    assert graph_nodes["skill-c"]["status"] == "rejected"
    payload = json.loads(llm.judge_calls[0]["user_content"])
    assert set(payload) == {
        "query",
        "direction",
        "current_skill",
        "candidates",
    }
    assert payload["direction"] == "forward"
    assert [item["skill"]["id"] for item in payload["candidates"]] == [
        "skill-b",
        "skill-c",
    ]
    assert all(set(item) == {"candidate_id", "skill"} for item in payload["candidates"])
    assert "Write all user-visible natural-language fields in Simplified Chinese" in (
        llm.judge_calls[0]["system_prompt"]
    )
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

    payload = json.loads(llm.judge_calls[0]["user_content"])
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
    assert len(llm.judge_calls) == 3
    first_round_current_skills = {
        json.loads(call["user_content"])["current_skill"]["id"]
        for call in llm.judge_calls[:2]
    }
    assert first_round_current_skills == {"skill-a", "skill-c"}
    final_payload = json.loads(llm.judge_calls[-1]["user_content"])
    assert [item["skill"]["id"] for item in final_payload["candidates"]] == [
        "skill-d"
    ]
    assert result["decision"]["judge_cache_misses"] >= 3
    assert any("skill-d" in signature for signature in _plan_signatures(result))


async def test_beam_judges_same_candidate_separately_by_direction(tmp_path):
    artifacts = _artifacts(
        tmp_path,
        edges=[
            _edge("skill-a", "skill-shared", confidence=0.91),
            _edge("skill-shared", "skill-b", confidence=0.9),
        ],
    )
    llm = _FakeBeamLLM({"skill-shared": 0.9})

    result = await _planner(
        artifacts,
        llm,
        top_k=2,
        max_depth=2,
        candidate_skill_ids=["skill-a", "skill-b"],
    ).plan("use the shared skill")

    shared_calls = [
        json.loads(call["user_content"])
        for call in llm.judge_calls
        if any(
            candidate["skill"]["id"] == "skill-shared"
            for candidate in json.loads(call["user_content"])["candidates"]
        )
    ]
    assert result["llm_call_count"] == 2
    assert {payload["direction"] for payload in shared_calls} == {
        "forward",
        "backward",
    }


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
    graph = result["beam_search"]["graph"]
    assert sorted(node["id"] for node in graph["nodes"]) == [
        "skill-a",
        "skill-b",
        "skill-c",
    ]
    assert {
        (edge["source"], edge["target"])
        for edge in graph["edges"]
    } == {("skill-a", "skill-c"), ("skill-b", "skill-c")}
    assert {edge["status"] for edge in graph["edges"]} == {"final", "selected"}


async def test_beam_reuses_subtree_cache_after_reaching_same_skill(tmp_path):
    artifacts = _artifacts(
        tmp_path,
        edges=[
            _edge("skill-a", "skill-mid", confidence=0.91),
            _edge("skill-mid", "skill-shared", confidence=0.9),
            _edge("skill-b", "skill-shared", confidence=0.89),
            _edge("skill-shared", "skill-final", confidence=0.88),
        ],
    )
    llm = _FakeBeamLLM(
        {
            "skill-mid": 0.9,
            "skill-shared": 0.9,
            "skill-final": 0.9,
        }
    )

    result = await _planner(
        artifacts,
        llm,
        top_k=4,
        max_depth=4,
        candidate_skill_ids=["skill-a", "skill-b"],
    ).plan("complete through the shared skill")

    current_skills = [
        json.loads(call["user_content"])["current_skill"]["id"]
        for call in llm.judge_calls
    ]

    assert current_skills.count("skill-shared") == 1
    assert result["beam_search"]["subtree_cache_hits"] >= 1
    assert (
        "skill-a",
        "skill-mid",
        "skill-shared",
        "skill-final",
    ) in _plan_signatures(result)


def test_beam_subtree_cache_hit_preserves_parent_path(tmp_path):
    artifacts = _artifacts(
        tmp_path,
        edges=[
            _edge("skill-a", "skill-shared", confidence=0.91),
            _edge("skill-b", "skill-shared", confidence=0.9),
            _edge("skill-shared", "skill-final", confidence=0.89),
        ],
    )
    planner = _planner(
        artifacts,
        _FakeBeamLLM({}),
        top_k=3,
        max_depth=3,
        candidate_skill_ids=["skill-a", "skill-b"],
    )
    state = BeamState(
        skill_ids=("skill-b", "skill-shared"),
        edge_indices=(1,),
        available=frozenset(),
        judgement_scores=(1.0, 0.9),
        score_reasons=("shared is useful",),
        seed_skill_ids=("skill-a", "skill-b"),
        directions=frozenset({"forward"}),
    )

    cached_states = planner._states_from_subtree_cache(
        state=state,
        direction="forward",
        entries=(
            _subtree_entry(
                candidate_skill_id="skill-final",
                edge_index=2,
                judgement_score=0.8,
                reason="final is useful",
            ),
        ),
    )

    assert [item.skill_ids for item in cached_states] == [
        ("skill-b", "skill-shared", "skill-final")
    ]
    assert cached_states[0].edge_indices == (1, 2)
    assert cached_states[0].judgement_scores == (1.0, 0.9, 0.8)


def test_beam_subtree_cache_hit_skips_existing_candidate_to_avoid_cycle(tmp_path):
    artifacts = _artifacts(
        tmp_path,
        edges=[
            _edge("skill-a", "skill-b", confidence=0.91),
            _edge("skill-b", "skill-a", confidence=0.9),
        ],
    )
    planner = _planner(
        artifacts,
        _FakeBeamLLM({}),
        top_k=2,
        max_depth=3,
        candidate_skill_ids=["skill-a"],
    )
    state = BeamState(
        skill_ids=("skill-a", "skill-b"),
        edge_indices=(0,),
        available=frozenset(),
        judgement_scores=(1.0, 0.9),
        score_reasons=(),
        seed_skill_ids=("skill-a",),
        directions=frozenset({"forward"}),
    )

    cached_states = planner._states_from_subtree_cache(
        state=state,
        direction="forward",
        entries=(
            _subtree_entry(
                candidate_skill_id="skill-a",
                edge_index=1,
                judgement_score=0.9,
                reason="cycle",
            ),
        ),
    )

    assert cached_states == []


async def test_beam_effective_top_k_keeps_seed_plans_in_recommendations(tmp_path):
    artifacts = _artifacts(
        tmp_path,
        edges=[
            _edge("skill-a", "skill-x", confidence=0.91),
            _edge("skill-b", "skill-y", confidence=0.9),
            _edge("skill-c", "skill-z", confidence=0.89),
        ],
    )
    llm = _FakeBeamLLM({"skill-x": 0.0, "skill-y": 0.0, "skill-z": 0.0})

    result = await _planner(
        artifacts,
        llm,
        top_k=1,
        max_depth=2,
        candidate_skill_ids=["skill-a", "skill-b", "skill-c"],
    ).plan("keep all retrieved seeds visible")

    signatures = _plan_signatures(result)

    assert result["beam_search"]["top_k"] == 1
    assert result["beam_search"]["effective_top_k"] == 3
    assert len(result["recommended_plans"]) == 3
    assert {("skill-a",), ("skill-b",), ("skill-c",)} <= signatures


async def test_beam_final_rerank_selects_second_plan_as_primary(tmp_path):
    artifacts = _artifacts(
        tmp_path,
        edges=[
            _edge("skill-a", "skill-b", confidence=0.91),
            _edge("skill-c", "skill-d", confidence=0.9),
        ],
    )
    llm = _FakeBeamLLM(
        {"skill-b": 0.9, "skill-d": 0.9},
        rerank_response={
            "selected_plan_index": 2,
            "reason": "第二个更符合交付要求",
        },
    )

    result = await _planner(
        artifacts,
        llm,
        top_k=2,
        max_depth=2,
        candidate_skill_ids=["skill-a", "skill-c"],
    ).plan("choose the better branch")

    assert result["beam_search"]["final_rerank_applied"] is True
    assert result["beam_search"]["final_rerank_cross_plan_skill_merge"] is False
    assert result["recommended_plans"][0]["source"] == (
        "bidirectional_beam_final_rerank"
    )
    assert _plan_signature_from_dict(result["recommended_plans"][0]) == (
        "skill-c",
        "skill-d",
    )


async def test_beam_final_rerank_guard_rejects_uncompensated_seed_loss(tmp_path):
    artifacts = _artifacts(
        tmp_path,
        edges=[_edge("skill-a", "skill-b", confidence=0.95)],
    )
    llm = _FakeBeamLLM(
        {"skill-b": 1.0},
        rerank_response={
            "selected_plan_index": 1,
            "reason": "错误剪掉一个种子",
            "steps": [{"skill_id": "skill-a"}],
        },
    )

    result = await _planner(
        artifacts,
        llm,
        top_k=2,
        max_depth=2,
        candidate_skill_ids=["skill-a", "skill-b"],
    ).plan("guard seed loss")

    assert result["beam_search"]["final_rerank_applied"] is False
    assert result["beam_search"]["final_rerank_guard_applied"] is True
    assert "loses retrieval seed coverage" in (
        result["beam_search"]["final_rerank_guard_reason"]
    )
    assert _plan_signature_from_dict(result["recommended_plans"][0]) == (
        "skill-a",
        "skill-b",
    )


async def test_beam_final_rerank_guard_allows_graph_expansion_compensation(tmp_path):
    artifacts = _artifacts(
        tmp_path,
        edges=[
            _edge("skill-a", "skill-b", confidence=0.95),
            _edge("skill-a", "skill-c", confidence=0.71),
        ],
    )
    llm = _FakeBeamLLM(
        {"skill-b": 1.0, "skill-c": 0.7},
        rerank_response={
            "selected_plan_index": 1,
            "reason": "新增图扩展能力",
            "steps": [
                {"skill_id": "skill-a"},
                {"skill_id": "skill-c"},
            ],
            "can_feed_edges": [
                {"source_id": "skill-a", "target_id": "skill-c"}
            ],
        },
    )

    result = await _planner(
        artifacts,
        llm,
        top_k=2,
        max_depth=2,
        candidate_skill_ids=["skill-a", "skill-b"],
    ).plan("allow compensated seed loss")

    assert result["beam_search"]["final_rerank_applied"] is True
    assert result["beam_search"]["final_rerank_guard_applied"] is False
    assert _plan_signature_from_dict(result["recommended_plans"][0]) == (
        "skill-a",
        "skill-c",
    )


async def test_beam_final_rerank_invalid_selection_falls_back(tmp_path):
    artifacts = _artifacts(
        tmp_path,
        edges=[_edge("skill-a", "skill-b", confidence=0.91)],
    )
    llm = _FakeBeamLLM(
        {"skill-b": 0.9},
        rerank_response={
            "selected_plan_index": 1,
            "steps": [{"skill_id": "unknown-skill"}],
        },
    )

    result = await _planner(
        artifacts,
        llm,
        top_k=1,
        max_depth=2,
        candidate_skill_ids=["skill-a"],
    ).plan("fallback on invalid rerank")

    assert result["beam_search"]["final_rerank_applied"] is False
    assert "unknown-skill" in result["beam_search"]["final_rerank_error"]
    assert _plan_signature_from_dict(result["recommended_plans"][0]) == (
        "skill-a",
        "skill-b",
    )


async def test_beam_final_rerank_merges_skills_from_multiple_plans(tmp_path):
    artifacts = _artifacts(
        tmp_path,
        edges=[
            _edge("skill-a", "skill-x", confidence=0.91),
            _edge("skill-b", "skill-y", confidence=0.9),
        ],
    )
    llm = _FakeBeamLLM(
        {"skill-x": 0.0, "skill-y": 0.0},
        rerank_response={
            "selected_plan_index": 1,
            "reason": "合并两个核心种子",
            "steps": [
                {"skill_id": "skill-a", "reason": "核心输入"},
                {"skill_id": "skill-b", "reason": "核心输出"},
            ],
            "can_feed_edges": [
                {
                    "source_id": "skill-a",
                    "target_id": "skill-b",
                    "reason": "连接两个种子",
                }
            ],
        },
    )

    result = await _planner(
        artifacts,
        llm,
        top_k=2,
        max_depth=2,
        candidate_skill_ids=["skill-a", "skill-b"],
    ).plan("merge seed skills")

    primary = result["recommended_plans"][0]

    assert result["beam_search"]["final_rerank_applied"] is True
    assert result["beam_search"]["final_rerank_cross_plan_skill_merge"] is True
    assert _plan_signature_from_dict(primary) == ("skill-a", "skill-b")
    assert primary["can_feed_edges"][0]["method"] == "beam_final_rerank_inferred"
    assert not any(
        edge.get("method") == "beam_final_rerank_inferred"
        for edge in artifacts.graph["edges"]
    )


async def test_beam_final_rerank_merge_preserves_auxiliary_skill(tmp_path):
    artifacts = _artifacts(
        tmp_path,
        edges=[
            _edge("skill-a", "skill-helper", confidence=0.91),
            _edge("skill-b", "skill-y", confidence=0.9),
        ],
    )
    llm = _FakeBeamLLM(
        {"skill-helper": 0.9, "skill-y": 0.0},
        rerank_response={
            "selected_plan_index": 1,
            "reason": "保留辅助步骤",
            "steps": [
                {"skill_id": "skill-a", "reason": "核心输入"},
                {"skill_id": "skill-helper", "reason": "辅助处理"},
                {"skill_id": "skill-b", "reason": "核心输出"},
            ],
            "can_feed_edges": [
                {"source_id": "skill-a", "target_id": "skill-helper"},
                {"source_id": "skill-helper", "target_id": "skill-b"},
            ],
        },
    )

    result = await _planner(
        artifacts,
        llm,
        top_k=2,
        max_depth=2,
        candidate_skill_ids=["skill-a", "skill-b"],
    ).plan("merge and keep useful helper")

    primary = result["recommended_plans"][0]

    assert result["beam_search"]["final_rerank_applied"] is True
    assert _plan_signature_from_dict(primary) == (
        "skill-a",
        "skill-helper",
        "skill-b",
    )


async def test_beam_final_rerank_can_prune_duplicate_skill(tmp_path):
    artifacts = _artifacts(
        tmp_path,
        edges=[
            _edge("skill-a", "skill-helper", confidence=0.91),
            _edge("skill-a", "skill-helper-duplicate", confidence=0.9),
        ],
    )
    llm = _FakeBeamLLM(
        {"skill-helper": 0.9, "skill-helper-duplicate": 0.9},
        rerank_response={
            "selected_plan_index": 1,
            "reason": "删除重复能力",
            "steps": [
                {"skill_id": "skill-a", "reason": "核心输入"},
                {"skill_id": "skill-helper", "reason": "保留一个辅助能力"},
            ],
        },
    )

    result = await _planner(
        artifacts,
        llm,
        top_k=2,
        max_depth=2,
        candidate_skill_ids=["skill-a"],
    ).plan("dedupe similar helpers")

    assert result["beam_search"]["final_rerank_applied"] is True
    assert _plan_signature_from_dict(result["recommended_plans"][0]) == (
        "skill-a",
        "skill-helper",
    )


async def test_beam_final_rerank_backward_edge_falls_back(tmp_path):
    artifacts = _artifacts(
        tmp_path,
        edges=[_edge("skill-a", "skill-b", confidence=0.91)],
    )
    llm = _FakeBeamLLM(
        {"skill-b": 0.9},
        rerank_response={
            "selected_plan_index": 1,
            "steps": [
                {"skill_id": "skill-a"},
                {"skill_id": "skill-b"},
            ],
            "can_feed_edges": [
                {"source_id": "skill-b", "target_id": "skill-a"}
            ],
        },
    )

    result = await _planner(
        artifacts,
        llm,
        top_k=1,
        max_depth=2,
        candidate_skill_ids=["skill-a"],
    ).plan("reject backward edge")

    assert result["beam_search"]["final_rerank_applied"] is False
    assert "backward" in result["beam_search"]["final_rerank_error"]
    assert _plan_signature_from_dict(result["recommended_plans"][0]) == (
        "skill-a",
        "skill-b",
    )


async def test_beam_final_rerank_drops_self_loop_edge(tmp_path):
    artifacts = _artifacts(
        tmp_path,
        edges=[_edge("skill-a", "skill-b", confidence=0.91)],
    )
    llm = _FakeBeamLLM(
        {"skill-b": 0.9},
        rerank_response={
            "selected_plan_index": 1,
            "steps": [
                {"skill_id": "skill-a"},
                {"skill_id": "skill-b"},
            ],
            "can_feed_edges": [
                {"source_id": "skill-a", "target_id": "skill-a"},
                {"source_id": "skill-a", "target_id": "skill-b"},
            ],
        },
    )

    result = await _planner(
        artifacts,
        llm,
        top_k=1,
        max_depth=2,
        candidate_skill_ids=["skill-a"],
    ).plan("drop self loop")

    assert result["beam_search"]["final_rerank_applied"] is True
    assert [
        (edge["source_id"], edge["target_id"])
        for edge in result["recommended_plans"][0]["can_feed_edges"]
    ] == [("skill-a", "skill-b")]


async def test_beam_final_rerank_prompt_uses_compact_hits(tmp_path):
    artifacts = _artifacts(
        tmp_path,
        edges=[_edge("skill-a", "skill-b", confidence=0.91)],
        outputs_by_skill={
            "skill-b": [{"name": "workbook", "type": "xlsx"}],
        },
    )
    llm = _FakeBeamLLM({"skill-b": 0.9})

    await _planner(
        artifacts,
        llm,
        top_k=1,
        max_depth=2,
        candidate_skill_ids=["skill-a"],
    ).plan("Create report.xlsx")

    rerank_payload = json.loads(llm.rerank_calls[0]["user_content"])
    plan = rerank_payload["candidate_plans"][0]
    pool = rerank_payload["candidate_skill_pool"]

    assert "candidate_plans" in rerank_payload
    assert "candidate_skill_pool" in rerank_payload
    assert rerank_payload["retrieval_seed_skill_ids"] == ["skill-a"]
    assert {
        item["skill_id"]: {
            "is_retrieval_seed": item["is_retrieval_seed"],
            "is_graph_expanded": item["is_graph_expanded"],
            "produced_artifact_types": item["produced_artifact_types"],
        }
        for item in pool
    } == {
        "skill-a": {
            "is_retrieval_seed": True,
            "is_graph_expanded": False,
            "produced_artifact_types": ["text"],
        },
        "skill-b": {
            "is_retrieval_seed": False,
            "is_graph_expanded": True,
            "produced_artifact_types": ["xlsx"],
        },
    }
    assert "seed_hits" in plan
    assert "missing_seed_hits" not in plan
    assert plan["graph_expansion_hits"] == ["skill-b"]
    assert "output_hits" in plan
    assert "_state_score" not in llm.rerank_calls[0]["user_content"]
    assert "50 Chinese characters" in llm.rerank_calls[0]["system_prompt"]
    assert "Retrieval seed Skill IDs are strong evidence" in (
        llm.rerank_calls[0]["system_prompt"]
    )
    assert "Prefer preserving retrieval seed Skills" in (
        llm.rerank_calls[0]["system_prompt"]
    )
    assert "Drop a retrieval seed Skill only when it duplicates" in (
        llm.rerank_calls[0]["system_prompt"]
    )
    assert "data collection, computation, generation, presentation" in (
        llm.rerank_calls[0]["system_prompt"]
    )
    assert "graph-expanded Skills are additional" in (
        llm.rerank_calls[0]["system_prompt"]
    )
    assert "adds relevant graph-expanded Skills" in (
        llm.rerank_calls[0]["system_prompt"]
    )
    assert "slightly longer plan is acceptable" in (
        llm.rerank_calls[0]["system_prompt"]
    )
    assert "Preserve auxiliary Skills" in llm.rerank_calls[0]["system_prompt"]
    assert "similar-function duplicate Skills" in llm.rerank_calls[0]["system_prompt"]
    assert "Candidate plans are reference paths" in (
        llm.rerank_calls[0]["system_prompt"]
    )
    assert "Select final steps from candidate_skill_pool" in (
        llm.rerank_calls[0]["system_prompt"]
    )
    assert "parallel core Skills" in llm.rerank_calls[0]["system_prompt"]
    assert "missing can_feed edges do not" in llm.rerank_calls[0]["system_prompt"]
    assert "Do not replace a core delivery Skill" in (
        llm.rerank_calls[0]["system_prompt"]
    )
    assert "50 Chinese characters" in llm.judge_calls[0]["system_prompt"]


def test_beam_final_rerank_payload_keeps_extra_graph_expansion_candidates(tmp_path):
    artifacts = _artifacts(
        tmp_path,
        edges=[
            _edge("skill-seed", "skill-high", confidence=0.95),
            _edge("skill-seed", "skill-low", confidence=0.71),
        ],
    )
    planner = _planner(
        artifacts,
        _FakeBeamLLM({}),
        top_k=1,
        max_depth=2,
        candidate_skill_ids=["skill-seed"],
    )
    high_state = BeamState(
        skill_ids=("skill-seed", "skill-high"),
        edge_indices=(0,),
        available=frozenset(),
        judgement_scores=(1.0,),
        score_reasons=(),
        seed_skill_ids=("skill-seed",),
        directions=frozenset({"forward"}),
    )
    lower_state = BeamState(
        skill_ids=("skill-seed", "skill-low"),
        edge_indices=(1,),
        available=frozenset(),
        judgement_scores=(0.51,),
        score_reasons=(),
        seed_skill_ids=("skill-seed",),
        directions=frozenset({"forward"}),
    )

    final_states = planner._final_candidate_states(
        searched_states=[high_state, lower_state],
        seed_states=[],
    )
    plans = planner._plans_from_states(final_states)[: planner._final_candidate_limit()]
    rerank_payload = planner._final_rerank_payload(
        query="prefer recall",
        recommended_plans=[
            planner._plan_payload(plan, query="prefer recall")
            for plan in plans
        ],
        seed_skill_ids=("skill-seed",),
    )

    candidate_skills = [
        tuple(plan["skills"])
        for plan in rerank_payload["candidate_plans"]
    ]
    low_plan = next(
        plan
        for plan in rerank_payload["candidate_plans"]
        if plan["skills"] == ["skill-seed", "skill-low"]
    )

    assert planner._final_candidate_limit() > planner.effective_top_k
    assert ("skill-seed", "skill-high") in candidate_skills
    assert ("skill-seed", "skill-low") in candidate_skills
    assert low_plan["seed_hits"] == ["skill-seed"]
    assert low_plan["graph_expansion_hits"] == ["skill-low"]
    assert "missing_seed_hits" not in low_plan
    assert [item["skill_id"] for item in rerank_payload["candidate_skill_pool"]] == [
        "skill-high",
        "skill-low",
        "skill-seed",
    ]


def test_beam_ranking_prefers_broader_seed_coverage(tmp_path):
    artifacts = _artifacts(
        tmp_path,
        edges=[
            _edge("skill-a", "skill-c", confidence=0.9),
            _edge("skill-b", "skill-c", confidence=0.9),
            _edge("skill-d", "skill-e", confidence=0.9),
        ],
    )
    planner = _planner(
        artifacts,
        _FakeBeamLLM({}),
        top_k=1,
        max_depth=2,
        candidate_skill_ids=["skill-a", "skill-b", "skill-d"],
    )
    broader_seed_path = BeamState(
        skill_ids=("skill-a", "skill-b", "skill-c"),
        edge_indices=(0, 1),
        available=frozenset(),
        judgement_scores=(0.8,),
        score_reasons=(),
        seed_skill_ids=("skill-a", "skill-b", "skill-d"),
        directions=frozenset({"forward"}),
    )
    narrow_seed_path = BeamState(
        skill_ids=("skill-d", "skill-e"),
        edge_indices=(2,),
        available=frozenset(),
        judgement_scores=(0.8,),
        score_reasons=(),
        seed_skill_ids=("skill-a", "skill-b", "skill-d"),
        directions=frozenset({"forward"}),
    )

    ranked = planner._rank_and_trim(
        [narrow_seed_path, broader_seed_path],
        limit=1,
    )

    assert ranked == [broader_seed_path]
    assert planner._state_seed_coverage(broader_seed_path) == 2 / 3
    assert planner._state_seed_coverage(narrow_seed_path) == 1 / 3


def test_beam_state_score_uses_average_judgement_score(tmp_path):
    artifacts = _artifacts(
        tmp_path,
        edges=[_edge("skill-root", "skill-output", confidence=0.9)],
    )
    planner = _planner(
        artifacts,
        _FakeBeamLLM({}),
        top_k=1,
        max_depth=2,
        candidate_skill_ids=["skill-root"],
    )
    lower_judgement_path = BeamState(
        skill_ids=("skill-root", "skill-output"),
        edge_indices=(0,),
        available=frozenset(),
        judgement_scores=(0.6,),
        score_reasons=(),
        seed_skill_ids=("skill-root",),
        directions=frozenset({"forward"}),
    )
    higher_judgement_path = BeamState(
        skill_ids=("skill-root", "skill-output"),
        edge_indices=(0,),
        available=frozenset(),
        judgement_scores=(1.0, 0.8),
        score_reasons=(),
        seed_skill_ids=("skill-root",),
        directions=frozenset({"forward"}),
    )

    score_delta = (
        planner._state_score(higher_judgement_path)
        - planner._state_score(lower_judgement_path)
    )

    assert round(score_delta, 6) == round(0.30 * (0.9 - 0.6), 6)


def test_beam_ranking_prefers_required_xlsx_output(tmp_path):
    artifacts = _artifacts(
        tmp_path,
        edges=[
            _edge("skill-root", "skill-xlsx", confidence=0.9),
            _edge("skill-root", "skill-chart", confidence=0.9),
        ],
        outputs_by_skill={
            "skill-xlsx": [{"name": "workbook", "type": "xlsx"}],
            "skill-chart": [{"name": "chart", "type": "image"}],
        },
    )
    planner = _planner(
        artifacts,
        _FakeBeamLLM({}),
        top_k=1,
        max_depth=2,
        candidate_skill_ids=["skill-root"],
    )
    planner.required_output_types = _required_output_types_from_query(
        "Create portfolio_var.xlsx with risk metrics."
    )
    xlsx_path = BeamState(
        skill_ids=("skill-root", "skill-xlsx"),
        edge_indices=(0,),
        available=frozenset(),
        judgement_scores=(0.8,),
        score_reasons=(),
        seed_skill_ids=("skill-root",),
        directions=frozenset({"forward"}),
    )
    chart_path = BeamState(
        skill_ids=("skill-root", "skill-chart"),
        edge_indices=(1,),
        available=frozenset(),
        judgement_scores=(0.8,),
        score_reasons=(),
        seed_skill_ids=("skill-root",),
        directions=frozenset({"forward"}),
    )

    ranked = planner._rank_and_trim([chart_path, xlsx_path], limit=1)

    assert ranked == [xlsx_path]
    assert planner._state_output_coverage(xlsx_path) == 1.0
    assert planner._state_output_coverage(chart_path) == 0.0


def test_beam_ranking_prefers_html_and_image_output_coverage(tmp_path):
    artifacts = _artifacts(
        tmp_path,
        edges=[
            _edge("skill-root", "skill-html", confidence=0.9),
            _edge("skill-root", "skill-exhibit", confidence=0.9),
        ],
        outputs_by_skill={
            "skill-html": [{"name": "page", "type": "html"}],
            "skill-exhibit": [
                {"name": "page", "type": "html"},
                {"name": "chapter", "type": "image"},
            ],
        },
    )
    planner = _planner(
        artifacts,
        _FakeBeamLLM({}),
        top_k=1,
        max_depth=2,
        candidate_skill_ids=["skill-root"],
    )
    planner.required_output_types = _required_output_types_from_query(
        "Create index.html and chapter_1.png."
    )
    html_only_path = BeamState(
        skill_ids=("skill-root", "skill-html"),
        edge_indices=(0,),
        available=frozenset(),
        judgement_scores=(0.8,),
        score_reasons=(),
        seed_skill_ids=("skill-root",),
        directions=frozenset({"forward"}),
    )
    exhibit_path = BeamState(
        skill_ids=("skill-root", "skill-exhibit"),
        edge_indices=(1,),
        available=frozenset(),
        judgement_scores=(0.8,),
        score_reasons=(),
        seed_skill_ids=("skill-root",),
        directions=frozenset({"forward"}),
    )

    ranked = planner._rank_and_trim([html_only_path, exhibit_path], limit=1)

    assert planner.required_output_types == frozenset({"html", "image"})
    assert ranked == [exhibit_path]
    assert planner._state_output_coverage(exhibit_path) == 1.0
    assert planner._state_output_coverage(html_only_path) == 0.5


def test_beam_ranking_without_required_outputs_keeps_score_order(tmp_path):
    artifacts = _artifacts(
        tmp_path,
        edges=[
            _edge("skill-root", "skill-low", confidence=0.9),
            _edge("skill-root", "skill-high", confidence=0.9),
        ],
        outputs_by_skill={
            "skill-low": [{"name": "workbook", "type": "xlsx"}],
            "skill-high": [{"name": "chart", "type": "image"}],
        },
    )
    planner = _planner(
        artifacts,
        _FakeBeamLLM({}),
        top_k=1,
        max_depth=2,
        candidate_skill_ids=["skill-root"],
    )
    planner.required_output_types = _required_output_types_from_query(
        "Create a useful result."
    )
    lower_score_path = BeamState(
        skill_ids=("skill-root", "skill-low"),
        edge_indices=(0,),
        available=frozenset(),
        judgement_scores=(0.7,),
        score_reasons=(),
        seed_skill_ids=("skill-root",),
        directions=frozenset({"forward"}),
    )
    higher_score_path = BeamState(
        skill_ids=("skill-root", "skill-high"),
        edge_indices=(1,),
        available=frozenset(),
        judgement_scores=(0.9,),
        score_reasons=(),
        seed_skill_ids=("skill-root",),
        directions=frozenset({"forward"}),
    )

    ranked = planner._rank_and_trim([lower_score_path, higher_score_path], limit=1)

    assert planner.required_output_types == frozenset()
    assert ranked == [higher_score_path]


def test_unrequested_extra_output_types_do_not_increase_coverage(tmp_path):
    artifacts = _artifacts(
        tmp_path,
        edges=[
            _edge("skill-root", "skill-docx", confidence=0.9),
            _edge("skill-root", "skill-docx-extra", confidence=0.9),
        ],
        outputs_by_skill={
            "skill-docx": [{"name": "document", "type": "docx"}],
            "skill-docx-extra": [
                {"name": "document", "type": "docx"},
                {"name": "slides", "type": "pptx"},
                {"name": "workbook", "type": "xlsx"},
            ],
        },
    )
    planner = _planner(
        artifacts,
        _FakeBeamLLM({}),
        top_k=2,
        max_depth=2,
        candidate_skill_ids=["skill-root"],
    )
    planner.required_output_types = _required_output_types_from_query(
        "Create contract.docx."
    )
    docx_path = BeamState(
        skill_ids=("skill-root", "skill-docx"),
        edge_indices=(0,),
        available=frozenset(),
        judgement_scores=(0.8,),
        score_reasons=(),
        seed_skill_ids=("skill-root",),
        directions=frozenset({"forward"}),
    )
    extra_path = BeamState(
        skill_ids=("skill-root", "skill-docx-extra"),
        edge_indices=(1,),
        available=frozenset(),
        judgement_scores=(0.8,),
        score_reasons=(),
        seed_skill_ids=("skill-root",),
        directions=frozenset({"forward"}),
    )

    assert planner.required_output_types == frozenset({"docx"})
    assert planner._state_output_coverage(docx_path) == 1.0
    assert planner._state_output_coverage(extra_path) == 1.0


async def test_beam_progress_callback_receives_lightweight_graph_events(tmp_path):
    artifacts = _artifacts(
        tmp_path,
        edges=[
            _edge("skill-a", "skill-b", confidence=0.91),
            _edge("skill-a", "skill-c", confidence=0.88),
        ],
    )
    llm = _FakeBeamLLM({"skill-b": 0.9, "skill-c": 0.4})
    events = []

    async def progress_callback(event):
        events.append(event)

    result = await _planner(
        artifacts,
        llm,
        top_k=2,
        max_depth=2,
        candidate_skill_ids=["skill-a"],
        progress_callback=progress_callback,
    ).plan("compose an alpha plan")

    assert [event["event"] for event in events] == [
        "started",
        "candidates_found",
        "candidates_judged",
        "graph_merged",
        "completed",
    ]
    assert events == result["beam_search"]["events"]
    assert result["language"] == "cn"
    assert result["beam_search"]["language"] == "cn"
    assert all(event["language"] == "cn" for event in events)
    judged = events[2]["payload"]["candidates"]
    assert {item["status"] for item in judged} == {"selected", "rejected"}
    assert all("score" not in item and "reason" not in item for item in judged)


async def test_beam_judge_reason_uses_english_without_seed_copy(tmp_path):
    artifacts = _artifacts(
        tmp_path,
        edges=[_edge("skill-a", "skill-b", confidence=0.91)],
    )
    llm = _FakeBeamLLM({"skill-b": 0.9})

    result = await _planner(
        artifacts,
        llm,
        top_k=1,
        max_depth=2,
        candidate_skill_ids=["skill-a"],
        language="en",
    ).plan("compose a plan")

    prompt = llm.judge_calls[0]
    payload = json.loads(prompt["user_content"])
    assert "language" not in payload
    assert "language_instruction" not in payload
    assert "state" not in payload
    assert "Write all user-visible natural-language fields in English" in (
        prompt["system_prompt"]
    )
    assert result["reason"] == "skill-b is useful"


def _planner(
    artifacts: ScoreArtifacts,
    llm: _FakeBeamLLM,
    *,
    top_k: int,
    max_depth: int,
    candidate_skill_ids: list[str],
    progress_callback=None,
    language="cn",
) -> BidirectionalBeamPlanner:
    return BidirectionalBeamPlanner(
        artifacts,
        llm_config=None,
        llm_client=llm,
        min_edge_confidence=0.7,
        top_k=top_k,
        max_depth=max_depth,
        candidate_skill_ids=candidate_skill_ids,
        progress_callback=progress_callback,
        language=language,
    )


def _artifacts(
    tmp_path,
    *,
    edges: list[dict[str, object]],
    outputs_by_skill: dict[str, list[dict[str, object]]] | None = None,
) -> ScoreArtifacts:
    outputs_by_skill = outputs_by_skill or {}
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
                "outputs": outputs_by_skill.get(
                    current_skill_id,
                    [{"name": current_skill_id, "type": "text"}],
                ),
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


def _subtree_entry(
    *,
    candidate_skill_id: str,
    edge_index: int,
    judgement_score: float,
    reason: str,
) -> SubtreeCacheEntry:
    return SubtreeCacheEntry(
        candidate_skill_id=candidate_skill_id,
        edge_index=edge_index,
        judgement_score=judgement_score,
        reason=reason,
    )


def _plan_signatures(result: dict[str, object]) -> set[tuple[str, ...]]:
    return {
        tuple(step["skill_id"] for step in plan["steps"])
        for plan in result["recommended_plans"]
    }


def _plan_signature_from_dict(plan: dict[str, object]) -> tuple[str, ...]:
    return tuple(step["skill_id"] for step in plan["steps"])
