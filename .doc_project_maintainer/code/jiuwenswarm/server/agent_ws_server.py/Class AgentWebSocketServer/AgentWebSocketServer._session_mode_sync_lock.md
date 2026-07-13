---
symbol: AgentWebSocketServer._session_mode_sync_lock
kind: method
source: jiuwenswarm/server/agent_ws_server.py
source_role: runtime_source
audit_scope: default_health_audit
class: AgentWebSocketServer
signature: "_session_mode_sync_lock(session_id: str) -> asyncio.Lock"
health:
  overall: watch
  name_behavior_match: good
  responsibility_focus: single
  length: short
  complexity: low
  implementation_soundness: partial
  boundary_safety: partial
  input_contract: clear
  output_contract: clear
  side_effects: implicit
  error_handling: clear
  state_mutation: global
  dependency_coupling: medium
  test_coverage: partial
  observability: not_applicable
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
    dimension: state_mutation
    severity: medium
    status: open
    summary: "Per-session mode-sync locks are never removed."
    evidence: "The global dict is defined near module startup and this method stores missing locks, but searches found no pop or clear path; reset_instance, stop, and session delete do not clear _session_mode_sync_locks."
    suggested_action: "Add a guarded cleanup path for completed or deleted sessions, and clear the registry during test-only singleton reset or full server shutdown if no sync is in flight."
  - id: ISSUE-002
    dimension: test_coverage
    severity: medium
    status: open
    summary: "No direct test verifies the per-session serialization contract or registry lifetime."
    evidence: "Plan-mode orchestration tests cover _ensure_code_mode_state transitions and skip paths, but no direct reference to _session_mode_sync_lock or _session_mode_sync_locks and no same-session contention test was found."
    suggested_action: "Add focused async tests proving same-session calls share and serialize on one lock, different sessions do not block each other, and cleanup cannot remove a locked in-flight guard."
confidence: confirmed
details: {}
---

# `AgentWebSocketServer._session_mode_sync_lock`

## Actual Role

Returns a cached per-session `asyncio.Lock`, creating and storing one in module-global `_session_mode_sync_locks` on first use. `_ensure_code_mode_state` uses it to serialize plan/normal mode checkpoint synchronization for the same session while allowing different session IDs to proceed independently.

## Key Signals

- Input: `session_id` string; caller uses `request.session_id` or `"default"`.
- Output: Reused `asyncio.Lock` for that session key.
- Main side effects: Mutates module-global `_session_mode_sync_locks` on cache miss.
- Main risk: Locks live for the process lifetime once created, with no observed cleanup path on session delete, server stop, or singleton reset.
- Related tests: Indirect `_ensure_code_mode_state` behavior tests exist; no direct lock, concurrency, or lifetime test was found.

## Detail Index

- Detail docs pending.
