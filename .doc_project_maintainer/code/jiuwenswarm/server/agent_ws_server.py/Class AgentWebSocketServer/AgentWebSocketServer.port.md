---
symbol: AgentWebSocketServer.port
kind: method
source: jiuwenswarm/server/agent_ws_server.py
source_role: runtime_source
audit_scope: default_health_audit
class: AgentWebSocketServer
signature: "port(self) -> int"
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
    summary: "No direct test asserts the port accessor returns the constructor-supplied bind port."
    evidence: "No AgentWebSocketServer.port property test was found; startup uses _port directly and app-level tests mock the server instance."
    suggested_action: "Add a small constructor/accessor test if external code depends on this property."
confidence: confirmed
details: {}
---

# `AgentWebSocketServer.port`

## Actual Role

Read-only property that returns the constructor-captured WebSocket bind port from `self._port`. It performs no validation, fallback, mutation, I/O, logging, or synchronization.

## Key Signals

- Input: Initialized `AgentWebSocketServer` instance.
- Output: Configured port integer.
- Main side effects: None.
- Main risk: The accessor is simple but lacks a direct regression test for externally visible configuration.
- Related tests: No direct `.port` property coverage was found; constructor and startup behavior are covered only indirectly or through fakes.

## Detail Index

- Detail docs pending.
