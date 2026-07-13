---
symbol: AgentWebSocketServer._ensure_code_mode_state
kind: method
source: jiuwenswarm/server/agent_ws_server.py
source_role: runtime_source
audit_scope: default_health_audit
class: AgentWebSocketServer
signature: "_ensure_code_mode_state(request: AgentRequest, mode: str, sub_mode: str, agent: Any) -> bool"
health:
  overall: risky
  name_behavior_match: good
  responsibility_focus: mixed
  length: long
  complexity: high
  implementation_soundness: questionable
  boundary_safety: risky
  input_contract: weak
  output_contract: clear
  side_effects: explicit
  error_handling: partial
  state_mutation: global
  dependency_coupling: high
  test_coverage: partial
  observability: partial
  performance_risk: medium
audit:
  status: unaudited
  auditor: null
  audited_at: null
  audited_commit: null
  audited_source_hash: null
  confidence: confirmed
  expired_reason: null
issues:
  - id: ISSUE-001
    dimension: input_contract
    severity: medium
    status: open
    summary: "Default session normalization is inconsistent before checkpointer access."
    evidence: "_ensure_code_mode_state computes session_id = request.session_id or default, but passes raw request.session_id into create_agent_session; OpenJiuwen create_agent_session(None) creates a fresh UUID session."
    suggested_action: "Pass the normalized session_id variable into create_agent_session and add a no-session regression test for default-session plan-mode sync."
  - id: ISSUE-002
    dimension: state_mutation
    severity: medium
    status: open
    summary: "Mode-sync locks are process-global and have no observed cleanup path."
    evidence: "_session_mode_sync_locks is a module-global dict populated through _session_mode_sync_lock; session delete clears _plan_exited_sessions but not the lock registry, and no pop/clear path was found."
    suggested_action: "Clear the session lock on successful session delete, shutdown, or reset when no sync is in flight, or replace the registry with a bounded/weak lifecycle."
  - id: ISSUE-003
    dimension: test_coverage
    severity: medium
    status: open
    summary: "Important guard branches lack direct tests."
    evidence: "Tests cover plan-to-normal sync, already-matching state, explicit /plan re-entry, team skip, and non-code skip; no direct test was found for interrupt-resume skip, non-explicit _plan_exited_sessions stale block, or plan_slug fallback block."
    suggested_action: "Add focused async tests for those three branches, including request.params['mode'] correction and plan.mode_exited push behavior."
confidence: confirmed
details: {}
---

# `AgentWebSocketServer._ensure_code_mode_state`

## Actual Role

For eligible code-mode, non-team chat turns, loads persisted OpenJiuwen plan-mode state under a per-session async lock and reconciles it with the requested sub-mode. It persists mode switches, blocks stale normal-to-plan re-entry after plan exit unless the user explicitly requested `/plan`, skips tool-interrupt resumes, may mutate request params/query when correcting mode or injecting a plan reminder, and returns whether plan mode was restored to normal.

## Key Signals

- Input: `AgentRequest`, resolved `mode` and `sub_mode`, and an agent exposing `get_instance()`.
- Output: Boolean; true only when persisted plan mode was `plan` and the requested sub-mode is `normal`.
- Main side effects: Reads/writes checkpointer state, mutates request params/query, uses module-global plan-exit and lock registries, and may send `plan.mode_exited`.
- Main risk: Stateful cross-boundary synchronization depends on implicit session-id, checkpoint, lock lifetime, and frontend payload contracts.
- Related tests: Direct transition tests exist, but high-risk stale re-entry and interrupt-resume skip branches are incomplete.

## Detail Index

- Detail docs pending.
