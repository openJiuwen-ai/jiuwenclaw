---
symbol: AgentWebSocketServer._handle_command_chrome
kind: method
source: jiuwenswarm/server/agent_ws_server.py
source_role: runtime_source
audit_scope: default_health_audit
class: AgentWebSocketServer
signature: "_handle_command_chrome(ws: Any, request: AgentRequest, send_lock: asyncio.Lock) -> None"
health:
  overall: watch
  name_behavior_match: partial
  responsibility_focus: single
  length: short
  complexity: low
  implementation_soundness: partial
  boundary_safety: partial
  input_contract: implicit
  output_contract: implicit
  side_effects: explicit
  error_handling: partial
  state_mutation: none
  dependency_coupling: low
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
    dimension: output_contract
    severity: medium
    status: open
    summary: "Reports success with an empty payload without executing or describing a Chrome action."
    evidence: "The normal path builds ok=true with payload={}, while the frontend success copy says the Chrome command was dispatched."
    suggested_action: "Implement the intended Chrome behavior or make the route an explicit no-op/status acknowledgement."
  - id: ISSUE-002
    dimension: boundary_safety
    severity: medium
    status: open
    summary: "The frontend chrome command file appears disconnected from builtin command registration."
    evidence: "chrome.ts exports createChromeCommand, but registry.ts createBuiltinCommands does not import or include it."
    suggested_action: "Register the command path intentionally, or remove the dead builtin and forwarding surface."
  - id: ISSUE-003
    dimension: test_coverage
    severity: medium
    status: open
    summary: "No direct handler, dispatch, or frontend registration tests cover command.chrome."
    evidence: "Searches found no direct tests for _handle_command_chrome or the chrome builtin registration path."
    suggested_action: "Add tests for dispatch, response payload semantics, and command registration."
confidence: confirmed
details: {}
---

# `AgentWebSocketServer._handle_command_chrome`

## Actual Role

Acknowledges `command.chrome` by sending one successful AgentResponse with an empty payload. It does not read params, route to Chrome integration code, or mutate AgentServer state.

## Key Signals

- Input: The request id and channel id are only used to form the response envelope.
- Output: One websocket response with `ok=true` and `{}` payload, or an error payload if response construction fails.
- Main side effects: Sends a websocket frame and logs exceptions.
- Main risk: The route looks like a real Chrome command but currently behaves as a no-op acknowledgement.
- Related tests: No direct handler, dispatch, or frontend builtin registration tests were found.

## Detail Index

- Detail docs pending.
