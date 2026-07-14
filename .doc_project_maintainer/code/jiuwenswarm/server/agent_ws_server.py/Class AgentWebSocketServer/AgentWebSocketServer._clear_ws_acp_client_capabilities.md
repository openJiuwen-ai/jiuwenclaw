---
symbol: AgentWebSocketServer._clear_ws_acp_client_capabilities
kind: method
source: jiuwenswarm/server/agent_ws_server.py
source_role: runtime_source
audit_scope: default_health_audit
class: AgentWebSocketServer
signature: "_clear_ws_acp_client_capabilities(self, ws) -> None"
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
  error_handling: clear
  state_mutation: shared
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
    dimension: test_coverage
    severity: low
    status: open
    summary: "No direct test asserts WebSocket capability cache cleanup."
    evidence: "Related ACP tests cover initialize storage and ws-scoped reads, but not this helper's removal behavior or _connection_handler cleanup on disconnect."
    suggested_action: "Add a narrow unit test that seeds _acp_client_capabilities_by_ws, calls _clear_ws_acp_client_capabilities(ws), and asserts only that WebSocket entry is removed."
confidence: confirmed
details: {}
---

# `AgentWebSocketServer._clear_ws_acp_client_capabilities`

## Actual Role

Removes the cached ACP client-capability dict for one WebSocket identity by popping `id(ws)` from `_acp_client_capabilities_by_ws`. It is the per-connection cleanup counterpart to initialize-time storage and request-time metadata injection.

## Key Signals

- Input: WebSocket-like object accepted as `Any`.
- Output: None.
- Main side effects: Removes one key from `self._acp_client_capabilities_by_ws` when present.
- Main risk: Lifecycle depends on `_connection_handler.finally` calling it; direct cleanup behavior is not test-asserted.
- Related tests: `tests/unit_tests/agentserver/test_agentserver_acp.py` covers initialize storage and ws-scoped reads; clear/removal tests are pending.

## Detail Index

- Detail docs pending.
