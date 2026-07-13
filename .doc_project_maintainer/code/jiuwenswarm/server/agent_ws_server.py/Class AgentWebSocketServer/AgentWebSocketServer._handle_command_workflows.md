---
symbol: AgentWebSocketServer._handle_command_workflows
kind: method
source: jiuwenswarm/server/agent_ws_server.py
source_role: runtime_source
audit_scope: default_health_audit
class: AgentWebSocketServer
signature: "_handle_command_workflows(ws: Any, request: AgentRequest, send_lock: asyncio.Lock) -> None"
health:
  overall: risky
  name_behavior_match: good
  responsibility_focus: single
  length: medium
  complexity: medium
  implementation_soundness: partial
  boundary_safety: risky
  input_contract: weak
  output_contract: clear
  side_effects: explicit
  error_handling: partial
  state_mutation: global
  dependency_coupling: medium
  test_coverage: partial
  observability: clear
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
    dimension: boundary_safety
    severity: high
    status: open
    summary: "session_id is not validated before checkpoint metadata restore."
    evidence: "The handler uses request.session_id or empty string, then restore_workflow_runs(session_id); restore reads get_agent_sessions_dir() / session_id / metadata.json."
    suggested_action: "Validate session_id as a safe session identifier before fallback restore, or use a metadata accessor that enforces containment."
  - id: ISSUE-002
    dimension: error_handling
    severity: medium
    status: open
    summary: "Snapshot and restore failures are reported as successful empty snapshots."
    evidence: "Both restore exceptions and live get_workflow_snapshot exceptions log a warning and send ok=true with an empty workflow_run_snapshot."
    suggested_action: "Return diagnostic metadata or ok=false for backend failures while preserving empty snapshots for real no-data cases."
  - id: ISSUE-003
    dimension: test_coverage
    severity: medium
    status: open
    summary: "Fallback and dispatch behavior are not fully tested."
    evidence: "Tests call the handler directly and do not assert restored workflow runs, restore failure behavior, or real dispatch routing."
    suggested_action: "Add tests for dispatcher routing, checkpoint restore success, restore exception, and unsafe or invalid session_id handling."
confidence: confirmed
details: {}
---

# `AgentWebSocketServer._handle_command_workflows`

## Actual Role

Handles `command.workflows` by returning a `workflow_run_snapshot` from a live workflow handler when present, or from persisted workflow-run metadata after the handler is gone. It always sends one encoded response under the send lock and currently degrades backend errors to an empty successful snapshot.

## Key Signals

- Input: `request.session_id`, `channel_id`, and request id after COMMAND_WORKFLOWS routing.
- Output: `ok: true` AgentResponse with `{type, workflows, session_id, total, truncated}`.
- Main side effects: Logs request/result/failures, may create a TeamManager, reads metadata on fallback, and sends one websocket frame.
- Main risk: Unvalidated session id reaches checkpoint restore and failures look like valid empty snapshots.
- Related tests: Direct handler tests cover live snapshots, size limits, defaults, and handler exception; restore and real dispatch tests are missing.

## Detail Index

- Detail docs pending.
