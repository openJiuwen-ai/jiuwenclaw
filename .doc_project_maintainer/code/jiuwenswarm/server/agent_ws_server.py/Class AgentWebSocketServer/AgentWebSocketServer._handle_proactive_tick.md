---
symbol: AgentWebSocketServer._handle_proactive_tick
kind: method
source: jiuwenswarm/server/agent_ws_server.py
source_role: runtime_source
audit_scope: default_health_audit
class: AgentWebSocketServer
signature: "_handle_proactive_tick(ws: Any, request: AgentRequest, send_lock: asyncio.Lock) -> None"
health:
  overall: watch
  name_behavior_match: good
  responsibility_focus: single
  length: medium
  complexity: low
  implementation_soundness: sound
  boundary_safety: partial
  input_contract: implicit
  output_contract: clear
  side_effects: explicit
  error_handling: partial
  state_mutation: shared
  dependency_coupling: medium
  test_coverage: partial
  observability: partial
  performance_risk: medium
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
    summary: "Missing direct coverage for the websocket adapter and proactive cron branch."
    evidence: "ProactiveEngine.tick_now has indirect tests, but no _handle_proactive_tick handler payload or proactive scheduler branch test was found."
    suggested_action: "Add fake-engine and fake-websocket tests for uninitialized, success, skipped, exception, target_channel pass-through, and scheduler envelope mapping."
  - id: ISSUE-002
    dimension: input_contract
    severity: low
    status: open
    summary: "params and target_channel are accepted implicitly from an Any boundary."
    evidence: "_payload_to_request passes raw params; the handler uses request.params.get and forwards target_channel without type normalization."
    suggested_action: "Guard request.params as a dict and normalize target_channel to str or None before calling the engine."
confidence: confirmed
details: {}
---

# `AgentWebSocketServer._handle_proactive_tick`

## Actual Role

Handles gateway cron `proactive.tick` requests by checking for an injected ProactiveEngine, forwarding optional `params.target_channel` to `tick_now`, mapping the boolean result to a status payload, and sending one E2A AgentResponse. Recommendation, profile, and delivery work stays inside ProactiveEngine.

## Key Signals

- Input: `AgentRequest.params.target_channel`, websocket, and send lock.
- Output: One encoded AgentResponse with `success` and status, or an initialization/error payload.
- Main side effects: May trigger proactive profile/recommendation work and sends a WebSocket frame.
- Main risk: Cross-process adapter params are implicit and lack direct handler coverage.
- Related tests: ProactiveEngine flow tests exist; direct handler and cron branch tests were not found.

## Detail Index

- Detail docs pending.
