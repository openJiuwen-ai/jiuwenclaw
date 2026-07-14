---
symbol: AgentWebSocketServer._get_ws_acp_client_capabilities
kind: method
source: jiuwenswarm/server/agent_ws_server.py
source_role: runtime_source
audit_scope: default_health_audit
class: AgentWebSocketServer
signature: "_get_ws_acp_client_capabilities(self, ws) -> dict[str, Any]"
health:
  overall: watch
  name_behavior_match: good
  responsibility_focus: single
  length: short
  complexity: low
  implementation_soundness: partial
  boundary_safety: partial
  input_contract: implicit
  output_contract: weak
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
    dimension: output_contract
    severity: medium
    status: open
    summary: "Getter cannot distinguish missing cache from explicitly empty capabilities."
    evidence: "The setter stores any dict, including an empty dict; the getter returns an empty dict for both missing and empty values, and the caller uses ws_caps or manager fallback."
    suggested_action: "Return None for no entry or add a presence-aware helper, then fall back only when no WebSocket-scoped value exists."
  - id: ISSUE-002
    dimension: test_coverage
    severity: low
    status: open
    summary: "Existing coverage validates non-empty per-WebSocket behavior but not getter edge cases."
    evidence: "The related ACP test asserts a non-empty terminal capability map; no direct getter tests were found for no-entry, top-level copy isolation, or explicit empty capabilities."
    suggested_action: "Add focused helper tests for no entry, copy isolation, and explicit empty capabilities."
confidence: confirmed
details: {}
---

# `AgentWebSocketServer._get_ws_acp_client_capabilities`

## Actual Role

Returns a top-level copy of cached ACP client capabilities for the given WebSocket identity, keyed by `id(ws)`. It supports later ACP requests using connection-scoped capability metadata instead of relying only on AgentManager's channel-level cache.

## Key Signals

- Input: WebSocket-like object accepted as `Any`.
- Output: A copied capabilities dict, or `{}` when no dict is cached.
- Main side effects: None.
- Main risk: `{}` means both no cache entry and explicit empty capabilities, so the caller can accidentally fall back to stale manager-level capabilities.
- Related tests: `tests/unit_tests/agentserver/test_agentserver_acp.py::test_handle_message_uses_ws_scoped_acp_client_capabilities`; direct getter and explicit-empty tests are pending.

## Detail Index

- Detail docs pending.
