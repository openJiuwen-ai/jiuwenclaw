"""WorkflowRunState unit tests — verify state transitions and delta computation."""

from __future__ import annotations

import pytest
from jiuwenswarm.agents.harness.team.handlers.workflow_state import (
    WorkflowRunState,
    WorkflowPhaseState,
    WorkflowAgentState,
    WorkflowAgentActivity,
    WorkflowProgress,
    PhasePlan,
)


_DEFAULT_RUN_ID = "wf_testrun00001"


def _make_progress(kind: str, **kwargs) -> WorkflowProgress:
    if "run_id" not in kwargs:
        kwargs["run_id"] = _DEFAULT_RUN_ID
    return WorkflowProgress(kind=kind, **kwargs)


class TestWorkflowRunStateLifecycle:
    """Scenario 1 & 5: workflow started -> phases -> agents -> completed."""

    @staticmethod
    def test_workflow_started_creates_run():
        progress = _make_progress("workflow_started", workflow_name="werewolf-game", text="start")
        state = WorkflowRunState()
        delta = state.apply(progress)
        assert state.id.startswith("wf_")
        assert state.name == "werewolf-game"
        assert state.status == "running"
        assert state.started_at is not None
        assert delta is not None
        assert delta["id"] == state.id
        assert delta["status"] == "running"

    @staticmethod
    def test_workflow_started_pre_populates_planned_phases():
        """Phases from META (already normalized to PhasePlan) are pre-created as planned."""
        phases_meta = [
            PhasePlan(title="发牌", description="分配身份"),
            PhasePlan(title="游戏进行"),
            PhasePlan(title="结算"),
        ]
        progress = _make_progress("workflow_started", workflow_name="werewolf-game", phases=phases_meta)
        state = WorkflowRunState()
        delta = state.apply(progress)
        assert len(state.phases) == 3
        assert state.phases[0].name == "发牌"
        assert state.phases[0].status == "planned"
        assert state.phases[0].description == "分配身份"
        assert state.phases[1].name == "游戏进行"
        assert state.phases[1].status == "planned"
        assert state.phases[1].description is None
        assert state.phases[2].name == "结算"
        assert state.phases[2].status == "planned"
        assert len(delta["phases"]) == 3
        assert all(p["status"] == "planned" for p in delta["phases"])

    @staticmethod
    def test_planned_phase_activated_on_agent_started():
        """A planned phase becomes running when an agent starts within it."""
        phases_meta = [PhasePlan(title="发牌"), PhasePlan(title="游戏进行")]
        state = WorkflowRunState()
        state.apply(_make_progress("workflow_started", workflow_name="test", phases=phases_meta))
        assert state.phases[0].status == "planned"
        assert len(state.phases) == 2

        delta = state.apply(_make_progress("agent_started", phase="发牌", label="dealer"))
        assert state.phases[0].status == "running"
        assert state.phases[1].status == "planned"
        assert delta["phases"][0]["name"] == "发牌"
        assert delta["phases"][0]["status"] == "running"

    @staticmethod
    def test_agent_started_creates_running_phase():
        state = WorkflowRunState()
        state.apply(_make_progress("workflow_started", workflow_name="test"))
        progress = _make_progress("agent_started", phase="Night 1", label="agent-a")
        delta = state.apply(progress)
        assert len(state.phases) == 1
        assert state.phases[0].name == "Night 1"
        assert state.phases[0].status == "running"
        assert delta is not None
        assert delta["phases"][0]["id"] == state.phases[0].id

    @staticmethod
    def test_phase_started_event_ignored():
        """phase_started is no longer handled — ignored, no state change."""
        state = WorkflowRunState()
        state.apply(_make_progress("workflow_started", workflow_name="test"))
        delta = state.apply(_make_progress("phase_started", phase="Day Vote"))
        assert delta is None
        assert len(state.phases) == 0

    @staticmethod
    def test_agent_started_adds_agent_to_current_phase():
        state = WorkflowRunState()
        state.apply(_make_progress("workflow_started", workflow_name="test"))
        state.apply(_make_progress("phase", phase="Night 1"))
        progress = _make_progress("agent_started", phase="Night 1", label="werewolf-kill", prompt="你是狼人")
        delta = state.apply(progress)
        assert state.phases[0].agent_count == 1
        assert len(state.phases[0].agents) == 1
        assert state.phases[0].agents[0].name == "werewolf-kill"
        assert state.phases[0].agents[0].prompt == "你是狼人"
        assert state.phases[0].agents[0].status == "running"
        assert state.agent_count == 1

    @staticmethod
    def test_agent_completed_updates_agent():
        state = WorkflowRunState()
        state.apply(_make_progress("workflow_started", workflow_name="test"))
        state.apply(_make_progress("phase", phase="Night 1"))
        state.apply(_make_progress("agent_started", phase="Night 1", label="werewolf-kill"))
        progress = _make_progress("agent_completed", phase="Night 1", label="werewolf-kill", outcome="击杀 Carol")
        delta = state.apply(progress)
        assert state.phases[0].agents[0].status == "completed"
        assert state.phases[0].agents[0].outcome == "击杀 Carol"
        assert state.completed_agent_count == 1
        assert state.phases[0].completed_agent_count == 1

    @staticmethod
    def test_agent_failed_marks_failed():
        state = WorkflowRunState()
        state.apply(_make_progress("workflow_started", workflow_name="test"))
        state.apply(_make_progress("phase", phase="Night 1"))
        state.apply(_make_progress("agent_started", phase="Night 1", label="witch-action"))
        progress = _make_progress("agent_failed", phase="Night 1", label="witch-action")
        delta = state.apply(progress)
        assert state.phases[0].agents[0].status == "failed"
        assert state.phases[0].agents[0].error is not None

    @staticmethod
    def test_phase_sealed_on_switch_to_next_phase():
        """A running phase is sealed to completed when an agent starts in the next phase."""
        state = WorkflowRunState()
        state.apply(_make_progress("workflow_started", workflow_name="test"))
        state.apply(_make_progress("agent_started", phase="Night 1", label="agent-a"))
        assert state.phases[0].status == "running"
        state.apply(_make_progress("agent_started", phase="Day 1", label="agent-b"))
        assert state.phases[0].status == "completed"
        assert state.phases[1].status == "running"

    @staticmethod
    def test_workflow_completed_marks_terminal():
        state = WorkflowRunState()
        state.apply(_make_progress("workflow_started", workflow_name="test"))
        state.apply(_make_progress("phase", phase="Night 1"))
        state.apply(_make_progress("phase_completed", phase="Night 1"))
        progress = _make_progress("workflow_completed", text="done")
        delta = state.apply(progress)
        assert state.status == "completed"
        assert state.completed_at is not None
        assert state.is_terminal is True
        assert delta["status"] == "completed"

    @staticmethod
    def test_workflow_failed_marks_terminal_with_error():
        state = WorkflowRunState()
        state.apply(_make_progress("workflow_started", workflow_name="test"))
        state.apply(_make_progress("phase", phase="Night 1"))
        state.apply(_make_progress("agent_started", phase="Night 1", label="agent-1"))
        state.apply(_make_progress("agent_failed", phase="Night 1", label="agent-1"))
        progress = _make_progress("workflow_failed", text="error")
        delta = state.apply(progress)
        assert state.status == "failed"
        assert state.error is not None
        assert state.is_terminal is True

    @staticmethod
    def test_workflow_completed_finalizes_running_phases_and_agents():
        """All running phases and agents are marked completed on workflow_completed."""
        state = WorkflowRunState()
        state.apply(_make_progress("workflow_started", workflow_name="test"))
        state.apply(_make_progress("phase", phase="Phase 1"))
        state.apply(_make_progress("agent_started", phase="Phase 1", label="agent-a"))
        # Phase 2 entered but no agent_completed events
        state.apply(_make_progress("phase", phase="Phase 2"))
        state.apply(_make_progress("agent_started", phase="Phase 2", label="agent-b"))
        state.apply(_make_progress("workflow_completed", text="done"))
        assert state.status == "completed"
        assert state.phases[0].status == "completed"
        assert state.phases[0].agents[0].status == "completed"
        assert state.phases[1].status == "completed"
        assert state.phases[1].agents[0].status == "completed"

    @staticmethod
    def test_workflow_failed_finalizes_running_phases_and_agents():
        """All running phases and agents are marked failed on workflow_failed."""
        state = WorkflowRunState()
        state.apply(_make_progress("workflow_started", workflow_name="test"))
        state.apply(_make_progress("phase", phase="Phase 1"))
        state.apply(_make_progress("agent_started", phase="Phase 1", label="agent-a"))
        state.apply(_make_progress("workflow_failed", text="error"))
        assert state.status == "failed"
        assert state.phases[0].status == "failed"
        assert state.phases[0].agents[0].status == "failed"

    @staticmethod
    def test_log_event_produces_delta_with_logs():
        """Log events produce a delta with ``logs`` at the same level as ``phases``."""
        state = WorkflowRunState()
        state.apply(_make_progress("workflow_started", workflow_name="test"))
        progress = _make_progress("log", text="some narration")
        delta = state.apply(progress)
        assert delta is not None
        assert "logs" in delta
        assert delta["logs"] == ["some narration"]
        assert "phases" not in delta  # log delta does not include phases
        assert len(state.logs) == 1

    @staticmethod
    def test_log_with_phase_and_label_stored_in_logs_only():
        """Log with phase + label is stored in self.logs only, not in agent activity."""
        state = WorkflowRunState()
        state.apply(_make_progress("workflow_started", workflow_name="test"))
        state.apply(_make_progress("phase", phase="Phase 1"))
        state.apply(_make_progress("agent_started", phase="Phase 1", label="agent-a"))
        progress = _make_progress("log", phase="Phase 1", label="agent-a", text="thinking...")
        delta = state.apply(progress)
        assert delta is not None
        assert "logs" in delta
        assert delta["logs"] == ["thinking..."]
        agent = state.phases[0].agents[0]
        assert len(agent.activity) == 0  # log is not written to agent activity
        assert len(state.logs) == 1

    @staticmethod
    def test_log_with_phase_only_stored_in_logs():
        """Log with phase but no label is stored in self.logs only, not in agent activity."""
        state = WorkflowRunState()
        state.apply(_make_progress("workflow_started", workflow_name="test"))
        state.apply(_make_progress("phase", phase="Phase 1"))
        state.apply(_make_progress("agent_started", phase="Phase 1", label="agent-a"))
        progress = _make_progress("log", phase="Phase 1", text="phase-level log")
        delta = state.apply(progress)
        assert delta is not None
        assert "logs" in delta
        assert delta["logs"] == ["phase-level log"]
        agent = state.phases[0].agents[0]
        assert len(agent.activity) == 0  # log is not written to agent activity
        assert len(state.logs) == 1

    @staticmethod
    def test_log_without_phase_stored_in_top_level_only():
        """Log without phase or label only stored in self.logs, delta includes logs."""
        state = WorkflowRunState()
        state.apply(_make_progress("workflow_started", workflow_name="test"))
        progress = _make_progress("log", text="orphan log")
        delta = state.apply(progress)
        assert delta is not None
        assert "logs" in delta
        assert delta["logs"] == ["orphan log"]
        assert len(state.logs) == 1
        assert state.logs[0] == "orphan log"

    @staticmethod
    def test_multiple_phases_and_agents():
        """Scenario 2: multi-phase workflow with multiple agents."""
        state = WorkflowRunState()
        state.apply(_make_progress("workflow_started", workflow_name="game"))
        state.apply(_make_progress("phase", phase="Night 1"))
        state.apply(_make_progress("agent_started", phase="Night 1", label="werewolf", prompt="狼人行动"))
        state.apply(_make_progress("agent_completed", phase="Night 1", label="werewolf", outcome="击杀"))
        state.apply(_make_progress("agent_started", phase="Night 1", label="witch", prompt="女巫行动"))
        state.apply(_make_progress("agent_completed", phase="Night 1", label="witch", outcome="救人"))
        state.apply(_make_progress("phase_completed", phase="Night 1"))
        state.apply(_make_progress("phase", phase="Day 1 Vote"))
        state.apply(_make_progress("agent_started", phase="Day 1 Vote", label="alice-vote", prompt="投票"))
        assert len(state.phases) == 2
        assert state.phases[0].status == "completed"
        assert state.phases[0].agent_count == 2
        assert state.phases[1].status == "running"
        assert state.phases[1].agents[0].name == "alice-vote"
        assert state.agent_count == 3


class TestWorkflowRunStateDelta:
    """Verify delta only contains changed phase/agent objects."""

    @staticmethod
    def test_delta_contains_finalized_and_new_phase():
        """Entering a new phase via agent_started: delta includes finalized previous + new phase."""
        state = WorkflowRunState()
        state.apply(_make_progress("workflow_started", workflow_name="test"))
        state.apply(_make_progress("agent_started", phase="Phase 1", label="agent-a"))
        progress = _make_progress("agent_started", phase="Phase 2", label="agent-b")
        delta = state.apply(progress)
        # Delta includes finalized Phase 1 + new Phase 2
        assert len(delta["phases"]) == 2
        assert delta["phases"][0]["name"] == "Phase 1"
        assert delta["phases"][0]["status"] == "completed"
        assert delta["phases"][1]["name"] == "Phase 2"
        assert delta["phases"][1]["status"] == "running"

    @staticmethod
    def test_delta_on_agent_completed_contains_updated_agent():
        state = WorkflowRunState()
        state.apply(_make_progress("workflow_started", workflow_name="test"))
        state.apply(_make_progress("phase", phase="Phase 1"))
        state.apply(_make_progress("agent_started", phase="Phase 1", label="agent-a"))
        progress = _make_progress("agent_completed", phase="Phase 1", label="agent-a", outcome="done")
        delta = state.apply(progress)
        assert len(delta["phases"]) == 1
        agent_in_delta = delta["phases"][0]["agents"][0]
        assert agent_in_delta["status"] == "completed"
        assert agent_in_delta["outcome"] == "done"


class TestWorkflowRunStateSerialization:
    """Scenario 6: checkpoint persist/restore round-trip."""

    @staticmethod
    def test_model_dump_and_restore():
        state = WorkflowRunState()
        state.apply(_make_progress("workflow_started", workflow_name="test"))
        state.apply(_make_progress("phase", phase="Phase 1"))
        state.apply(_make_progress("agent_started", phase="Phase 1", label="agent-a", prompt="prompt"))
        data = state.model_dump()
        restored = WorkflowRunState.model_validate(data)
        assert restored.id == state.id
        assert restored.name == state.name
        assert restored.status == state.status
        assert len(restored.phases) == 1
        assert len(restored.phases[0].agents) == 1

    @staticmethod
    def test_to_workflow_run_dict_returns_full_snapshot():
        """command.workflows returns complete WorkflowRun."""
        state = WorkflowRunState()
        state.apply(_make_progress("workflow_started", workflow_name="game"))
        state.apply(_make_progress("phase", phase="Phase 1"))
        state.apply(_make_progress("agent_started", phase="Phase 1", label="agent-a"))
        snapshot = state.to_workflow_run_dict()
        assert snapshot["id"] == state.id
        assert snapshot["status"] == "running"
        assert len(snapshot["phases"]) == 1
        assert len(snapshot["phases"][0]["agents"]) == 1


class TestWorkflowRunStateTimestamps:
    """Verify timestamp and duration fields."""

    @staticmethod
    def test_started_at_set_on_workflow_started():
        state = WorkflowRunState()
        state.apply(_make_progress("workflow_started", workflow_name="test"))
        assert state.started_at is not None

    @staticmethod
    def test_completed_at_and_duration_on_workflow_completed():
        state = WorkflowRunState()
        state.apply(_make_progress("workflow_started", workflow_name="test"))
        state.started_at = "2026-06-04T10:00:00+08:00"
        progress = _make_progress("workflow_completed", text="done")
        state.apply(progress)
        assert state.completed_at is not None
        assert state.duration_ms is not None

    @staticmethod
    def test_agent_started_at_on_agent_started():
        state = WorkflowRunState()
        state.apply(_make_progress("workflow_started", workflow_name="test"))
        state.apply(_make_progress("phase", phase="Phase 1"))
        state.apply(_make_progress("agent_started", phase="Phase 1", label="agent-a"))
        assert state.phases[0].agents[0].started_at is not None

    @staticmethod
    def test_agent_completed_at_on_agent_completed():
        state = WorkflowRunState()
        state.apply(_make_progress("workflow_started", workflow_name="test"))
        state.apply(_make_progress("phase", phase="Phase 1"))
        state.apply(_make_progress("agent_started", phase="Phase 1", label="agent-a"))
        state.phases[0].agents[0].started_at = "2026-06-04T10:00:08+08:00"
        state.apply(_make_progress("agent_completed", phase="Phase 1", label="agent-a", outcome="done"))
        assert state.phases[0].agents[0].completed_at is not None
        assert state.phases[0].agents[0].duration_ms is not None


class TestIDGeneration:
    """Verify ID generation: uuid for workflow, slug+seq for phase/agent."""

    @staticmethod
    def test_workflow_id_starts_with_wf():
        state = WorkflowRunState()
        state.apply(_make_progress("workflow_started", workflow_name="test"))
        assert state.id.startswith("wf_")

    @staticmethod
    def test_phase_id_is_slug_with_sequence():
        state = WorkflowRunState()
        state.apply(_make_progress("workflow_started", workflow_name="test"))
        state.apply(_make_progress("agent_started", phase="Night 1", label="a"))
        assert state.phases[0].id == "night-1-1"
        state.apply(_make_progress("agent_started", phase="Day Vote", label="b"))
        assert state.phases[1].id == "day-vote-2"

    @staticmethod
    def test_agent_id_is_slug_with_sequence():
        state = WorkflowRunState()
        state.apply(_make_progress("workflow_started", workflow_name="test"))
        state.apply(_make_progress("phase", phase="Night 1"))
        state.apply(_make_progress("agent_started", phase="Night 1", label="werewolf-kill"))
        assert state.phases[0].agents[0].id == "werewolf-kill-1"
        state.apply(_make_progress("agent_started", phase="Night 1", label="werewolf-kill"))
        assert state.phases[0].agents[1].id == "werewolf-kill-2"

    @staticmethod
    def test_unknown_kind_returns_none():
        """Unknown kind values are ignored — delta is None."""
        state = WorkflowRunState()
        state.apply(_make_progress("workflow_started", workflow_name="test"))
        progress = _make_progress("unknown_kind")
        delta = state.apply(progress)
        assert delta is None