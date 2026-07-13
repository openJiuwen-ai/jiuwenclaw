---
symbol: AgentWebSocketServer._process_request
kind: method
source: jiuwenswarm/server/agent_ws_server.py
source_role: runtime_source
audit_scope: default_health_audit
class: AgentWebSocketServer
signature: "_process_request(self, *args)"
health:
  overall: watch
  name_behavior_match: good
  responsibility_focus: single
  length: short
  complexity: low
  implementation_soundness: sound
  boundary_safety: partial
  input_contract: implicit
  output_contract: implicit
  side_effects: explicit
  error_handling: partial
  state_mutation: none
  dependency_coupling: medium
  test_coverage: missing
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
    severity: medium
    status: open
    summary: "Missing direct tests for origin allow/reject behavior and both websockets callback shapes."
    evidence: "start() registers _process_request with legacy and current websockets serve APIs, but no direct tests were found for disabled checks, allowed origins, rejected origins, missing Origin, or legacy/current response shapes."
    suggested_action: "Add async unit tests for disabled origin checking, allowed origin, rejected origin, absent Origin, legacy tuple response, and current Response behavior."
confidence: confirmed
details: {}
---

# `AgentWebSocketServer._process_request`

## Actual Role

WebSocket handshake hook registered as `process_request` during server startup. It extracts request data from either legacy `(path, headers)` arguments or current websockets request objects, reads the `Origin` header, skips validation unless origin checking is enabled, and returns a 403 handshake response for disallowed browser origins.

## Key Signals

- Input: Variable `process_request` callback arguments from legacy or current websockets APIs.
- Output: `None` to continue the handshake, or a forbidden handshake response from `forbidden_origin_response(args)`.
- Main side effects: Emits info/warning logs for origin-check state and rejected origins.
- Main risk: This is a security-boundary hook whose behavior is environment and helper driven, but it lacks direct allow/reject tests.
- Related tests: No direct `_process_request` or origin response-shape tests were found.

## Detail Index

- Detail docs pending.
