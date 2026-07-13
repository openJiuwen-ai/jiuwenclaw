---
symbol: AgentWebSocketServer.send_push
kind: method
source: jiuwenswarm/server/agent_ws_server.py
source_role: runtime_source
audit_scope: default_health_audit
class: AgentWebSocketServer
signature: "send_push(self, msg) -> None"
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
  state_mutation: none
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
    dimension: boundary_safety
    severity: medium
    status: open
    summary: "Mutable singleton connection state can target the wrong socket or drop pushes."
    evidence: "_connection_handler assigns and clears _current_ws/_current_send_lock globally, while send_push checks those attributes and later re-reads them for the actual send."
    suggested_action: "Snapshot socket/lock locally and add connection ownership checks, or make replacement Gateway connections explicit."
  - id: ISSUE-002
    dimension: boundary_safety
    severity: medium
    status: open
    summary: "Server push can race ahead of connection.ack."
    evidence: "_connection_handler publishes _current_ws before sending connection.ack, while Gateway connect expects the first frame to be the ack event."
    suggested_action: "Send ack under the send lock before publishing the socket, or gate send_push until ack completion."
  - id: ISSUE-003
    dimension: test_coverage
    severity: medium
    status: open
    summary: "Direct send_push and build_server_push_wire branch tests are missing."
    evidence: "test_gateway_push_transport.py verifies forwarding to send_push, but not socket send behavior; no tests were found for build_server_push_wire response_kind and chunk-style branches."
    suggested_action: "Add fake-WebSocket async tests for no connection, successful send, closed send, snapshot behavior, response_kind, metadata filtering, and session propagation."
confidence: confirmed
details: {}
---

# `AgentWebSocketServer.send_push`

## Actual Role

Encodes an AgentServer-originated push dict with `build_server_push_wire` and sends the resulting E2A server-push JSON through the currently active Gateway WebSocket under its send lock. If no Gateway is connected, or encoding/sending fails, it logs and drops the push.

## Key Signals

- Input: message dict carrying payload or `response_kind` body.
- Output: E2A server-push wire frame sent to Gateway.
- Main side effects: WebSocket send.
- Main risk: one active connection is assumed and ack ordering is shared with `_connection_handler`.
- Related tests: `tests/unit/agentserver/test_gateway_push_transport.py`; direct `send_push` and wire branch tests are pending.

## Detail Index

- Detail docs pending.
