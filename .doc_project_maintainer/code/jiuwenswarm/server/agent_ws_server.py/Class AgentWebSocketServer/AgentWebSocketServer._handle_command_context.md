---
symbol: AgentWebSocketServer._handle_command_context
kind: method
source: jiuwenswarm/server/agent_ws_server.py
source_role: runtime_source
audit_scope: default_health_audit
class: AgentWebSocketServer
signature: "_handle_command_context(ws: Any, request: AgentRequest, send_lock: asyncio.Lock) -> None"
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
  state_mutation: shared
  dependency_coupling: medium
  test_coverage: missing
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
    summary: "No direct command.context tests cover dispatch, routing, success payload, or error envelope."
    evidence: "Existing tests exercise mode resolution and nearby command edges, but no test directly targets _handle_command_context."
    suggested_action: "Add direct handler tests for agent lookup args, successful payload forwarding, missing agent, and adapter exception responses."
confidence: confirmed
details: {}
---

# `AgentWebSocketServer._handle_command_context`

## Actual Role

Handles `command.context` as a transport bridge: it resolves session, mode, sub-mode, channel, and project directory, obtains the scoped agent, forwards `agent.get_context_usage(session_id=...)`, and sends the returned payload as one response.

## Key Signals

- Input: `session_id`, optional `params.mode`, channel id, and request project directory.
- Output: One websocket response containing the adapter context-usage payload, or an error payload.
- Main side effects: Calls the runtime adapter and sends a websocket frame.
- Main risk: Contract and error semantics are implicit and untested at this handler boundary.
- Related tests: No direct _handle_command_context tests were found.

## Detail Index

- Detail docs pending.
