---
symbol: AgentWebSocketServer._handle_team_history_get
kind: method
source: jiuwenswarm/server/agent_ws_server.py
source_role: runtime_source
audit_scope: default_health_audit
class: AgentWebSocketServer
signature: "_handle_team_history_get(ws: Any, request: AgentRequest, send_lock: asyncio.Lock) -> None"
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
  state_mutation: none
  dependency_coupling: medium
  test_coverage: partial
  observability: partial
  performance_risk: high
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
    summary: "Pagination happens after full history load and full-record sanitization."
    evidence: "The handler reads all team history records, sanitizes every dict record, then coerces cursor/limit and selects a bounded page."
    suggested_action: "Move pagination closer to storage or slice by cursor before expensive sanitization while preserving total and has_more semantics."
  - id: ISSUE-002
    dimension: error_handling
    severity: medium
    status: open
    summary: "Read failures are indistinguishable from empty history to clients."
    evidence: "Exceptions from read_team_history_records are logged and converted to records=[]; the final response is ok=true with total=0."
    suggested_action: "Return ok=false or include an explicit non-fatal warning/error field for storage failures, with direct tests for that path."
confidence: confirmed
details: {}
---

# `AgentWebSocketServer._handle_team_history_get`

## Actual Role

Handles `team.history.get` by requiring a session id, reading team-relevant records for that session, sanitizing them for wire safety, coercing cursor/limit/byte-budget params, selecting a bounded page, and sending one response. Storage read exceptions are logged but returned as a successful empty history page.

## Key Signals

- Input: `params.session_id`, optional `cursor`/`offset`, `limit`, and `max_bytes`.
- Output: One AgentResponse with `records`, `session_id`, `cursor`, `next_cursor`, `has_more`, and `total`, or `ok: false` when session id is missing.
- Main side effects: Reads history in a thread, logs warning/debug lines, and sends a websocket frame.
- Main risk: Large histories pay full read and sanitization cost before pagination.
- Related tests: History payload limit tests cover large-record paging, cursor continuation, and placeholder behavior.

## Detail Index

- Detail docs pending.
