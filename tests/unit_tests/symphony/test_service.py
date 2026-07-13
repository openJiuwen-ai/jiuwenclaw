import json
from types import SimpleNamespace

import pytest

from jiuwenswarm.symphony.config import SymphonyOrchestrationConfig
from jiuwenswarm.symphony.orchestration import service
from jiuwenswarm.symphony.orchestration.artifacts import ScoreArtifacts


class _FakeLLMClient:
    def __init__(self, response: dict):
        self.response = response
        self.calls = []

    async def complete_json_async(self, **kwargs):
        self.calls.append(kwargs)
        return json.dumps(self.response)


def _artifacts(tmp_path, *, graph_skill_prefix: bool = False):
    def graph_id(value: str) -> str:
        return f"skill:{value}" if graph_skill_prefix else value

    return ScoreArtifacts(
        score_dir=tmp_path,
        manifest={},
        skills=[
            {
                "id": "skill-a",
                "name": "Alpha Skill",
                "description": "Creates an alpha draft.",
                "inputs": [{"name": "brief", "type": "text", "required": True}],
                "outputs": [{"name": "draft", "type": "markdown"}],
            },
            {
                "id": "skill-b",
                "name": "Beta Skill",
                "description": "Reviews an alpha draft for beta quality.",
                "inputs": [{"name": "draft", "type": "markdown", "required": True}],
                "outputs": [{"name": "review", "type": "markdown"}],
            },
            {
                "id": "skill-c",
                "name": "Gamma Skill",
                "description": "Publishes a gamma report.",
                "inputs": [{"name": "review", "type": "markdown", "required": True}],
                "outputs": [{"name": "report", "type": "markdown"}],
            },
        ],
        graph={
            "edges": [
                {
                    "type": "can_feed",
                    "source": graph_id("skill-a"),
                    "target": graph_id("skill-b"),
                    "confidence": 0.91,
                    "method": "llm",
                    "evidence": {
                        "reasons": ["draft feeds review"],
                        "supporting_fields": {
                            "source_outputs": ["draft"],
                            "target_inputs": ["draft"],
                        },
                    },
                },
                {
                    "type": "can_feed",
                    "source": graph_id("skill-a"),
                    "target": graph_id("skill-c"),
                    "confidence": 0.2,
                    "method": "llm",
                    "evidence": {"reasons": ["weak relation"]},
                },
                {
                    "type": "can_feed",
                    "source": graph_id("skill-b"),
                    "target": graph_id("skill-c"),
                    "confidence": 0.88,
                    "method": "llm",
                    "evidence": {
                        "reasons": ["review feeds report"],
                        "supporting_fields": {
                            "source_outputs": ["review"],
                            "target_inputs": ["review"],
                        },
                    },
                },
            ]
        },
        lookup={},
    )


async def test_plan_from_score_fast_uses_one_shot_planner(monkeypatch, tmp_path):
    artifacts = _artifacts(tmp_path)
    llm = _FakeLLMClient(
        {
            "title": "Fast plan",
            "status": "ready",
            "reason": "Alpha feeds beta.",
            "steps": [
                {"skill_id": "skill-a", "reason": "Create draft."},
                {"skill_id": "skill-b", "reason": "Review draft."},
            ],
            "can_feed_edges": [
                {"source_id": "skill-a", "target_id": "skill-b"},
            ],
        }
    )

    monkeypatch.setattr(service, "load_score_artifacts", lambda score_dir: artifacts)

    result = await service.plan_from_score(
        tmp_path,
        "unrelated user request",
        llm_client=llm,
        orchestration_config=SymphonyOrchestrationConfig(
            mode="fast",
            top_k=2,
            min_edge_confidence=0.7,
        ),
    )

    assert len(llm.calls) == 1
    assert result["planning_mode"] == "one_shot_fast"
    assert result["llm_call_count"] == 1
    assert result["recommended_plans"][0]["title"] == "Fast plan"
    assert result["recommended_plans"][0]["steps"][0]["inputs"] == [
        {"name": "brief", "type": "text", "required": True}
    ]
    assert result["recommended_plans"][0]["steps"][0]["outputs"] == [
        {"name": "draft", "type": "markdown"}
    ]
    assert result["execution_graph"]["edges"][0]["source"] == "skill-a"

    prompt_payload = json.loads(llm.calls[0]["user_content"])
    assert prompt_payload["can_feed_edges"] == [
        {
            "source_id": "skill-a",
            "target_id": "skill-b",
        },
        {
            "source_id": "skill-b",
            "target_id": "skill-c",
        },
    ]
    assert all("inputs" not in skill for skill in prompt_payload["skills"])
    assert all("outputs" not in skill for skill in prompt_payload["skills"])


async def test_plan_from_score_fast_accepts_prefixed_graph_skill_ids(
    monkeypatch,
    tmp_path,
):
    artifacts = _artifacts(tmp_path, graph_skill_prefix=True)
    llm = _FakeLLMClient(
        {
            "title": "Fast plan",
            "status": "ready",
            "steps": [
                {"skill_id": "skill-a"},
                {"skill_id": "skill-b"},
            ],
            "can_feed_edges": [
                {"source_id": "skill-a", "target_id": "skill-b"},
            ],
        }
    )
    monkeypatch.setattr(service, "load_score_artifacts", lambda score_dir: artifacts)

    result = await service.plan_from_score(
        tmp_path,
        "prefixed graph",
        llm_client=llm,
        orchestration_config=SymphonyOrchestrationConfig(
            mode="fast",
            min_edge_confidence=0.7,
        ),
    )

    prompt_payload = json.loads(llm.calls[0]["user_content"])
    assert result["candidate_edge_count"] == 2
    assert prompt_payload["can_feed_edges"] == [
        {"source_id": "skill-a", "target_id": "skill-b"},
        {"source_id": "skill-b", "target_id": "skill-c"},
    ]
    assert result["recommended_plans"][0]["can_feed_edges"][0]["source_id"] == "skill-a"
    assert result["execution_graph"]["edges"][0]["source"] == "skill-a"


async def test_plan_from_score_fast_rejects_unknown_skill_once(monkeypatch, tmp_path):
    artifacts = _artifacts(tmp_path)
    llm = _FakeLLMClient(
        {
            "title": "Invalid",
            "status": "ready",
            "steps": [{"skill_id": "missing-skill"}],
        }
    )
    monkeypatch.setattr(service, "load_score_artifacts", lambda score_dir: artifacts)

    result = await service.plan_from_score(
        tmp_path,
        "unrelated user request",
        llm_client=llm,
        orchestration_config=SymphonyOrchestrationConfig(mode="fast"),
    )

    assert len(llm.calls) == 1
    assert result["success"] is False
    assert "unknown skill IDs" in result["detail"]
    assert result["execution_graph"]["nodes"] == []


async def test_plan_from_score_fast_no_plan_calls_llm_once(monkeypatch, tmp_path):
    artifacts = _artifacts(tmp_path)
    llm = _FakeLLMClient(
        {
            "title": "No useful plan",
            "status": "no_plan",
            "reason": "Candidates do not satisfy the request.",
            "steps": [],
            "can_feed_edges": [],
        }
    )
    monkeypatch.setattr(service, "load_score_artifacts", lambda score_dir: artifacts)

    result = await service.plan_from_score(
        tmp_path,
        "unrelated user request",
        llm_client=llm,
        orchestration_config=SymphonyOrchestrationConfig(mode="fast"),
    )

    assert len(llm.calls) == 1
    assert result["status"] == "no_plan"
    assert result["recommended_plans"] == []
    assert result["execution_graph"]["nodes"] == []


async def test_plan_from_score_fast_rejects_low_confidence_edge_once(
    monkeypatch,
    tmp_path,
):
    artifacts = _artifacts(tmp_path)
    llm = _FakeLLMClient(
        {
            "title": "Invalid edge",
            "status": "ready",
            "steps": [
                {"skill_id": "skill-a"},
                {"skill_id": "skill-c"},
            ],
            "can_feed_edges": [
                {"source_id": "skill-a", "target_id": "skill-c"},
            ],
        }
    )
    monkeypatch.setattr(service, "load_score_artifacts", lambda score_dir: artifacts)

    result = await service.plan_from_score(
        tmp_path,
        "unrelated user request",
        llm_client=llm,
        orchestration_config=SymphonyOrchestrationConfig(
            mode="fast",
            min_edge_confidence=0.7,
        ),
    )

    prompt_payload = json.loads(llm.calls[0]["user_content"])
    assert len(llm.calls) == 1
    assert prompt_payload["can_feed_edges"] == [
        {
            "source_id": "skill-a",
            "target_id": "skill-b",
        },
        {
            "source_id": "skill-b",
            "target_id": "skill-c",
        },
    ]
    assert result["success"] is False
    assert "illegal can_feed edges" in result["detail"]


async def test_plan_from_score_fast_uses_input_candidates_and_neighbors(
    monkeypatch,
    tmp_path,
):
    artifacts = _artifacts(tmp_path)
    artifacts.skills.extend(
        [
            {
                "id": "skill-d",
                "name": "Delta Skill",
                "description": "Unrelated high-confidence source.",
                "inputs": [],
                "outputs": [{"name": "delta", "type": "markdown"}],
            },
            {
                "id": "skill-e",
                "name": "Echo Skill",
                "description": "Unrelated high-confidence target.",
                "inputs": [{"name": "delta", "type": "markdown"}],
                "outputs": [{"name": "echo", "type": "markdown"}],
            },
        ]
    )
    artifacts.graph["edges"].append(
        {
            "type": "can_feed",
            "source": "skill-d",
            "target": "skill-e",
            "confidence": 0.99,
            "method": "llm",
            "evidence": {"reasons": ["unrelated"]},
        }
    )
    llm = _FakeLLMClient(
        {
            "title": "Retrieved fast plan",
            "status": "ready",
            "steps": [{"skill_id": "skill-b", "reason": "Retrieved seed."}],
            "can_feed_edges": [],
        }
    )
    monkeypatch.setattr(service, "load_score_artifacts", lambda score_dir: artifacts)

    result = await service.plan_from_score(
        tmp_path,
        "use beta",
        llm_client=llm,
        orchestration_config=SymphonyOrchestrationConfig(
            mode="fast",
            min_edge_confidence=0.7,
        ),
        candidate_skill_ids=["skill-b"],
    )

    prompt_payload = json.loads(llm.calls[0]["user_content"])
    prompt_skill_ids = {skill["id"] for skill in prompt_payload["skills"]}
    assert prompt_skill_ids == {"skill-a", "skill-b", "skill-c"}
    assert "skill-d" not in prompt_skill_ids
    assert result["skill_retrieval"]["source"] == "input"
    assert result["skill_retrieval"]["used"] is True
    assert result["skill_retrieval"]["candidate_skill_ids"] == ["skill-b"]


async def test_plan_from_score_filters_disabled_skills_from_prompt(
    monkeypatch,
    tmp_path,
):
    artifacts = _artifacts(tmp_path)
    llm = _FakeLLMClient(
        {
            "title": "Enabled-only plan",
            "status": "ready",
            "steps": [{"skill_id": "skill-a", "reason": "Enabled seed."}],
            "can_feed_edges": [],
        }
    )
    monkeypatch.setattr(service, "load_score_artifacts", lambda score_dir: artifacts)

    result = await service.plan_from_score(
        tmp_path,
        "use beta",
        llm_client=llm,
        orchestration_config=SymphonyOrchestrationConfig(
            mode="fast",
            min_edge_confidence=0.7,
        ),
        candidate_skill_ids=["skill-b"],
        disabled_skill_names=["Beta Skill"],
    )

    prompt_payload = json.loads(llm.calls[0]["user_content"])
    prompt_skill_ids = {skill["id"] for skill in prompt_payload["skills"]}
    assert prompt_skill_ids == {"skill-a", "skill-c"}
    assert prompt_payload["can_feed_edges"] == []
    assert result["skill_retrieval"] == {
        "source": "input",
        "used": False,
        "candidate_skill_ids": [],
        "candidate_count": 0,
        "fallback_reason": "candidate_skill_ids did not match current score",
    }


async def test_plan_from_score_fast_without_candidates_uses_default_subgraph(
    monkeypatch,
    tmp_path,
):
    artifacts = _artifacts(tmp_path)
    artifacts.skills.extend(
        [
            {
                "id": "skill-d",
                "name": "Delta Skill",
                "description": "Unrelated high-confidence source.",
                "inputs": [],
                "outputs": [{"name": "delta", "type": "markdown"}],
            },
            {
                "id": "skill-e",
                "name": "Echo Skill",
                "description": "Unrelated high-confidence target.",
                "inputs": [{"name": "delta", "type": "markdown"}],
                "outputs": [{"name": "echo", "type": "markdown"}],
            },
        ]
    )
    artifacts.graph["edges"].append(
        {
            "type": "can_feed",
            "source": "skill-d",
            "target": "skill-e",
            "confidence": 0.99,
            "method": "llm",
            "evidence": {"reasons": ["unrelated"]},
        }
    )
    llm = _FakeLLMClient(
        {
            "title": "Default fast plan",
            "status": "ready",
            "steps": [{"skill_id": "skill-d", "reason": "Default seed."}],
            "can_feed_edges": [],
        }
    )
    monkeypatch.setattr(service, "load_score_artifacts", lambda score_dir: artifacts)

    result = await service.plan_from_score(
        tmp_path,
        "use beta",
        llm_client=llm,
        orchestration_config=SymphonyOrchestrationConfig(
            mode="fast",
            min_edge_confidence=0.7,
        ),
    )

    prompt_payload = json.loads(llm.calls[0]["user_content"])
    prompt_skill_ids = {skill["id"] for skill in prompt_payload["skills"]}
    assert {"skill-d", "skill-e"}.issubset(prompt_skill_ids)
    assert result["skill_retrieval"] == {
        "source": "input",
        "used": False,
        "candidate_skill_ids": [],
        "candidate_count": 0,
        "fallback_reason": "candidate_skill_ids not provided",
    }


@pytest.mark.parametrize(
    ("candidate_skill_ids", "fallback_reason"),
    [
        ([], "candidate_skill_ids is empty"),
        (
            ["missing-skill", "missing-skill", ""],
            "candidate_skill_ids did not match current score",
        ),
    ],
)
async def test_plan_from_score_fast_falls_back_for_empty_or_unknown_candidates(
    monkeypatch,
    tmp_path,
    candidate_skill_ids,
    fallback_reason,
):
    artifacts = _artifacts(tmp_path)
    llm = _FakeLLMClient(
        {
            "title": "Fallback fast plan",
            "status": "ready",
            "steps": [{"skill_id": "skill-a", "reason": "Fallback seed."}],
            "can_feed_edges": [],
        }
    )
    monkeypatch.setattr(service, "load_score_artifacts", lambda score_dir: artifacts)

    result = await service.plan_from_score(
        tmp_path,
        "use beta",
        llm_client=llm,
        orchestration_config=SymphonyOrchestrationConfig(
            mode="fast",
            min_edge_confidence=0.7,
        ),
        candidate_skill_ids=candidate_skill_ids,
    )

    prompt_payload = json.loads(llm.calls[0]["user_content"])
    prompt_skill_ids = {skill["id"] for skill in prompt_payload["skills"]}
    assert prompt_skill_ids == {"skill-a", "skill-b", "skill-c"}
    assert result.get("success") is not False
    assert result["skill_retrieval"] == {
        "source": "input",
        "used": False,
        "candidate_skill_ids": [],
        "candidate_count": 0,
        "fallback_reason": fallback_reason,
    }


async def test_plan_from_score_rejects_non_fast_mode(monkeypatch, tmp_path):
    artifacts = _artifacts(tmp_path)
    monkeypatch.setattr(service, "load_score_artifacts", lambda score_dir: artifacts)

    with pytest.raises(ValueError, match="Unsupported orchestration mode"):
        await service.plan_from_score(
            tmp_path,
            "beam plan",
            llm_client=object(),
            ranker=object(),
            orchestration_config=SymphonyOrchestrationConfig(mode="beam"),
        )


def test_orchestration_skill_retrieval_filters_unknown_ids(
    monkeypatch,
    tmp_path,
):
    from jiuwenswarm.symphony.orchestration import skill_retrieval as retrieval_module

    artifacts = _artifacts(tmp_path)
    settings = SimpleNamespace(
        enabled=True,
        llm=SimpleNamespace(model="model", api_key="key"),
        retrieve=SimpleNamespace(top_k=2),
    )
    result = SimpleNamespace(
        candidate_records=[
            {"rank": 1, "worker_id": "missing-skill", "score": 0.9},
            {"rank": 2, "worker_id": "skill-b", "score": 0.8},
        ],
        payloads=["missing-skill", "skill-b"],
    )
    monkeypatch.setattr(retrieval_module, "load_settings", lambda: settings)
    monkeypatch.setattr(
        retrieval_module,
        "_skill_retrieval_status",
        lambda: {
            "index_exists": True,
            "fresh": True,
            "index_dir": str(tmp_path),
        },
    )
    monkeypatch.setattr(
        retrieval_module,
        "run_structured_skill_retrieve",
        lambda **kwargs: result,
    )

    selection = retrieval_module.select_orchestration_skill_candidates(
        query="use beta",
        artifacts=artifacts,
    )

    assert selection.used is True
    assert selection.candidate_skill_ids == ("skill-b",)
    assert selection.candidate_count == 1


def test_orchestration_skill_retrieval_falls_back_when_all_ids_are_stale(
    monkeypatch,
    tmp_path,
):
    from jiuwenswarm.symphony.orchestration import skill_retrieval as retrieval_module

    artifacts = _artifacts(tmp_path)
    settings = SimpleNamespace(
        enabled=True,
        llm=SimpleNamespace(model="model", api_key="key"),
        retrieve=SimpleNamespace(top_k=1),
    )
    result = SimpleNamespace(
        candidate_records=[{"rank": 1, "worker_id": "missing-skill"}],
        payloads=["missing-skill"],
    )
    monkeypatch.setattr(retrieval_module, "load_settings", lambda: settings)
    monkeypatch.setattr(
        retrieval_module,
        "_skill_retrieval_status",
        lambda: {
            "index_exists": True,
            "fresh": True,
            "index_dir": str(tmp_path),
        },
    )
    monkeypatch.setattr(
        retrieval_module,
        "run_structured_skill_retrieve",
        lambda **kwargs: result,
    )

    selection = retrieval_module.select_orchestration_skill_candidates(
        query="use beta",
        artifacts=artifacts,
    )

    assert selection.used is False
    assert selection.candidate_skill_ids == ()
    assert "no candidates present" in selection.fallback_reason


def test_orchestration_skill_retrieval_falls_back_when_index_missing(
    monkeypatch,
    tmp_path,
):
    from jiuwenswarm.symphony.orchestration import skill_retrieval as retrieval_module

    artifacts = _artifacts(tmp_path)
    settings = SimpleNamespace(
        enabled=True,
        llm=SimpleNamespace(model="model", api_key="key"),
    )
    monkeypatch.setattr(retrieval_module, "load_settings", lambda: settings)
    monkeypatch.setattr(
        retrieval_module,
        "_skill_retrieval_status",
        lambda: {"index_exists": False, "fresh": False},
    )

    selection = retrieval_module.select_orchestration_skill_candidates(
        query="use beta",
        artifacts=artifacts,
    )

    assert selection.used is False
    assert selection.fallback_reason == "skill retrieval index does not exist"


def test_orchestration_skill_retrieval_falls_back_when_llm_config_missing(
    monkeypatch,
    tmp_path,
):
    from jiuwenswarm.symphony.orchestration import skill_retrieval as retrieval_module

    artifacts = _artifacts(tmp_path)
    settings = SimpleNamespace(
        enabled=True,
        llm=SimpleNamespace(model="", api_key=""),
    )
    monkeypatch.setattr(retrieval_module, "load_settings", lambda: settings)
    monkeypatch.setattr(
        retrieval_module,
        "_skill_retrieval_status",
        lambda: {
            "index_exists": True,
            "fresh": True,
            "index_dir": str(tmp_path),
        },
    )

    selection = retrieval_module.select_orchestration_skill_candidates(
        query="use beta",
        artifacts=artifacts,
    )

    assert selection.used is False
    assert selection.fallback_reason == "skill retrieval LLM config is missing"


def test_orchestration_skill_retrieval_falls_back_when_retrieve_raises(
    monkeypatch,
    tmp_path,
):
    from jiuwenswarm.symphony.orchestration import skill_retrieval as retrieval_module

    artifacts = _artifacts(tmp_path)
    settings = SimpleNamespace(
        enabled=True,
        llm=SimpleNamespace(model="model", api_key="key"),
    )

    def raise_retrieve(**kwargs):
        del kwargs
        raise RuntimeError("boom")

    monkeypatch.setattr(retrieval_module, "load_settings", lambda: settings)
    monkeypatch.setattr(
        retrieval_module,
        "_skill_retrieval_status",
        lambda: {
            "index_exists": True,
            "fresh": True,
            "index_dir": str(tmp_path),
        },
    )
    monkeypatch.setattr(
        retrieval_module,
        "run_structured_skill_retrieve",
        raise_retrieve,
    )

    selection = retrieval_module.select_orchestration_skill_candidates(
        query="use beta",
        artifacts=artifacts,
    )

    assert selection.used is False
    assert selection.fallback_reason == "skill retrieval failed: boom"
