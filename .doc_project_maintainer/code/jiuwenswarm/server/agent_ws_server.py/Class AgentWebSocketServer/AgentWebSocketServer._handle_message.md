---
symbol: AgentWebSocketServer._handle_message
kind: method
source: jiuwenswarm/server/agent_ws_server.py
source_role: runtime_source
audit_scope: default_health_audit
class: AgentWebSocketServer
signature: "_handle_message(self, ws: Any, raw: str | bytes, send_lock: asyncio.Lock) -> None"
health:
  overall: risky
  name_behavior_match: good
  responsibility_focus: overloaded
  length: excessive
  complexity: high
  implementation_soundness: partial
  boundary_safety: risky
  input_contract: weak
  output_contract: implicit
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
    dimension: complexity
    severity: medium
    status: open
    summary: "Large method is the central routing table for many runtime RPC families."
    evidence: "It parses JSON/E2A/legacy payloads, injects ACP metadata, triggers hooks, dispatches dozens of ReqMethod branches, handles cancel semantics, and sends fallback errors."
    suggested_action: "Keep branch-specific tests and document handler families before refactoring."
  - id: ISSUE-002
    dimension: boundary_safety
    severity: high
    status: open
    summary: "Malformed non-JSON-error payloads can escape before normalized error handling."
    evidence: "After json.loads succeeds, E2A/legacy conversion runs before the main request-handling try; non-object JSON or missing legacy_agent_request can raise before the fallback error response path has a request context."
    suggested_action: "Validate decoded JSON is a dict and wrap E2A/legacy conversion in the same error-normalization path."
  - id: ISSUE-003
    dimension: test_coverage
    severity: medium
    status: open
    summary: "Direct router coverage misses malformed converted payloads and many dispatch branches."
    evidence: "Direct tests cover closed-WebSocket handling, invalid JSON sends, and ACP-scoped capabilities, but not non-object JSON, invalid fallback envelopes, unknown legacy req_method, or representative dispatch routing."
    suggested_action: "Add direct router tests for malformed E2A/legacy inputs and selected high-risk ReqMethod branches."
confidence: confirmed
details: {}
---

# `AgentWebSocketServer._handle_message`

## Actual Role

Parses one inbound WebSocket frame, converts E2A or legacy payloads into an `AgentRequest`, enriches ACP metadata, triggers before-chat hooks, and routes many `ReqMethod` families to local handlers or unary/streaming agent execution. It normalizes JSON parse errors and most handler failures into wire responses when the WebSocket is still open, but some conversion failures happen before that guarded path.

## Key Signals

- Input: raw WebSocket JSON frame and send lock.
- Output: response sent to WebSocket or delegated to another handler.
- Main side effects: local handler dispatch, canceling stream tasks, extension hook invocation.
- Main risk: high branch count plus unguarded E2A/legacy conversion before the request error path.
- Related tests: `test_agentserver_modes.py`, `test_agentserver_acp.py`, `test_agentserver_cli_commands.py`, `test_agent_ws_connection_close.py`.

## Detail Index

- Detail docs pending.
