# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for the scheduler-review-feedback to Skill-evolution bridge."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openjiuwen.agent_evolving.checkpointing import EvolutionStore
from openjiuwen.agent_evolving.trajectory import (
    ToolCallDetail,
    TrajectoryStep,
    trajectory_from_steps,
)
from openjiuwen.core.session.stream import OutputSchema

from jiuwenswarm.agents.swarm.review_feedback_evolution import (
    SwarmReviewFeedbackEvolutionHandler,
)


class _AttributionLLM:
    def __init__(self, payload: dict) -> None:
        self._payload = payload
        self.calls = 0

    async def invoke(self, **_kwargs):
        self.calls += 1
        return {"content": json.dumps(self._payload)}


class _SequenceAttributionLLM:
    def __init__(self, payloads: list[dict]) -> None:
        self._payloads = payloads
        self.calls = 0

    async def invoke(self, **_kwargs):
        payload = self._payloads[self.calls]
        self.calls += 1
        return {"content": json.dumps(payload)}


class _TrajectoryRegistry:
    def __init__(self, trajectory) -> None:
        self.trajectory = trajectory
        self.calls: list[dict] = []

    def get_trajectory(self, **kwargs):
        self.calls.append(kwargs)
        return self.trajectory


def _trajectory(skill_md: str | None):
    steps = []
    if skill_md is not None:
        steps.append(
            TrajectoryStep(
                kind="tool",
                detail=ToolCallDetail(
                    tool_name="read_file",
                    call_args={"path": skill_md},
                    call_result="# xlsx",
                ),
            )
        )
    return trajectory_from_steps(
        execution_id="trace-1", steps=steps, session_id="sess-1"
    )


def _rail(tmp_path, llm):
    skill_dir = tmp_path / "xlsx"
    skill_dir.mkdir(exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: xlsx\ndescription: Build spreadsheets\n---\n\nValidate workbook output.\n",
        encoding="utf-8",
    )
    return SimpleNamespace(
        evolution_store=EvolutionStore(str(tmp_path)),
        evolver=SimpleNamespace(llm=llm, model="test-model"),
        auto_save=False,
        evolve_from_external_signals=AsyncMock(
            return_value=SimpleNamespace(
                skill_name="xlsx",
                status="staged",
                request=SimpleNamespace(request_id="skill_evolve_1"),
            )
        ),
        drain_pending_approval_events=AsyncMock(return_value=[]),
    )


@pytest.mark.asyncio
async def test_failed_review_evolves_member_then_team_completion_promotes_global(
    tmp_path, monkeypatch
):
    llm = _AttributionLLM(
        {
            "classification": "skill_issue",
            "skill_name": "xlsx",
            "target": "body",
            "reason": "output validation guidance is incomplete",
            "reusable_guidance": "Reopen and validate the workbook before delivery.",
            "is_reusable": True,
            "confidence": 0.93,
        }
    )
    global_rail = _rail(tmp_path, llm)
    member_rail = _rail(tmp_path, llm)
    manager = SimpleNamespace(
        get_review_feedback_skill_rail=lambda _session_id: global_rail
    )
    monkeypatch.setattr(
        "jiuwenswarm.agents.harness.team.team_manager.get_team_manager",
        lambda _channel: manager,
    )
    registry = _TrajectoryRegistry(_trajectory(str(tmp_path / "xlsx" / "SKILL.md")))
    handler = SwarmReviewFeedbackEvolutionHandler(
        channel_id="web",
        session_id="sess-1",
        team_id="team-1",
        trajectory_registry=registry,
        config={
            "react": {
                "evolution": {
                    "skill_evolution": True,
                    "review_feedback_min_confidence": 0.7,
                }
            }
        },
    )
    monkeypatch.setattr(
        handler, "_member_rail_for", lambda _assignee, _global: member_rail
    )

    await handler(
        {
            "task_id": "task-1",
            "review_round": 1,
            "task_title": "Create a workbook",
            "task_content": "Build formulas and formatting",
            "assignee": "worker-1",
            "feedback": "The delivered workbook was never reopened or validated.",
        }
    )

    member_rail.evolve_from_external_signals.assert_awaited_once()
    member_call = member_rail.evolve_from_external_signals.await_args.kwargs
    assert member_call["signals"][0].skill_name == "xlsx"
    assert member_call["requires_approval"] is False
    global_rail.evolve_from_external_signals.assert_not_awaited()

    assert await handler.on_team_completed() is True

    global_rail.evolve_from_external_signals.assert_awaited_once()
    global_call = global_rail.evolve_from_external_signals.await_args.kwargs
    assert global_call["signals"][0].skill_name == "xlsx"
    assert global_call["requires_approval"] is True
    assert "task=task-1" in global_call["user_query"]
    # The terminal pass advances its cursor and is idempotent until new task
    # feedback arrives.
    assert await handler.on_team_completed() is False
    global_rail.evolve_from_external_signals.assert_awaited_once()
    assert registry.calls == [
        {"team_id": "team-1", "session_id": "sess-1", "filter_collaborative": False},
        {"team_id": "team-1", "session_id": "sess-1", "filter_collaborative": False},
    ]


@pytest.mark.asyncio
async def test_failed_review_without_skill_read_never_evolves_installed_skill(
    tmp_path, monkeypatch
):
    llm = _AttributionLLM(
        {
            "classification": "skill_issue",
            "skill_name": "xlsx",
            "target": "body",
            "reason": "guidance is incomplete",
            "reusable_guidance": "Add validation guidance.",
            "is_reusable": True,
            "confidence": 0.99,
        }
    )
    global_rail = _rail(tmp_path, llm)
    member_rail = _rail(tmp_path, llm)
    manager = SimpleNamespace(
        get_review_feedback_skill_rail=lambda _session_id: global_rail
    )
    monkeypatch.setattr(
        "jiuwenswarm.agents.harness.team.team_manager.get_team_manager",
        lambda _channel: manager,
    )
    handler = SwarmReviewFeedbackEvolutionHandler(
        channel_id="web",
        session_id="sess-1",
        team_id="team-1",
        trajectory_registry=_TrajectoryRegistry(_trajectory(None)),
        config={"react": {"evolution": {"skill_evolution": True}}},
    )
    monkeypatch.setattr(
        handler, "_member_rail_for", lambda _assignee, _global: member_rail
    )

    await handler(
        {
            "task_id": "task-1",
            "review_round": 1,
            "assignee": "worker-1",
            "feedback": "The workbook output was not validated.",
        }
    )

    assert llm.calls == 1
    member_rail.evolve_from_external_signals.assert_not_awaited()
    global_rail.evolve_from_external_signals.assert_not_awaited()


@pytest.mark.asyncio
async def test_team_completion_groups_all_task_feedback_for_the_same_global_skill(
    tmp_path, monkeypatch
):
    llm = _AttributionLLM(
        {
            "classification": "skill_issue",
            "skill_name": "xlsx",
            "target": "body",
            "reason": "validation guidance is incomplete",
            "reusable_guidance": "Validate the workbook before delivery.",
            "is_reusable": True,
            "confidence": 0.95,
        }
    )
    global_rail = _rail(tmp_path, llm)
    member_rail = _rail(tmp_path, llm)
    manager = SimpleNamespace(
        get_review_feedback_skill_rail=lambda _session_id: global_rail
    )
    monkeypatch.setattr(
        "jiuwenswarm.agents.harness.team.team_manager.get_team_manager",
        lambda _channel: manager,
    )
    handler = SwarmReviewFeedbackEvolutionHandler(
        channel_id="web",
        session_id="sess-1",
        team_id="team-1",
        trajectory_registry=_TrajectoryRegistry(
            _trajectory(str(tmp_path / "xlsx" / "SKILL.md"))
        ),
        config={"react": {"evolution": {"skill_evolution": True}}},
    )
    monkeypatch.setattr(
        handler, "_member_rail_for", lambda _assignee, _global: member_rail
    )

    for task_id, feedback in (
        ("task-1", "Workbook one was not validated."),
        ("task-2", "Workbook two was not reopened."),
    ):
        await handler(
            {
                "task_id": task_id,
                "review_round": 1,
                "assignee": "worker-1",
                "feedback": feedback,
            }
        )

    assert member_rail.evolve_from_external_signals.await_count == 2
    assert await handler.on_team_completed() is True
    global_call = global_rail.evolve_from_external_signals.await_args.kwargs
    assert len(global_call["signals"]) == 2
    assert "task=task-1" in global_call["user_query"]
    assert "task=task-2" in global_call["user_query"]


@pytest.mark.asyncio
async def test_repeated_unattributed_pattern_routes_to_skill_creation_rail(
    tmp_path, monkeypatch
):
    llm = _AttributionLLM(
        {
            "classification": "new_skill_pattern",
            "skill_name": "",
            "target": None,
            "reason": "the same release recovery workflow is missing across tasks",
            "reusable_guidance": "Create a reusable release recovery checklist.",
            "is_reusable": True,
            "confidence": 0.91,
        }
    )
    global_rail = _rail(tmp_path, llm)
    member_rail = _rail(tmp_path, llm)
    approval_event = OutputSchema(
        type="chat.ask_user_question",
        index=0,
        payload={
            "request_id": "skill_create_1",
            "source": "skill_creation_approval",
            "questions": [],
        },
    )
    creation_rail = SimpleNamespace(
        propose_from_external_evidence=AsyncMock(return_value=True),
        drain_pending_approval_events=AsyncMock(return_value=[approval_event]),
    )
    manager = SimpleNamespace(
        get_review_feedback_skill_rail=lambda _session_id: global_rail,
        get_team_skill_create_rail=lambda _session_id: creation_rail,
        broadcast_event=AsyncMock(),
    )
    monkeypatch.setattr(
        "jiuwenswarm.agents.harness.team.team_manager.get_team_manager",
        lambda _channel: manager,
    )
    handler = SwarmReviewFeedbackEvolutionHandler(
        channel_id="web",
        session_id="sess-1",
        team_id="team-1",
        trajectory_registry=_TrajectoryRegistry(_trajectory(None)),
        config={
            "react": {
                "evolution": {
                    "skill_evolution": True,
                    "review_feedback_min_confidence": 0.7,
                }
            }
        },
    )
    monkeypatch.setattr(
        handler, "_member_rail_for", lambda _assignee, _global: member_rail
    )

    for task_id in ("task-a", "task-b"):
        await handler(
            {
                "task_id": task_id,
                "review_round": 1,
                "assignee": f"worker-{task_id[-1]}",
                "feedback": "The release plan omitted the recovery policy.",
            }
        )

    assert llm.calls == 2
    member_rail.evolve_from_external_signals.assert_not_awaited()
    global_rail.evolve_from_external_signals.assert_not_awaited()

    assert await handler.on_team_completed() is True
    creation_rail.propose_from_external_evidence.assert_awaited_once()
    creation_call = creation_rail.propose_from_external_evidence.await_args.kwargs
    assert (
        creation_call["proposal_key"] == "create-a-reusable-release-recovery-checklist"
    )
    assert len(creation_call["evidence"]) == 2
    assert "task=task-a" in creation_call["evidence"][0]
    assert "task=task-b" in creation_call["evidence"][1]
    creation_rail.drain_pending_approval_events.assert_awaited_once_with(wait=False)
    manager.broadcast_event.assert_awaited_once()
    assert manager.broadcast_event.call_args.args[0] == "sess-1"
    assert (
        manager.broadcast_event.call_args.args[1]["source"] == "skill_creation_approval"
    )
    assert await handler.on_team_completed() is False


@pytest.mark.asyncio
async def test_new_skill_repetition_only_groups_matching_guidance(
    tmp_path, monkeypatch
):
    llm = _SequenceAttributionLLM(
        [
            {
                "classification": "new_skill_pattern",
                "skill_name": "",
                "target": None,
                "reason": "release recovery is missing",
                "reusable_guidance": "Create a reusable release recovery checklist.",
                "is_reusable": True,
                "confidence": 0.91,
            },
            {
                "classification": "new_skill_pattern",
                "skill_name": "",
                "target": None,
                "reason": "audit retention is missing",
                "reusable_guidance": "Create a reusable audit retention checklist.",
                "is_reusable": True,
                "confidence": 0.92,
            },
            {
                "classification": "new_skill_pattern",
                "skill_name": "",
                "target": None,
                "reason": "audit retention is missing again",
                "reusable_guidance": "Create a reusable audit retention checklist.",
                "is_reusable": True,
                "confidence": 0.93,
            },
        ]
    )
    global_rail = _rail(tmp_path, llm)
    member_rail = _rail(tmp_path, llm)
    creation_rail = SimpleNamespace(
        propose_from_external_evidence=AsyncMock(return_value=True),
        drain_pending_approval_events=AsyncMock(return_value=[]),
    )
    manager = SimpleNamespace(
        get_review_feedback_skill_rail=lambda _session_id: global_rail,
        get_team_skill_create_rail=lambda _session_id: creation_rail,
        broadcast_event=AsyncMock(),
    )
    monkeypatch.setattr(
        "jiuwenswarm.agents.harness.team.team_manager.get_team_manager",
        lambda _channel: manager,
    )
    handler = SwarmReviewFeedbackEvolutionHandler(
        channel_id="web",
        session_id="sess-1",
        team_id="team-1",
        trajectory_registry=_TrajectoryRegistry(_trajectory(None)),
        config={
            "react": {
                "evolution": {
                    "skill_evolution": True,
                    "review_feedback_min_confidence": 0.7,
                }
            }
        },
    )
    monkeypatch.setattr(
        handler, "_member_rail_for", lambda _assignee, _global: member_rail
    )

    for task_id in ("task-a", "task-b", "task-c"):
        await handler(
            {
                "task_id": task_id,
                "review_round": 1,
                "assignee": f"worker-{task_id[-1]}",
                "feedback": f"Missing reusable workflow in {task_id}.",
            }
        )

    assert await handler.on_team_completed() is True
    creation_rail.propose_from_external_evidence.assert_awaited_once()
    creation_call = creation_rail.propose_from_external_evidence.await_args.kwargs
    assert (
        creation_call["proposal_key"] == "create-a-reusable-audit-retention-checklist"
    )
    assert len(creation_call["evidence"]) == 2
    assert "task=task-b" in creation_call["evidence"][0]
    assert "task=task-c" in creation_call["evidence"][1]
    assert all("task=task-a" not in item for item in creation_call["evidence"])


@pytest.mark.asyncio
async def test_pending_nonapproval_event_is_awaited(tmp_path, monkeypatch):
    llm = _AttributionLLM({})
    global_rail = _rail(tmp_path, llm)
    manager = SimpleNamespace(broadcast_event=AsyncMock())
    monkeypatch.setattr(
        "jiuwenswarm.agents.harness.team.team_manager.get_team_manager",
        lambda _channel: manager,
    )
    handler = SwarmReviewFeedbackEvolutionHandler(
        channel_id="web",
        session_id="sess-1",
        team_id="team-1",
        trajectory_registry=_TrajectoryRegistry(_trajectory(None)),
        config={"react": {"evolution": {"skill_evolution": True}}},
    )
    global_rail.drain_pending_approval_events = AsyncMock(
        return_value=[{"event_type": "chat.delta", "content": "analysis complete"}]
    )

    await handler._push_pending_events(global_rail)

    manager.broadcast_event.assert_awaited_once_with(
        "sess-1",
        {"event_type": "chat.delta", "content": "analysis complete"},
    )


@pytest.mark.asyncio
async def test_review_feedback_evolution_is_off_by_default(tmp_path, monkeypatch):
    llm = _AttributionLLM({})
    rail = _rail(tmp_path, llm)
    manager = SimpleNamespace(get_review_feedback_skill_rail=lambda _session_id: rail)
    monkeypatch.setattr(
        "jiuwenswarm.agents.harness.team.team_manager.get_team_manager",
        lambda _channel: manager,
    )
    handler = SwarmReviewFeedbackEvolutionHandler(
        channel_id="web",
        session_id="sess-1",
        team_id="team-1",
        trajectory_registry=_TrajectoryRegistry(_trajectory(None)),
        config={},
    )

    await handler(
        {
            "task_id": "task-1",
            "review_round": 1,
            "assignee": "worker-1",
            "feedback": "failed",
        }
    )

    assert llm.calls == 0
    rail.evolve_from_external_signals.assert_not_awaited()
