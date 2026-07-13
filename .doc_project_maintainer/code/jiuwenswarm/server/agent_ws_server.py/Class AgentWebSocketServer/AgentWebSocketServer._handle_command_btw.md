---
symbol: AgentWebSocketServer._handle_command_btw
kind: method
source: jiuwenswarm/server/agent_ws_server.py
source_role: runtime_source
audit_scope: default_health_audit
class: AgentWebSocketServer
signature: "_handle_command_btw(ws: Any, request: AgentRequest, send_lock: asyncio.Lock) -> None"
health:
  overall: watch
  name_behavior_match: good
  responsibility_focus: single
  length: medium
  complexity: low
  implementation_soundness: partial
  boundary_safety: partial
  input_contract: implicit
  output_contract: implicit
  side_effects: explicit
  error_handling: clear
  state_mutation: shared
  dependency_coupling: medium
  test_coverage: partial
  observability: clear
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
    dimension: performance_risk
    severity: medium
    status: open
    summary: "Long-running /btw calls have no handler-level timeout or cancellation alignment."
    evidence: "The handler awaits agent.generate_btw_answer; the TUI requests 120000 ms, but the gateway TUI unary wait is capped at 55 seconds."
    suggested_action: "Align TUI, gateway, and AgentServer timeout semantics and add cancellation/timeout handling."
  - id: ISSUE-002
    dimension: output_contract
    severity: low
    status: open
    summary: "Adapter payload is returned with ok=true without shape or status validation."
    evidence: "The handler wraps result_data directly, while the frontend expects status values such as ok, no_context, and failed."
    suggested_action: "Normalize non-dict or unknown-status adapter results into a failed payload before sending."
  - id: ISSUE-003
    dimension: boundary_safety
    severity: low
    status: open
    summary: "INFO logging includes the first 100 characters of the user question."
    evidence: "The command.btw received log records question[:100]."
    suggested_action: "Log metadata or question length instead, or move redacted content to debug logging."
  - id: ISSUE-004
    dimension: test_coverage
    severity: low
    status: open
    summary: "Core paths are tested, but boundary handoffs are not fully covered."
    evidence: "Missing coverage includes project_dir forwarding, channel fallback, unknown payload normalization, and slow-call timeout behavior."
    suggested_action: "Add boundary tests for routing, payload normalization, and timeout/cancellation behavior."
confidence: confirmed
details: {}
---

# `AgentWebSocketServer._handle_command_btw`

## Actual Role

Handles non-streaming `command.btw` by validating that `params.question` is non-empty, resolving session/channel/mode/project identity, obtaining the scoped agent, and delegating to `agent.generate_btw_answer`. It sends one response with the adapter payload, or a failed status for empty questions and handler exceptions.

## Key Signals

- Input: `params.question`, optional `params.mode`, session id, channel id, and request project directory.
- Output: One websocket response containing the adapter BTW answer payload, or `status=failed`.
- Main side effects: Calls the runtime adapter, logs receipt/result/failures, and sends a websocket frame.
- Main risk: Timeout semantics differ across TUI/gateway/AgentServer, and the handler trusts adapter payload shape.
- Related tests: BTW command tests cover many core paths; routing, timeout, and payload-normalization edges remain incomplete.

## Detail Index

- Detail docs pending.
