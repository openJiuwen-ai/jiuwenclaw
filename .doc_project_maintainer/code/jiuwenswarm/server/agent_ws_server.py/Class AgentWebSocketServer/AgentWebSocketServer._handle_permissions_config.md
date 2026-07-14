---
symbol: AgentWebSocketServer._handle_permissions_config
kind: method
source: jiuwenswarm/server/agent_ws_server.py
source_role: runtime_source
audit_scope: default_health_audit
class: AgentWebSocketServer
signature: "_handle_permissions_config(ws: Any, request: AgentRequest, send_lock: asyncio.Lock) -> None"
health:
  overall: watch
  name_behavior_match: good
  responsibility_focus: single
  length: short
  complexity: low
  implementation_soundness: sound
  boundary_safety: partial
  input_contract: implicit
  output_contract: clear
  side_effects: explicit
  error_handling: partial
  state_mutation: global
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
    summary: "No direct coverage for the permissions WebSocket handler."
    evidence: "No direct tests were found for _handle_permissions_config or dispatcher reload behavior; existing tests cover adjacent timeout/reload paths."
    suggested_action: "Add async tests for read-only no-reload, mutation reload, dispatcher error no-reload, and reload-exception behavior."
  - id: ISSUE-002
    dimension: observability
    severity: medium
    status: open
    summary: "Config mutation can return success when immediate reload fails."
    evidence: "The handler catches reload exceptions, logs only debug, and still sends the dispatcher success response."
    suggested_action: "Document eventual consistency, log at warning, or include response metadata when runtime permissions may be stale."
  - id: ISSUE-003
    dimension: boundary_safety
    severity: medium
    status: open
    summary: "No local authorization or channel gate protects permissions mutations."
    evidence: "Dispatch routes by req_method membership, then delegates to config mutation helpers without an AgentServer-side admin/channel check."
    suggested_action: "Document trusted-Gateway assumptions or add an explicit admin/channel/session gate."
confidence: confirmed
details: {}
---

# `AgentWebSocketServer._handle_permissions_config`

## Actual Role

Bridges AgentServer E2A `permissions.*` requests to the shared permissions config dispatcher. It reloads agent config after successful non-read operations, encodes the returned AgentResponse into E2A wire format, and sends one WebSocket response under `send_lock`.

## Key Signals

- Input: Request whose method already matched `get_permissions_config_req_methods()`.
- Output: One E2A JSON response from `dispatch_permissions_config_request`.
- Main side effects: Reads or mutates config YAML through the dispatcher; successful mutations attempt `reload_agents_config(get_config(), None)`.
- Main risk: Permissions config is sensitive, reload failure is hidden from clients, and local authorization depends on upstream trust.
- Related tests: No direct handler or dispatcher tests were found; adjacent timeout/reload tests exist.

## Detail Index

- Detail docs pending.
