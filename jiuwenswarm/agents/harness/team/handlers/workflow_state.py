# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Workflow state models — aggregate state for a single workflow run."""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Input model — mirrors WorkflowProgressTeamEvent fields
# ---------------------------------------------------------------------------

class PhasePlan(BaseModel):
    """One phase entry from the script's META ``phases`` list.

    Mirrors agent-core ``PhasePlan`` (dataclass). Normalized by the engine
    before emitting ``WORKFLOW_STARTED``, so downstream receives uniform
    structured entries — no ``isinstance`` checks needed.
    """

    title: str
    description: Optional[str] = None


class WorkflowProgress(BaseModel):
    """Incoming workflow progress event data.

    Field mapping from agent-core WorkflowProgressTeamEvent:
      kind           -> kind
      workflow_name  -> workflow_name
      phase          -> phase
      label          -> label
      prompt         -> prompt
      outcome        -> outcome
      text           -> text  (agent-core 'message' field maps to 'text' in TeamEvent)

    """

    kind: str
    workflow_name: Optional[str] = None
    phase: Optional[str] = None
    label: Optional[str] = None
    prompt: Optional[str] = None
    model: Optional[str] = None
    outcome: Optional[str] = None
    text: Optional[str] = None
    phases: Optional[list[PhasePlan]] = None


# ---------------------------------------------------------------------------
# Slug helper — for ID generation
# ---------------------------------------------------------------------------

_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
_MULTI_DASH_RE = re.compile(r"-{2,}")


def _slugify(name: str) -> str:
    """Convert a name string to a lowercase slug with single dashes."""
    s = _NON_ALNUM_RE.sub("-", name.lower().strip())
    s = _MULTI_DASH_RE.sub("-", s)
    return s.strip("-") or "anon"


# ---------------------------------------------------------------------------
# State models
# ---------------------------------------------------------------------------

class WorkflowAgentActivity(BaseModel):
    """A single activity entry for a workflow agent.

    Agent lifecycle (started/completed/failed) is already captured by
    ``status`` / ``started_at`` / ``completed_at`` / ``outcome`` / ``error``
    / ``duration_ms`` on ``WorkflowAgentState`` itself, so no status-type
    activity is written. Tool-call activity (type="tool_call" /
    "tool_result") requires upstream structured data which is not yet
    available. ``activity`` on ``WorkflowAgentState`` is always empty.
    """

    timestamp: str                    # required — every entry must be timestamped
    type: str                         # "tool_call" | "tool_result" (pending upstream)
    content: str = ""
    # reserved — tool calls require upstream WorkflowProgressEvent extension
    tool_name: Optional[str] = None
    tool_call_id: Optional[str] = None
    tool_result_preview: Optional[str] = None


class WorkflowAgentState(BaseModel):
    """State of a single agent within a workflow phase."""

    id: str
    name: str
    status: str = "running"            # running / completed / failed
    model: Optional[str] = None
    prompt: Optional[str] = None
    activity: list[WorkflowAgentActivity] = []
    outcome: Optional[str] = None
    error: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    # reserved — pending upstream token accounting
    token_count: Optional[int] = None
    duration_ms: Optional[int] = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict for event payload."""
        return self.model_dump(exclude_none=True)


class WorkflowPhaseState(BaseModel):
    """State of a single phase within a workflow run."""

    id: str
    name: str
    description: Optional[str] = None
    status: str = "running"            # running / completed / failed / planned
    agent_count: int = 0
    completed_agent_count: int = 0
    agents: list[WorkflowAgentState] = []

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict for event payload."""
        return self.model_dump(exclude_none=True)


class WorkflowRunState(BaseModel):
    """Complete state of a single workflow run.

    Maintains aggregate state, provides apply(progress) -> delta
    for incremental push, and to_workflow_run_dict() for full snapshots.
    """

    id: str = ""
    name: str = ""
    summary: str = ""
    status: str = "running"            # running / completed / failed / stopped
    agent_count: int = 0
    completed_agent_count: int = 0
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    script: Optional[str] = None
    result: Optional[str] = None
    error: Optional[str] = None
    logs: list[str] = []
    phases: list[WorkflowPhaseState] = []

    # reserved — pending upstream token accounting
    token_count: Optional[int] = None
    duration_ms: Optional[int] = None
    estimated_token_count: Optional[int] = None

    # Private mutable state for ID generation sequencing (not serialized)
    _phase_counter: int = 0        # Global phase counter (1-based)
    _agent_slug_counter: dict[str, int] = {}  # Per-slug agent counter

    model_config = {"arbitrary_types_allowed": True}

    @property
    def is_terminal(self) -> bool:
        return self.status in ("completed", "failed", "stopped")

    def _find_phase_by_name(self, phase_name: str) -> Optional[WorkflowPhaseState]:
        """Find a phase by its name string."""
        for phase in self.phases:
            if phase.name == phase_name:
                return phase
        return None

    def _find_agent_in_phase(self, phase: WorkflowPhaseState, agent_label: str) -> Optional[WorkflowAgentState]:
        """Find an agent by its label within a phase."""
        for agent in phase.agents:
            if agent.name == agent_label:
                return agent
        return None

    def _generate_phase_id(self, phase_name: str) -> str:
        """Generate a phase ID: slug for first phase, slug+global_seq for subsequent.

        First phase gets just its slug as ID. Subsequent phases get slug + global
        sequence number appended, ensuring uniqueness across different phase names.
        """
        slug = _slugify(phase_name)
        self._phase_counter += 1
        return f"{slug}-{self._phase_counter}"

    def _generate_agent_id(self, agent_label: str) -> str:
        """Generate an agent ID: slugified label + per-slug sequence number.

        Per-slug counter is always appended, so same-name agents within a phase
        get incrementing sequence numbers.
        """
        slug = _slugify(agent_label)
        counter = self._agent_slug_counter.get(slug, 0) + 1
        self._agent_slug_counter[slug] = counter
        return f"{slug}-{counter}"

    def _utc_now_iso(self) -> str:
        """Return current UTC time as ISO format string."""
        return datetime.now(timezone.utc).isoformat()

    def _calc_duration_ms(self, started_at: str, completed_at: str) -> int:
        """Calculate duration in milliseconds between two ISO timestamps."""
        start = datetime.fromisoformat(started_at)
        end = datetime.fromisoformat(completed_at)
        return int((end - start).total_seconds() * 1000)

    def apply(self, progress: WorkflowProgress) -> Optional[dict[str, Any]]:
        """Apply a progress event, update state, return incremental delta dict.

        Returns None if no push is needed (e.g. log, or unknown kind).
        """
        kind = progress.kind
        handler = self._KIND_HANDLERS.get(kind)
        if handler is None:
            return None
        method = getattr(self, handler)
        return method(progress)

    # --- Kind handler dispatch table ---

    _KIND_HANDLERS: dict[str, str] = {
        "workflow_started": "_on_workflow_started",
        "phase": "_on_phase",
        "phase_started": "_on_phase_started",
        "phase_completed": "_on_phase_completed",
        "agent_started": "_on_agent_started",
        "agent_completed": "_on_agent_completed",
        "agent_failed": "_on_agent_failed",
        "workflow_completed": "_on_workflow_completed",
        "workflow_failed": "_on_workflow_failed",
        "log": "_on_log",
    }

    def _on_workflow_started(self, progress: WorkflowProgress) -> dict[str, Any]:
        """Create new WorkflowRunState on workflow_started.

        When ``progress.phases`` is present (from script META, already
        normalized to ``PhasePlan`` by the engine), pre-create every step
        as ``planned`` so the first delta shows the full phase list on
        the frontend.
        """
        self.id = f"wf_{uuid.uuid4().hex[:12]}"
        self.name = progress.workflow_name or "workflow"
        self.summary = progress.text or ""
        self.status = "running"
        self.started_at = self._utc_now_iso()

        for phase_plan in (progress.phases or []):
            phase_id = self._generate_phase_id(phase_plan.title)
            self.phases.append(
                WorkflowPhaseState(
                    id=phase_id,
                    name=phase_plan.title,
                    description=phase_plan.description,
                    status="planned",
                )
            )

        return self._build_top_level_delta()

    def _on_phase(self, progress: WorkflowProgress) -> dict[str, Any]:
        """Add or enter a new phase — finalize the previous running phase first.

        Engine sends PHASE but not PHASE_COMPLETED, so we implicitly complete
        the previous running phase (and any still-running agents within it).
        The returned delta includes **both** the finalized phase(s) and the
        new/re-entered phase so the frontend can update both in one frame.
        """
        # Finalize the previous running phase (engine sends PHASE but not PHASE_COMPLETED)
        finalized_phases: list[WorkflowPhaseState] = []
        for prev_phase in self.phases:
            if prev_phase.status == "running":
                prev_phase.status = "completed"
                for agent in prev_phase.agents:
                    if agent.status == "running":
                        agent.status = "completed"
                        agent.completed_at = self._utc_now_iso()
                        if agent.started_at:
                            agent.duration_ms = self._calc_duration_ms(agent.started_at, agent.completed_at)
                finalized_phases.append(prev_phase)

        phase_name = progress.phase or "?"
        existing = self._find_phase_by_name(phase_name)
        if existing is not None:
            # Re-entering an existing phase — mark as running
            existing.status = "running"
            target_phase = existing
        else:
            # New phase
            phase_id = self._generate_phase_id(phase_name)
            target_phase = WorkflowPhaseState(id=phase_id, name=phase_name, status="running")
            self.phases.append(target_phase)

        # Return delta containing both finalized phases and the new/re-entered phase
        phases_in_delta = finalized_phases + [target_phase]
        return self._build_phases_delta(phases_in_delta)

    def _on_phase_started(self, progress: WorkflowProgress) -> dict[str, Any]:
        """Alias for 'phase' — same behavior."""
        return self._on_phase(progress)

    def _on_phase_completed(self, progress: WorkflowProgress) -> dict[str, Any]:
        """Mark current phase as completed."""
        phase_name = progress.phase or "?"
        phase = self._find_phase_by_name(phase_name)
        if phase is None:
            return None
        phase.status = "completed"
        return self._build_phase_delta(phase)

    def _on_agent_started(self, progress: WorkflowProgress) -> dict[str, Any]:
        """Add a new agent to the current phase."""
        phase_name = progress.phase or "?"
        phase = self._find_phase_by_name(phase_name)
        if phase is None:
            return None
        agent_label = progress.label or "agent"
        agent_id = self._generate_agent_id(agent_label)
        agent = WorkflowAgentState(
            id=agent_id,
            name=agent_label,
            status="running",
            prompt=progress.prompt,
            model=progress.model,
            started_at=self._utc_now_iso(),
        )
        phase.agents.append(agent)
        phase.agent_count += 1
        self.agent_count += 1
        return self._build_phase_delta(phase)

    def _on_agent_completed(self, progress: WorkflowProgress) -> dict[str, Any]:
        """Mark an agent as completed with outcome."""
        phase_name = progress.phase or "?"
        phase = self._find_phase_by_name(phase_name)
        if phase is None:
            return None
        agent_label = progress.label or ""
        agent = self._find_agent_in_phase(phase, agent_label)
        if agent is None:
            return None
        agent.status = "completed"
        agent.outcome = progress.outcome
        agent.completed_at = self._utc_now_iso()
        if agent.started_at:
            agent.duration_ms = self._calc_duration_ms(agent.started_at, agent.completed_at)
        phase.completed_agent_count += 1
        self.completed_agent_count += 1
        return self._build_phase_delta(phase)

    def _on_agent_failed(self, progress: WorkflowProgress) -> dict[str, Any]:
        """Mark an agent as failed with error."""
        phase_name = progress.phase or "?"
        phase = self._find_phase_by_name(phase_name)
        if phase is None:
            return None
        agent_label = progress.label or ""
        agent = self._find_agent_in_phase(phase, agent_label)
        if agent is None:
            return None
        agent.status = "failed"
        agent.error = progress.outcome or progress.text or "agent failed"
        agent.completed_at = self._utc_now_iso()
        if agent.started_at:
            agent.duration_ms = self._calc_duration_ms(agent.started_at, agent.completed_at)
        phase.completed_agent_count += 1
        self.completed_agent_count += 1
        return self._build_phase_delta(phase)

    def _on_workflow_completed(self, progress: WorkflowProgress) -> dict[str, Any]:
        """Mark workflow as completed (terminal state) and finalize all running phases/agents."""
        self.status = "completed"
        self.completed_at = self._utc_now_iso()
        if self.started_at:
            self.duration_ms = self._calc_duration_ms(self.started_at, self.completed_at)
        self.result = progress.text or ""
        self._finalize_running_phases("completed")
        return self._build_terminal_delta()

    def _on_workflow_failed(self, progress: WorkflowProgress) -> dict[str, Any]:
        """Mark workflow as failed (terminal state) and finalize all running phases/agents."""
        self.status = "failed"
        self.completed_at = self._utc_now_iso()
        if self.started_at:
            self.duration_ms = self._calc_duration_ms(self.started_at, self.completed_at)
        self.error = progress.text or progress.outcome or "workflow failed"
        self._finalize_running_phases("failed")
        return self._build_terminal_delta()

    def _finalize_running_phases(self, terminal_status: str) -> None:
        """Mark all running phases and their running agents as terminal."""
        for phase in self.phases:
            if phase.status == "running":
                phase.status = terminal_status
            for agent in phase.agents:
                if agent.status == "running":
                    agent.status = terminal_status
                    agent.completed_at = self._utc_now_iso()
                    if agent.started_at:
                        agent.duration_ms = self._calc_duration_ms(agent.started_at, agent.completed_at)

    def _on_log(self, progress: WorkflowProgress) -> dict[str, Any]:
        """Append log text to top-level ``self.logs`` and emit delta with logs.

        Log text is stored at the workflow level only — not routed to any
        agent or phase activity. The returned delta includes ``logs`` at the
        same level as ``phases`` so the frontend receives log updates via the
        ``workflow.updated`` event.
        """
        log_text = progress.text or ""
        self.logs.append(log_text)
        return self._build_log_delta(log_text)

    # --- Delta builders ---

    def _build_log_delta(self, log_text: str) -> dict[str, Any]:
        """Build delta with **incremental** log entry — mirrors ``_build_phases_delta``.

        Only the newly appended log text is included in ``logs``, not the full
        history. The surrounding top-level fields match ``_build_phases_delta``
        so the frontend can merge this delta the same way.
        """
        return {
            "id": self.id,
            "name": self.name,
            "status": self.status,
            "agent_count": self.agent_count,
            "completed_agent_count": self.completed_agent_count,
            "started_at": self.started_at,
            "logs": [log_text],
        }

    def _build_top_level_delta(self) -> dict[str, Any]:
        """Build delta with workflow top-level fields and pre-populated phases."""
        return {
            "id": self.id,
            "name": self.name,
            "summary": self.summary,
            "status": self.status,
            "agent_count": self.agent_count,
            "completed_agent_count": self.completed_agent_count,
            "started_at": self.started_at,
            "phases": [p.to_dict() for p in self.phases],
            "logs": list(self.logs),
        }

    def _build_phase_delta(self, phase: WorkflowPhaseState) -> dict[str, Any]:
        """Build delta containing only one changed phase (with all its agents)."""
        return self._build_phases_delta([phase])

    def _build_phases_delta(self, phases: list[WorkflowPhaseState]) -> dict[str, Any]:
        """Build delta containing multiple changed phases (each with all its agents)."""
        return {
            "id": self.id,
            "name": self.name,
            "status": self.status,
            "agent_count": self.agent_count,
            "completed_agent_count": self.completed_agent_count,
            "started_at": self.started_at,
            "phases": [p.to_dict() for p in phases],
        }

    def _build_terminal_delta(self) -> dict[str, Any]:
        """Build terminal delta (status=completed/failed) with all phases."""
        result: dict[str, Any] = {
            "id": self.id,
            "name": self.name,
            "summary": self.summary,
            "status": self.status,
            "agent_count": self.agent_count,
            "completed_agent_count": self.completed_agent_count,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "duration_ms": self.duration_ms,
        }
        if self.status == "failed":
            result["error"] = self.error
        if self.result:
            result["result"] = self.result
        # Terminal delta includes all phases for completeness
        result["phases"] = [p.to_dict() for p in self.phases]
        result["logs"] = list(self.logs)
        return result

    def to_workflow_run_dict(self) -> dict[str, Any]:
        """Return complete WorkflowRun dict for command.workflows snapshot.

        Structure matches the workflow.updated event's workflow field
        but includes ALL phases and ALL agents (not just deltas).
        """
        result: dict[str, Any] = {
            "id": self.id,
            "name": self.name,
            "summary": self.summary,
            "status": self.status,
            "agent_count": self.agent_count,
            "completed_agent_count": self.completed_agent_count,
            "started_at": self.started_at,
            "phases": [p.to_dict() for p in self.phases],
            "logs": list(self.logs),
        }
        if self.completed_at:
            result["completed_at"] = self.completed_at
        if self.duration_ms:
            result["duration_ms"] = self.duration_ms
        if self.error:
            result["error"] = self.error
        if self.result:
            result["result"] = self.result
        # reserved fields — pending upstream token accounting
        result["token_count"] = self.token_count
        result["estimated_token_count"] = self.estimated_token_count
        return result