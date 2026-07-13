# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Workflow state models — aggregate state for a single workflow run."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any, ClassVar, Optional

from pydantic import BaseModel

logger = logging.getLogger(__name__)


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
      run_id         -> run_id  (unique run identifier, set by SwarmflowTool)
      workflow_name  -> workflow_name
      description    -> description  (META description, on workflow_started/completed)
      phase          -> phase
      label          -> label
      prompt         -> prompt
      model          -> model
      outcome        -> outcome
      text           -> text  (free narration text; term phrase on
                               workflow_started/completed, e.g. "Workflow started")

    """

    kind: str
    run_id: Optional[str] = None
    workflow_name: Optional[str] = None
    description: Optional[str] = None
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

    timestamp: str  # required — every entry must be timestamped
    type: str  # "tool_call" | "tool_result" (pending upstream)
    content: str = ""
    # reserved — tool calls require upstream WorkflowProgressEvent extension
    tool_name: Optional[str] = None
    tool_call_id: Optional[str] = None
    tool_result_preview: Optional[str] = None


class WorkflowAgentState(BaseModel):
    """State of a single agent within a workflow phase."""

    id: str
    name: str
    status: str = "running"  # running / completed / failed
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
    status: str = "running"  # running / completed / failed / planned
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
    status: str = "running"  # running / completed / failed / stopped
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
    _phase_counter: int = 0  # Global phase counter (1-based)
    _agent_slug_counter: dict[str, int] = {}  # Per-slug agent counter
    # Last phase entered via agent events (not serialized). Drives phase sealing:
    # when the next agent event carries a different phase name, the previous
    # phase is finalized. See ``_switch_to_phase``.
    _last_phase: Optional[WorkflowPhaseState] = None

    model_config = {"arbitrary_types_allowed": True}

    _KIND_HANDLERS: dict[str, str] = {
        "workflow_started": "_on_workflow_started",
        "phase": "_on_phase",
        "agent_started": "_on_agent_started",
        "agent_completed": "_on_agent_completed",
        "agent_failed": "_on_agent_failed",
        "workflow_completed": "_on_workflow_completed",
        "workflow_failed": "_on_workflow_failed",
        "log": "_on_log",
    }
    _TERMINAL_STATUSES: ClassVar[frozenset[str]] = frozenset({"completed", "failed", "stopped"})

    @property
    def is_terminal(self) -> bool:
        return self._is_terminal_status(self.status)

    @staticmethod
    def _is_terminal_status(status: str) -> bool:
        return status in WorkflowRunState._TERMINAL_STATUSES

    @staticmethod
    def _now_iso() -> str:
        """Return current local time as timezone-aware ISO 8601 string.

        Matches agent-core memory/timestamp convention:
        ``datetime.now(timezone.utc).astimezone()`` so the offset is inline
        (e.g. ``+08:00`` on China hosts) rather than bare UTC.
        """
        return datetime.now(timezone.utc).astimezone().isoformat()

    @staticmethod
    def _calc_duration_ms(started_at: str, completed_at: str) -> int:
        """Calculate duration in milliseconds between two ISO timestamps."""
        start = datetime.fromisoformat(started_at)
        end = datetime.fromisoformat(completed_at)
        return int((end - start).total_seconds() * 1000)

    @staticmethod
    def _find_agent_in_phase(phase: WorkflowPhaseState, agent_label: str) -> Optional[WorkflowAgentState]:
        """Find an agent by its label within a phase."""
        for agent in phase.agents:
            if agent.name == agent_label:
                return agent
        return None

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

    def finalize_if_running(self, terminal_status: str = "stopped") -> bool:
        """Force a non-terminal run to a terminal status. Returns True if changed.

        Used when the owning team runtime is torn down without a
        ``workflow_completed`` / ``workflow_failed`` event (e.g. session cancel
        or stop). Without this, a run left in ``running`` would persist that
        status to the checkpoint forever — no further events will ever arrive,
        so a restored snapshot would show a perpetually-running workflow.
        """
        if self.is_terminal:
            return False
        self._finalize_workflow(status=terminal_status)
        return True

    def _stamp_workflow_terminal(self, status: str) -> None:
        """Set workflow to a terminal status with completion timestamp and duration."""
        self.status = status
        self.completed_at = self._now_iso()
        if self.started_at:
            self.duration_ms = self._calc_duration_ms(self.started_at, self.completed_at)

    def _finalize_running_agents(self, phase: WorkflowPhaseState, terminal_status: str) -> None:
        """Finalize any still-running agents in ``phase`` to ``terminal_status``."""
        for agent in phase.agents:
            if agent.status == "running":
                self._stamp_agent_terminal(agent, terminal_status)

    def _finalize_running_phases(self, terminal_status: str) -> None:
        """Mark all running phases and their running agents as terminal.

        Only ``running`` phases are affected. A ``planned`` phase that never
        started (no agent ever entered it) is left untouched on purpose — by
        design an unexecuted/skipped phase stays ``planned`` in the terminal
        snapshot rather than being forced to a terminal status.
        """
        for phase in self.phases:
            if phase.status == "running":
                phase.status = terminal_status
            self._finalize_running_agents(phase, terminal_status)

    def _finalize_workflow(
            self,
            status: str,
            *,
            result: Optional[str] = None,
            error: Optional[str] = None,
    ) -> dict[str, Any]:
        """Transition workflow to terminal status, finalize phases/agents, return delta."""
        self._stamp_workflow_terminal(status)
        if result is not None:
            self.result = result
        if error is not None:
            self.error = error
        self._finalize_running_phases(status)
        return self._build_terminal_delta()

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

    def _find_phase_by_name(self, phase_name: str) -> Optional[WorkflowPhaseState]:
        """Find a phase by its name string."""
        for phase in self.phases:
            if phase.name == phase_name:
                return phase
        return None

    def _switch_to_phase(
            self, phase_name: str
    ) -> tuple[WorkflowPhaseState, Optional[WorkflowPhaseState]]:
        """Enter ``phase_name`` (running), sealing the previous phase on change.

        Driven by the ``phase`` field of agent events. When ``phase_name``
        differs from ``_last_phase.name``, the previous phase — if still
        ``running`` — is finalized to ``completed`` together with its
        still-running agents.

        Returns ``(target_phase, sealed_phase_or_None)``.
        """
        target = self._find_phase_by_name(phase_name)
        if target is None:
            phase_id = self._generate_phase_id(phase_name)
            target = WorkflowPhaseState(id=phase_id, name=phase_name, status="running")
            self.phases.append(target)
            logger.warning("[WF_DBG WorkflowRunState] phase %s not in plan, created on the fly", phase_name)
        elif not self._is_terminal_status(target.status):
            target.status = "running"

        sealed: Optional[WorkflowPhaseState] = None
        prev = self._last_phase
        if prev is not None and prev.name != phase_name and prev.status == "running":
            prev.status = "completed"
            self._finalize_running_agents(prev, "completed")
            sealed = prev
            logger.info("[WF_DBG WorkflowRunState] phase %s -> completed (sealed on switch to %s)",
                        prev.name, phase_name)

        self._last_phase = target
        return target, sealed

    def _resolve_agent(
            self, phase_name: str, agent_label: str
    ) -> tuple[Optional[WorkflowPhaseState], Optional[WorkflowAgentState]]:
        """Locate an agent by label, preferring its named phase.

        Falls back to scanning every phase when the named phase is missing or
        does not contain the agent — this tolerates phase-name drift between
        ``agent_started`` and ``agent_completed`` / ``agent_failed``. Logs a
        warning when nothing matches so the drop is visible, not silent.
        """
        phase = self._find_phase_by_name(phase_name)
        if phase is not None:
            agent = self._find_agent_in_phase(phase, agent_label)
            if agent is not None:
                return phase, agent
        for candidate in self.phases:
            agent = self._find_agent_in_phase(candidate, agent_label)
            if agent is not None:
                return candidate, agent
        logger.warning(
            "[WF_DBG WorkflowRunState] agent %r in phase %r not found; known phases=%s",
            agent_label, phase_name, [p.name for p in self.phases],
        )
        return None, None

    def _stamp_agent_terminal(self, agent: WorkflowAgentState, terminal_status: str) -> None:
        """Set agent to a terminal status with completion timestamp and duration."""
        agent.status = terminal_status
        agent.completed_at = self._now_iso()
        if agent.started_at:
            agent.duration_ms = self._calc_duration_ms(agent.started_at, agent.completed_at)

    def _finalize_agent(
            self,
            progress: WorkflowProgress,
            *,
            status: str,
            outcome: Optional[str] = None,
            error: Optional[str] = None,
    ) -> Optional[dict[str, Any]]:
        """Resolve agent from progress, mark terminal, bump counters, return phase delta."""
        phase, agent = self._resolve_agent(progress.phase or "?", progress.label or "")
        if phase is None or agent is None:
            return None
        self._stamp_agent_terminal(agent, status)
        if outcome is not None:
            agent.outcome = outcome
        if error is not None:
            agent.error = error
        phase.completed_agent_count += 1
        self.completed_agent_count += 1
        logger.info(
            "[WF_DBG WorkflowRunState] agent %s -> %s, phase=%s",
            agent.name, status, phase.name,
        )
        return self._build_phase_delta(phase)

    # --- Kind handler dispatch table ---

    def _on_workflow_started(self, progress: WorkflowProgress) -> dict[str, Any]:
        """Create new WorkflowRunState on workflow_started.

        When ``progress.phases`` is present (from script META, already
        normalized to ``PhasePlan`` by the engine), pre-create every step
        as ``planned`` so the first delta shows the full phase list on
        the frontend.

        ``workflow_name`` carries the script's META name; ``description``
        carries its META description; ``text`` is a term phrase
        (e.g. "Workflow started").
        """
        self.id = progress.run_id
        self.name = progress.workflow_name or "workflow"
        self.summary = progress.description or ""
        self.status = "running"
        self.started_at = self._now_iso()

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

    def _on_phase(self, progress: WorkflowProgress) -> Optional[dict[str, Any]]:
        """Log the phase event without modifying state.

        Phase transitions are driven by the ``phase`` field of agent events
        (see ``_switch_to_phase``), not by explicit PHASE events.
        """
        logger.info("[WF_DBG WorkflowRunState] id=%s name=%s phase event: %s (ignored, state unchanged)",
                    self.id, self.name, progress.phase or "?")
        return None

    def _on_agent_started(self, progress: WorkflowProgress) -> dict[str, Any]:
        """Add a new agent, entering its phase and sealing the previous one.

        The target phase is entered as ``running`` (created if missing). If
        this agent's phase differs from the last observed phase, the previous
        running phase is finalized — see ``_switch_to_phase``.
        """
        target_phase, sealed_phase = self._switch_to_phase(progress.phase or "?")

        agent_label = progress.label or "agent"
        agent_id = self._generate_agent_id(agent_label)
        agent_state = WorkflowAgentState(
            id=agent_id,
            name=agent_label,
            status="running",
            prompt=progress.prompt,
            model=progress.model,
            started_at=self._now_iso(),
        )
        target_phase.agents.append(agent_state)
        target_phase.agent_count += 1
        self.agent_count += 1
        logger.info("[WF_DBG WorkflowRunState] agent %s -> running, phase=%s", agent_label,
                    target_phase.name)

        if sealed_phase is not None:
            return self._build_phases_delta([sealed_phase, target_phase])
        return self._build_phase_delta(target_phase)

    def _on_agent_completed(self, progress: WorkflowProgress) -> Optional[dict[str, Any]]:
        """Mark an agent as completed with outcome."""
        return self._finalize_agent(
            progress,
            status="completed",
            outcome=progress.outcome,
        )

    def _on_agent_failed(self, progress: WorkflowProgress) -> Optional[dict[str, Any]]:
        """Mark an agent as failed with error."""
        return self._finalize_agent(
            progress,
            status="failed",
            error=progress.outcome or progress.text or "agent failed",
        )

    def _on_workflow_completed(self, progress: WorkflowProgress) -> dict[str, Any]:
        """Mark workflow as completed (terminal state) and finalize all running phases/agents.

        ``text`` is a term phrase (e.g. "Workflow completed") from the engine;
        ``workflow_name`` carries the script's META name; ``description`` carries
        its META description. Use description for the result summary if available.
        """
        return self._finalize_workflow(
            status="completed",
            result=progress.text or "",
        )

    def _on_workflow_failed(self, progress: WorkflowProgress) -> dict[str, Any]:
        """Mark workflow as failed (terminal state) and finalize all running phases/agents."""
        return self._finalize_workflow(
            status="failed",
            error=progress.text or progress.outcome or "workflow failed",
        )

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
        if self.error:
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
