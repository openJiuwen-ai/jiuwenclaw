from jiuwenswarm.symphony.orchestration.planning.models import (
    ArtifactRef,
    GroundedQuery,
    OrchestrationPlan,
    PlanStep,
    SearchState,
)
from jiuwenswarm.symphony.orchestration.planning.plan_builder import (
    compose_dag_plans,
    compose_plan_group,
    plan_stages,
    state_to_plan,
    topological_step_ids,
)


def test_plan_stages_and_topological_sort_parallelize_roots():
    steps = [
        _step("review"),
        _step("draft"),
        _step("publish"),
    ]
    edges = [
        {"source_id": "draft", "target_id": "publish"},
        {"source_id": "review", "target_id": "publish"},
    ]

    stages = plan_stages(steps, edges)

    assert topological_step_ids({"draft", "review", "publish"}, edges) == [
        "draft",
        "review",
        "publish",
    ]
    assert [
        [skill["skill_id"] for skill in stage["skills"]]
        for stage in stages
    ] == [["draft", "review"], ["publish"]]


def test_state_to_plan_projects_inputs_outputs_and_edge_metadata():
    skills = {
        "draft": {
            "id": "draft",
            "name": "Draft",
            "inputs": [{"name": "brief", "type": "text", "required": True}],
            "outputs": [{"name": "draft", "type": "markdown"}],
        },
        "review": {
            "id": "review",
            "name": "Review",
            "inputs": [{"name": "draft", "type": "markdown", "required": True}],
            "outputs": [{"name": "review", "type": "markdown"}],
        },
    }
    edges = [
        {
            "source": "skill:draft",
            "target": "skill:review",
            "confidence": 0.92,
            "method": "llm",
            "evidence": {
                "reasons": ["draft feeds review"],
                "supporting_fields": {
                    "source_outputs": ["draft"],
                    "target_inputs": ["draft"],
                    "port_mappings": [
                        {"source_output": "draft", "target_input": "draft"}
                    ],
                },
            },
        }
    ]

    plan = state_to_plan(
        state=SearchState(
            skill_ids=("draft", "review"),
            available=frozenset({("brief", "text")}),
            edges=(0,),
        ),
        grounded=GroundedQuery(
            query="make a reviewed draft",
            available_artifacts=[ArtifactRef(name="brief", type="text")],
            seed_skill_ids=("review",),
        ),
        skill_by_id=skills,
        can_feed_edges=edges,
    )

    assert plan.status == "ready"
    assert [step.skill_id for step in plan.steps] == ["draft", "review"]
    assert plan.missing_inputs == []
    assert plan.consumed_user_artifacts == 1
    assert plan.produced_artifacts == [
        ArtifactRef(name="draft", type="markdown", source="skill_output"),
        ArtifactRef(name="review", type="markdown", source="skill_output"),
    ]
    assert plan.can_feed_edges == [
        {
            "source_id": "draft",
            "target_id": "review",
            "confidence": 0.92,
            "method": "llm",
            "port_mappings": [{"source_output": "draft", "target_input": "draft"}],
            "source_outputs": ["draft"],
            "target_inputs": ["draft"],
            "reasons": ["draft feeds review"],
        }
    ]


def test_compose_plan_group_merges_steps_edges_and_reorders_by_dependencies():
    draft = _step("draft")
    review = _step("review")
    publish = _step("publish")
    left = _plan(
        [publish, draft],
        [{"source_id": "draft", "target_id": "publish", "confidence": 0.8}],
    )
    right = _plan(
        [review, draft],
        [{"source_id": "draft", "target_id": "review", "confidence": 0.9}],
    )

    composed = compose_plan_group([left, right])

    assert [step.skill_id for step in composed.steps] == [
        "draft",
        "publish",
        "review",
    ]
    assert round(composed.edge_confidence, 2) == 0.85
    assert composed.status == "ready"


def test_compose_dag_plans_keeps_unrelated_seed_plans_separate():
    left = _plan(
        [_step("draft"), _step("review")],
        [{"source_id": "draft", "target_id": "review", "confidence": 0.8}],
    )
    right = _plan(
        [_step("outline"), _step("publish")],
        [{"source_id": "outline", "target_id": "publish", "confidence": 0.9}],
    )

    composed_plans = compose_dag_plans([left, right], max_plans=10)

    signatures = {
        tuple(step.skill_id for step in plan.steps) for plan in composed_plans
    }
    assert signatures == {("draft", "review"), ("outline", "publish")}


def test_compose_dag_plans_dedupes_edges_by_highest_confidence():
    low_confidence = _plan(
        [_step("draft"), _step("review")],
        [{"source_id": "draft", "target_id": "review", "confidence": 0.4}],
    )
    high_confidence = _plan(
        [_step("draft"), _step("review")],
        [{"source_id": "draft", "target_id": "review", "confidence": 0.9}],
    )

    composed_plans = compose_dag_plans(
        [low_confidence, high_confidence],
        max_plans=10,
    )

    assert len(composed_plans) == 1
    assert composed_plans[0].can_feed_edges == [
        {"source_id": "draft", "target_id": "review", "confidence": 0.9}
    ]


def _step(skill_id: str) -> PlanStep:
    return PlanStep(
        skill_id=skill_id,
        name=skill_id.title(),
        inputs=[],
        outputs=[],
    )


def _plan(
    steps: list[PlanStep],
    edges: list[dict[str, object]],
) -> OrchestrationPlan:
    return OrchestrationPlan(
        steps=steps,
        produced_artifacts=[],
        missing_inputs=[],
        can_feed_edges=edges,
        goal_score=1.0,
        edge_confidence=1.0,
        consumed_user_artifacts=0,
        status="ready",
        reasons=[],
    )
