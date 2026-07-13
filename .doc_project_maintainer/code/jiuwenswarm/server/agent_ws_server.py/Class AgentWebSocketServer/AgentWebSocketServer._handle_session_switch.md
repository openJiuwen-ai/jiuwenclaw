---
symbol: AgentWebSocketServer._handle_session_switch
kind: method
source: jiuwenswarm/server/agent_ws_server.py
source_role: runtime_source
audit_scope: default_health_audit
class: AgentWebSocketServer
signature: "_handle_session_switch(ws: Any, request: AgentRequest, send_lock: asyncio.Lock) -> None"
health:
  overall: watch
  name_behavior_match: good
  responsibility_focus: single
  length: short
  complexity: low
  implementation_soundness: partial
  boundary_safety: partial
  input_contract: weak
  output_contract: clear
  side_effects: explicit
  error_handling: partial
  state_mutation: shared
  dependency_coupling: medium
  test_coverage: partial
  observability: partial
  performance_risk: low
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
    summary: "The handler trusts client-supplied team mode and target identity."
    evidence: "It checks only is_team_params(params) and a non-empty target before calling prepare_session_switch; it does not verify the target session directory or persisted metadata mode/team_name."
    suggested_action: "Validate the target session exists and is a team session before stopping stale distributed runtimes, or document this RPC as best-effort runtime prep only."
  - id: ISSUE-002
    dimension: error_handling
    severity: low
    status: open
    summary: "Delegated switch failures fall through to the outer generic error path."
    evidence: "prepare_session_switch is awaited without local try/except; _handle_message would convert exceptions to a generic error response without a switch-specific code."
    suggested_action: "Catch delegated failures locally and return a structured switch error code."
  - id: ISSUE-003
    dimension: test_coverage
    severity: medium
    status: open
    summary: "Important validation and failure branches lack direct tests."
    evidence: "Direct tests cover valid team switch and non-team rejection, but not missing session_id, nonexistent target, metadata mismatch, or prepare_session_switch exception behavior."
    suggested_action: "Add focused async handler tests for those branches plus metadata passthrough."
confidence: confirmed
details: {}
---

# `AgentWebSocketServer._handle_session_switch`

## Actual Role

Handles `session.switch` for team-mode sessions by validating a target session id, requiring team-mode params, and delegating stale distributed-runtime cleanup to the channel-scoped `TeamManager.prepare_session_switch`. It sends one E2A-encoded unary response and does not persist active-session metadata or load the target session itself.

## Key Signals

- Input: `params.session_id` wins, then `request.session_id`; `params.mode` or `params.team` controls team-mode acceptance.
- Output: Success payload is `{session_id, mode: "team", switched: true}`; validation failures return `BAD_REQUEST` or `UNSUPPORTED_MODE`.
- Main side effects: Calls `get_team_manager(channel_id).prepare_session_switch(target, reason="session.switch: ")`.
- Main risk: The handler trusts caller-provided mode/target identity without checking persisted session metadata before delegated cleanup.
- Related tests: Direct tests cover team success and non-team rejection; TeamManager tests cover distributed/local stale-runtime behavior.

## Detail Index

- Detail docs pending.
