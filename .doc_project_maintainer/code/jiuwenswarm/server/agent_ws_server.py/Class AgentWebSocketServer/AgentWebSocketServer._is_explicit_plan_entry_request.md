---
symbol: AgentWebSocketServer._is_explicit_plan_entry_request
kind: method
source: jiuwenswarm/server/agent_ws_server.py
source_role: runtime_source
audit_scope: default_health_audit
class: AgentWebSocketServer
signature: "_is_explicit_plan_entry_request(request: AgentRequest) -> bool"
health:
  overall: watch
  name_behavior_match: good
  responsibility_focus: single
  length: short
  complexity: low
  implementation_soundness: sound
  boundary_safety: partial
  input_contract: implicit
  output_contract: clear
  side_effects: none
  error_handling: clear
  state_mutation: none
  dependency_coupling: medium
  test_coverage: partial
  observability: not_applicable
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
    dimension: test_coverage
    severity: low
    status: open
    summary: "No direct unit test covers the predicate boundary cases."
    evidence: "No tests were found invoking _is_explicit_plan_entry_request directly; only the indirect re-entry test passes plan_entry_source: slash_command."
    suggested_action: "Add a direct unit test matrix for non-dict params, missing key, wrong value, and slash_command."
  - id: ISSUE-002
    dimension: input_contract
    severity: low
    status: open
    summary: "The cross-layer plan_entry_source contract is implicit and string-literal based."
    evidence: "AgentRequest.params is an untyped dict payload, TUI emits plan_entry_source, and the backend checks the literal slash_command."
    suggested_action: "Document or centralize accepted plan_entry_source values, or add an integration test pinning TUI slash-command serialization to backend behavior."
confidence: confirmed
details: {}
---

# `AgentWebSocketServer._is_explicit_plan_entry_request`

## Actual Role

Side-effect-free static predicate that returns true only when `request.params` is a dict and `plan_entry_source` is exactly `"slash_command"`. `_ensure_code_mode_state` uses that true result during normal-to-plan sync to bypass stale plan-exit guards for an explicit `/plan` re-entry.

## Key Signals

- Input: `AgentRequest`, with unchecked `params` from the WebSocket payload.
- Output: Boolean only.
- Main side effects: None.
- Main risk: The cross-layer marker is an implicit string literal shared with TUI request serialization.
- Related tests: Indirect coverage exists for the slash-command true path; no direct helper test covers false cases.

## Detail Index

- Detail docs pending.
