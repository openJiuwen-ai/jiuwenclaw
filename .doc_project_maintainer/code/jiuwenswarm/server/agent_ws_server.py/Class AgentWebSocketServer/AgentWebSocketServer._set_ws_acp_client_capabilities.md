---
symbol: AgentWebSocketServer._set_ws_acp_client_capabilities
kind: method
source: jiuwenswarm/server/agent_ws_server.py
source_role: runtime_source
audit_scope: default_health_audit
class: AgentWebSocketServer
signature: "_set_ws_acp_client_capabilities(self, ws, capabilities) -> None"
health:
  overall: watch
  name_behavior_match: good
  responsibility_focus: single
  length: short
  complexity: low
  implementation_soundness: partial
  boundary_safety: partial
  input_contract: implicit
  output_contract: clear
  side_effects: explicit
  error_handling: partial
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
    dimension: state_mutation
    severity: medium
    status: open
    summary: "ACP capabilities are cached before ACP initialize is known to have succeeded."
    evidence: "_handle_initialize calls this setter before awaiting AgentManager.initialize; the exception path sends an error but does not clear the per-WebSocket cache, and later ACP requests consume cached capabilities."
    suggested_action: "Move the setter after successful initialize, or clear the per-WebSocket cache in the initialize exception path."
  - id: ISSUE-002
    dimension: boundary_safety
    severity: low
    status: open
    summary: "Capability dicts are shallow-copied."
    evidence: "The setter uses dict(capabilities), while tests and downstream code use nested maps such as terminal/create and filesystem capabilities."
    suggested_action: "Deep-copy or normalize the ACP capability schema if caller-side nested mutation is possible."
  - id: ISSUE-003
    dimension: test_coverage
    severity: low
    status: open
    summary: "Tests cover per-WebSocket selection but not cache clearing, non-dict clearing, or failed-initialize rollback."
    evidence: "The ws-scoped ACP test asserts one non-empty capability set is selected, but no test references _clear_ws_acp_client_capabilities, _acp_client_capabilities_by_ws, or connection cleanup."
    suggested_action: "Add focused tests for set/get/clear, non-dict input removal, connection-finally cleanup, and initialize failure rollback."
confidence: confirmed
details: {}
---

# `AgentWebSocketServer._set_ws_acp_client_capabilities`

## Actual Role

Stores a shallow copy of ACP `clientCapabilities` for one WebSocket identity, keyed by `id(ws)`. If the provided value is not a dict, it removes that WebSocket's cached capabilities so later ACP requests fall back to manager-level capabilities or empty metadata.

## Key Signals

- Input: WebSocket-like object and optional capabilities dict.
- Output: None.
- Main side effects: Mutates `self._acp_client_capabilities_by_ws`.
- Main risk: Cache mutation happens before ACP initialize success is known, and nested capability values are only shallow-copied.
- Related tests: `tests/unit_tests/agentserver/test_agentserver_acp.py::test_handle_message_uses_ws_scoped_acp_client_capabilities`; cleanup and failure rollback tests are pending.

## Detail Index

- Detail docs pending.
