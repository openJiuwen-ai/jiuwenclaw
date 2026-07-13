---
symbol: AgentWebSocketServer._ensure_persistent_checkpointer_response
kind: method
source: jiuwenswarm/server/agent_ws_server.py
source_role: runtime_source
audit_scope: default_health_audit
class: AgentWebSocketServer
signature: "_ensure_persistent_checkpointer_response(request: AgentRequest) -> AgentResponse | None"
health:
  overall: watch
  name_behavior_match: good
  responsibility_focus: single
  length: short
  complexity: low
  implementation_soundness: sound
  boundary_safety: safe
  input_contract: clear
  output_contract: clear
  side_effects: explicit
  error_handling: clear
  state_mutation: global
  dependency_coupling: medium
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
    summary: "No direct helper test or live E2A wire assertion for CHECKPOINT_UNAVAILABLE."
    evidence: "Tests assert the legacy-shaped fake encoder output through delete handlers, but search found no test reference to _ensure_persistent_checkpointer_response and targeted pytest could not run in the current environment."
    suggested_action: "Add a direct async unit test for the helper's success/failure return values and a wire-normalization assertion if CHECKPOINT_UNAVAILABLE must remain visible in E2A details."
confidence: confirmed
details: {}
---

# `AgentWebSocketServer._ensure_persistent_checkpointer_response`

## Actual Role

Acts as a fail-closed delete-flow guard for persistent checkpoint storage. It imports and awaits the DeepAdapter persistent checkpointer initializer; on success it returns `None`, and on failure it logs the exception and returns an `AgentResponse` with `ok=false`, `code=CHECKPOINT_UNAVAILABLE`, request/channel identity, and request metadata preserved.

## Key Signals

- Input: Active `AgentRequest`; uses request id, channel id, and metadata in the fallback response.
- Output: `None` when persistent checkpointer setup succeeds, or a structured error `AgentResponse` when setup fails.
- Main side effects: May initialize the process-wide sqlite persistence checkpointer and logs exceptions on failure.
- Main risk: Behavior is only tested through delete handlers, not as a direct helper or live E2A wire contract.
- Related tests: Adjacent tests cover `team.delete` and `session.delete` checkpointer-unavailable responses and session.delete success initialization.

## Detail Index

- Detail docs pending.
