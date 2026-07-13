---
symbol: AgentWebSocketServer._mask_sensitive_fields
kind: method
source: jiuwenswarm/server/agent_ws_server.py
source_role: runtime_source
audit_scope: default_health_audit
class: AgentWebSocketServer
signature: "_mask_sensitive_fields(payload: Any) -> Any"
health:
  overall: risky
  name_behavior_match: good
  responsibility_focus: single
  length: short
  complexity: low
  implementation_soundness: partial
  boundary_safety: risky
  input_contract: implicit
  output_contract: clear
  side_effects: none
  error_handling: partial
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
    severity: high
    status: open
    summary: "Common MCP credential headers can be returned unmasked."
    evidence: "The helper only matches api_key with an underscore plus token/authorization/secret; MCP config accepts arbitrary headers and a key like x-api-key does not match."
    suggested_action: "Normalize key separators and expand the sensitive vocabulary, or mask all values under env and headers by default."
  - id: ISSUE-002
    dimension: test_coverage
    severity: medium
    status: open
    summary: "Masking behavior has only indirect narrow coverage."
    evidence: "Observed test coverage asserts env.TOKEN masking through command.mcp list only; no direct tests cover recursion, headers, password-like keys, or hyphenated API-key names."
    suggested_action: "Add direct unit tests for nested env/headers, Authorization, x-api-key, password/passwd, and non-sensitive fields."
confidence: confirmed
details: {}
---

# `AgentWebSocketServer._mask_sensitive_fields`

## Actual Role

Recursively copies dict/list payloads and replaces values with `***` when key names contain `api_key`, `token`, `authorization`, or `secret`, or when string values contain bearer/API-key/secret markers. It is used as the response-side masking boundary for `command.mcp` config items.

## Key Signals

- Input: Any payload, practically MCP server config dicts/lists from config and normalized command payloads.
- Output: Same JSON-like shape with matched sensitive values masked; primitives are returned unchanged.
- Main side effects: None.
- Main risk: Pattern-based masking misses common credential names such as `x-api-key`, `password`, `credential`, `access_key`, and generic auth headers.
- Related tests: One indirect command.mcp list test asserts `env.TOKEN` is masked.

## Detail Index

- Detail docs pending.
