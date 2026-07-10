---
symbol: AgentWebSocketServer._ws_capabilities_key
kind: method
source: jiuwenswarm/server/agent_ws_server.py
source_role: runtime_source
audit_scope: default_health_audit
class: AgentWebSocketServer
signature: "_ws_capabilities_key(ws) -> int"
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
  dependency_coupling: low
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
    dimension: boundary_safety
    severity: low
    status: open
    summary: "id(ws) is safe only while stale cache entries are reliably cleared."
    evidence: "The method returns id(ws), and the cache is keyed by int while cleanup happens externally in _connection_handler.finally."
    suggested_action: "Keep cleanup coverage strong; consider object-keyed weak storage if the cache grows beyond tightly scoped connection lifecycle use."
  - id: ISSUE-002
    dimension: test_coverage
    severity: low
    status: open
    summary: "No direct test asserts key behavior or clear-after-lifecycle semantics."
    evidence: "Only source references to _ws_capabilities_key were found; related behavior is covered indirectly through the ws-scoped ACP capability flow."
    suggested_action: "Add a small set/get/clear test with two websocket objects if this helper remains part of the cache contract."
confidence: confirmed
details: {}
---

# `AgentWebSocketServer._ws_capabilities_key`

## Actual Role

Returns `id(ws)` as the integer identity key used by adjacent helpers to store, retrieve, and clear per-WebSocket ACP client capabilities. The method itself is pure and tiny; correctness depends on surrounding cache lifecycle cleanup.

## Key Signals

- Input: WebSocket-like object accepted as `Any`.
- Output: Python object identity integer from `id(ws)`.
- Main side effects: None.
- Main risk: Stale cache entries could be mis-associated if cleanup is missed and Python later reuses an object id.
- Related tests: `tests/unit_tests/agentserver/test_agentserver_acp.py::test_handle_message_uses_ws_scoped_acp_client_capabilities` covers related behavior indirectly.

## Detail Index

- Detail docs pending.
