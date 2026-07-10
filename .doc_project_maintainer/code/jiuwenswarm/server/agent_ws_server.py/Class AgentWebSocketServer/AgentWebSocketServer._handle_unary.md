---
symbol: AgentWebSocketServer._handle_unary
kind: method
source: jiuwenswarm/server/agent_ws_server.py
source_role: runtime_source
audit_scope: default_health_audit
class: AgentWebSocketServer
signature: "_handle_unary(self, ws: Any, request: AgentRequest, send_lock: asyncio.Lock) -> None"
health:
  overall: watch
  name_behavior_match: good
  responsibility_focus: mixed
  length: medium
  complexity: medium
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
    dimension: error_handling
    severity: medium
    status: open
    summary: "Post-process plan-exit check can mask the original agent failure."
    evidence: "_handle_unary awaits _check_post_process_plan_exit in a finally block after agent.process_message; if both raise, the post-process exception replaces the original failure before _handle_message builds the error response."
    suggested_action: "Guard post-process plan-exit checking so it logs its own failure without masking process_message exceptions."
  - id: ISSUE-002
    dimension: test_coverage
    severity: medium
    status: open
    summary: "No direct tests exercise the real _handle_unary success and send-failure branches."
    evidence: "Existing closed-WebSocket tests override _handle_unary to raise; encoder and outer dispatcher paths are covered, but real stateless and code-mode unary paths are not directly verified."
    suggested_action: "Add direct fake-agent tests for stateless success, code-mode success, process_message exception, and send failure propagation through _handle_message."
confidence: confirmed
details: {}
---

# `AgentWebSocketServer._handle_unary`

## Actual Role

Handles non-stream requests by routing initialize/session/tool-response methods to dedicated handlers, sending stateless skill/plugin requests directly to an agent, and otherwise preparing code-mode state before `agent.process_message`. It encodes and sends one E2A response, leaving closed-WebSocket and generic error normalization to `_handle_message`.

## Key Signals

- Input: non-stream `AgentRequest`.
- Output: one E2A response frame.
- Main side effects: agent invocation, possible code-mode state restore and plan-mode exit push.
- Main risk: post-process plan-exit checks share the same exception boundary as agent processing.
- Related tests: ACP and CLI command tests, mode tests, wire codec tests; direct real `_handle_unary` tests are missing.

## Detail Index

- Detail docs pending.
