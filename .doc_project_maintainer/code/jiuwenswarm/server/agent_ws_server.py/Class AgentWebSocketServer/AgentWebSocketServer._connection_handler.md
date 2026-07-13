---
symbol: AgentWebSocketServer._connection_handler
kind: method
source: jiuwenswarm/server/agent_ws_server.py
source_role: runtime_source
audit_scope: default_health_audit
class: AgentWebSocketServer
signature: "_connection_handler(self, ws: Any) -> None"
health:
  overall: watch
  name_behavior_match: good
  responsibility_focus: mixed
  length: long
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
    dimension: state_mutation
    severity: high
    status: open
    summary: "Any connection close clears global active-connection state and cancels global work."
    evidence: "The handler assigns _current_ws/_current_send_lock before ack, then unconditionally clears them and cancels all inflight agent/team work plus session stream tasks in finally."
    suggested_action: "Make connection ownership explicit and only clear/cancel global state if the closing connection still owns the active slot, or reject replacement connections deliberately."
  - id: ISSUE-002
    dimension: state_mutation
    severity: medium
    status: open
    summary: "Scheduler shutdown is tied to per-connection cleanup."
    evidence: "_handle_schedule_request lazily starts _scheduler_service, but _connection_handler calls _stop_scheduler() in every connection finally block while the comment says server shutdown."
    suggested_action: "Move scheduler stop to process/server shutdown, or document and test that scheduler lifetime is intentionally Gateway-connection-scoped."
  - id: ISSUE-003
    dimension: boundary_safety
    severity: medium
    status: open
    summary: "connection.ack ordering can race with server push."
    evidence: "_current_ws is published before the ack send; send_push uses that socket and lock immediately, while the Gateway client expects the first frame to be connection.ack."
    suggested_action: "Send ack under the same lock before publishing _current_ws, or gate send_push until ack completes."
confidence: confirmed
details: {}
---

# `AgentWebSocketServer._connection_handler`

## Actual Role

Handles one Gateway WebSocket connection by publishing it as the active server-push socket, sending the initial `connection.ack`, and faning inbound frames into concurrent `_handle_message` tasks. On connection exit it clears per-WebSocket ACP capabilities, cancels connection tasks, cancels global agent and team work, stops scheduler state, gathers outstanding tasks, and clears tracked session stream tasks.

## Key Signals

- Input: a WebSocket connection object.
- Output: no direct return value; sends ack, dispatches messages, and performs cleanup.
- Main side effects: mutates active connection state, starts request tasks, cancels inflight runtime work, clears ACP capability and session stream state.
- Main risk: global cleanup is not guarded by connection ownership, so overlapping connections can interfere with each other.
- Related tests: closed-WebSocket paths in `test_agent_ws_connection_close.py` and indirect system WebSocket coverage; no direct `_connection_handler` tests for ack ordering or overlapping connections found.

## Detail Index

- Detail docs pending.
