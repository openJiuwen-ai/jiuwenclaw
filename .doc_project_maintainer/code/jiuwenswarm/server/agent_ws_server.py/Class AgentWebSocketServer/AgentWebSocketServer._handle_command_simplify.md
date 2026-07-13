---
symbol: AgentWebSocketServer._handle_command_simplify
kind: method
source: jiuwenswarm/server/agent_ws_server.py
source_role: runtime_source
audit_scope: default_health_audit
class: AgentWebSocketServer
signature: "_handle_command_simplify(ws: Any, request: AgentRequest, send_lock: asyncio.Lock) -> None"
health:
  overall: watch
  name_behavior_match: good
  responsibility_focus: single
  length: short
  complexity: low
  implementation_soundness: sound
  boundary_safety: safe
  input_contract: implicit
  output_contract: clear
  side_effects: explicit
  error_handling: partial
  state_mutation: none
  dependency_coupling: low
  test_coverage: partial
  observability: clear
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
    summary: "Missing boundary and failure-path tests for a cross-boundary command handler."
    evidence: "Direct tests cover only prompt shape and target append; gateway/TUI forwarding, dispatcher selection, and non-dict or malformed params are untested."
    suggested_action: "Add one dispatcher or gateway/TUI request-path test plus one malformed params or error response case."
confidence: confirmed
details: {}
---

# `AgentWebSocketServer._handle_command_simplify`

## Actual Role

Handles the unary `command.simplify` route by reading optional `params.target`, building the `/simplify` review prompt with `_build_simplify_prompt`, and returning that prompt to the caller. It does not run the review itself.

## Key Signals

- Input: Optional `params.target`.
- Output: One websocket response with `payload.prompt`, or an error payload.
- Main side effects: Sends a websocket frame and logs caught exceptions.
- Main risk: Cross-boundary forwarding and malformed-param behavior are not directly locked down by tests.
- Related tests: Direct tests cover prompt shape without target and Additional Focus with target.

## Detail Index

- Detail docs pending.
