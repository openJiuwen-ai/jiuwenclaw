---
symbol: AgentWebSocketServer._push_plan_mode_exited
kind: method
source: jiuwenswarm/server/agent_ws_server.py
source_role: runtime_source
audit_scope: default_health_audit
class: AgentWebSocketServer
signature: "_push_plan_mode_exited(request: AgentRequest) -> None"
health:
  overall: watch
  name_behavior_match: good
  responsibility_focus: single
  length: short
  complexity: low
  implementation_soundness: sound
  boundary_safety: partial
  input_contract: implicit
  output_contract: implicit
  side_effects: explicit
  error_handling: partial
  state_mutation: none
  dependency_coupling: medium
  test_coverage: missing
  observability: partial
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
    severity: medium
    status: open
    summary: "Missing direct tests for the plan-mode-exited push contract."
    evidence: "Search found only tests that mock _push_plan_mode_exited or test generic push forwarding; no test asserts no-session behavior or the emitted event_type, mode, channel, and session payload."
    suggested_action: "Add focused async tests for missing session_id, explicit/default channel_id, and send_push payload shape for plan.mode_exited."
  - id: ISSUE-002
    dimension: output_contract
    severity: low
    status: open
    summary: "The emitted mode field is not preserved by the WebChannel structured-event path."
    evidence: "The helper sends payload.mode=code.normal, but WebChannel does not include plan.mode_exited in its full-payload allowlist, so the frontend currently relies on its own default."
    suggested_action: "Either add plan.mode_exited to the structured WebChannel event allowlist with a gateway/frontend test, or document that frame.event alone is the contract."
confidence: confirmed
details: {}
---

# `AgentWebSocketServer._push_plan_mode_exited`

## Actual Role

Sends a server-push notification that a plan-mode session has returned to `code.normal`. It returns without side effects when `request.session_id` is missing; otherwise it forwards a push payload through `send_push` with `event_type: plan.mode_exited`, the request channel or `"default"`, the session id, and `mode: code.normal`.

## Key Signals

- Input: `AgentRequest`; requires `request.session_id`, uses `request.channel_id` or `"default"`.
- Output: None; emits a server-push payload containing `event_type=plan.mode_exited` and `mode=code.normal`.
- Main side effects: Calls `AgentWebSocketServer.send_push`, which writes an E2A server-push frame to the active Gateway WebSocket.
- Main risk: The direct payload contract is not pinned by tests, and one gateway path may drop `payload.mode`.
- Related tests: Indirect orchestration tests mock this helper; no direct payload or transport test was found.

## Detail Index

- Detail docs pending.
