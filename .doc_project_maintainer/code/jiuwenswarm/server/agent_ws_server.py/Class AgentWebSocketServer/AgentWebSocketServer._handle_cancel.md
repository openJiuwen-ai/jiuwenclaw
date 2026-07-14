---
symbol: AgentWebSocketServer._handle_cancel
kind: method
source: jiuwenswarm/server/agent_ws_server.py
source_role: runtime_source
audit_scope: default_health_audit
class: AgentWebSocketServer
signature: "_handle_cancel(self, ws: Any, request: AgentRequest, send_lock: asyncio.Lock) -> None"
health:
  overall: watch
  name_behavior_match: good
  responsibility_focus: mixed
  length: long
  complexity: high
  implementation_soundness: partial
  boundary_safety: partial
  input_contract: implicit
  output_contract: clear
  side_effects: explicit
  error_handling: partial
  state_mutation: shared
  dependency_coupling: high
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
    dimension: boundary_safety
    severity: medium
    status: open
    summary: "Missing team mode params can make cancel cleanup run too late for team cancel semantics."
    evidence: "_handle_cancel calls agent.process_message before checking team runtime state; generic interrupt handling may terminate runtime before this method can invoke cancel_session_runtime for missing-mode team requests."
    suggested_action: "Snapshot team runtime before agent.process_message or otherwise ensure missing-mode team cancel requests invoke cancel_session_runtime."
  - id: ISSUE-002
    dimension: test_coverage
    severity: medium
    status: open
    summary: "No direct server-side tests cover _handle_cancel selection and team cleanup."
    evidence: "Related tests cover adapter interrupts and Gateway cancel emission, but not AgentWebSocketServer._handle_cancel's existing-agent reuse, fallback, missing-mode team cancel, or response send."
    suggested_action: "Add focused tests for existing-agent reuse, fallback avoidance, missing-mode team cancel, supplement team terminate, and response send."
confidence: confirmed
details: {}
---

# `AgentWebSocketServer._handle_cancel`

## Actual Role

Processes `CHAT_CANCEL` after `_handle_message` has already canceled any tracked stream task for cancel/supplement intents. It selects an existing cached agent when possible, delegates interrupt semantics to `agent.process_message`, then performs opportunistic team-runtime cleanup and sends the encoded response.

## Key Signals

- Input: `CHAT_CANCEL` request and intent parameters.
- Output: E2A response acknowledging interrupt handling.
- Main side effects: active agent interruption, optional team runtime cleanup, and WebSocket response send.
- Main risk: team runtime cleanup is checked after agent interrupt handling.
- Related tests: adapter interrupt and Gateway cancel emission tests; direct `_handle_cancel` tests are missing.

## Detail Index

- Detail docs pending.
