---
symbol: AgentWebSocketServer.host
kind: method
source: jiuwenswarm/server/agent_ws_server.py
source_role: runtime_source
audit_scope: default_health_audit
class: AgentWebSocketServer
signature: "host(self) -> str"
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
  side_effects: none
  error_handling: clear
  state_mutation: none
  dependency_coupling: low
  test_coverage: missing
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
    summary: "No direct test asserts the host accessor returns the configured bind host."
    evidence: "No .host property test was found; startup uses _host directly and existing server tests use fakes or mocks."
    suggested_action: "Add a tiny constructor/accessor test if host configuration becomes externally relied on."
confidence: confirmed
details: {}
---

# `AgentWebSocketServer.host`

## Actual Role

Read-only property that returns the WebSocket server bind host stored in `self._host`. Startup binds with `_host` directly, so this method exposes configured state rather than participating in the listener startup path.

## Key Signals

- Input: Initialized `AgentWebSocketServer` instance.
- Output: Configured host string.
- Main side effects: None.
- Main risk: The accessor relies on constructor-initialized state and currently has no direct regression test.
- Related tests: No direct `.host` property coverage was found; constructor paths are only exercised indirectly.

## Detail Index

- Detail docs pending.
