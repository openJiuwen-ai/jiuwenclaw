---
symbol: AgentWebSocketServer._handle_session_list
kind: method
source: jiuwenswarm/server/agent_ws_server.py
source_role: runtime_source
audit_scope: default_health_audit
class: AgentWebSocketServer
signature: "_handle_session_list(ws: Any, request: AgentRequest, send_lock: asyncio.Lock) -> None"
health:
  overall: risky
  name_behavior_match: partial
  responsibility_focus: single
  length: short
  complexity: low
  implementation_soundness: questionable
  boundary_safety: partial
  input_contract: weak
  output_contract: weak
  side_effects: explicit
  error_handling: partial
  state_mutation: none
  dependency_coupling: medium
  test_coverage: partial
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
    dimension: implementation_soundness
    severity: medium
    status: open
    summary: "Session list handler bypasses shared helper semantics."
    evidence: "_handle_session_list manually scans directories and reads get_session_metadata(cache_bust=True), while get_all_sessions_metadata excludes heartbeat sessions, builds richer legacy fallbacks, sanitizes titles, sorts by last_message_at, and returns total."
    suggested_action: "Extend get_all_sessions_metadata with cache_bust support or share one listing helper, preserving heartbeat exclusion, fallback fields, sorting, and total semantics."
  - id: ISSUE-002
    dimension: input_contract
    severity: medium
    status: open
    summary: "Request limit/offset are ignored."
    evidence: "Adjacent Web handler parses and clamps limit/offset and returns total/limit/offset; TUI forwards session.list params to AgentServer, but _handle_session_list never reads request.params."
    suggested_action: "Honor limit/offset and include total/limit/offset in the AgentServer payload, or document that this handler intentionally returns an unpaginated list."
  - id: ISSUE-003
    dimension: error_handling
    severity: low
    status: open
    summary: "Directory scan failure is reported as ok=true."
    evidence: "The scan try/except logs a warning, then still emits an ok response with whatever sessions were collected."
    suggested_action: "Return ok=false for complete scan failure, or include a warning/partial flag when degrading."
  - id: ISSUE-004
    dimension: test_coverage
    severity: medium
    status: open
    summary: "No direct handler coverage found."
    evidence: "Search found get_all_sessions_metadata tests and gateway timeout tests, but no tests/unit_tests/agentserver match for _handle_session_list or session.list."
    suggested_action: "Add async handler tests for pagination, heartbeat exclusion, fallback metadata, scan errors, encoding, and send_lock send behavior."
confidence: confirmed
details: {}
---

# `AgentWebSocketServer._handle_session_list`

## Actual Role

Handles AgentServer `session.list` requests by scanning the session directory, reading each directory's metadata with `get_session_metadata(..., cache_bust=True)`, synthesizing minimal fallback metadata when missing, and sending one E2A-encoded ok response containing `payload.sessions`. It does not use `get_all_sessions_metadata`, request pagination params, or helper-provided `total`, `limit`, and `offset` semantics.

## Key Signals

- Input: WebSocket, `AgentRequest`, and shared `send_lock` after `ReqMethod.SESSION_LIST` routing.
- Output: `AgentResponse` with `ok=True` and `payload.sessions`, encoded by `encode_agent_response_for_wire` and JSON-sent under `send_lock`.
- Main side effects: Filesystem directory/stat reads, cache-busted metadata reads, warning logs, and WebSocket send.
- Main risk: Duplicated session listing logic diverges from shared metadata helper behavior and ignores pagination fields.
- Related tests: Helper tests cover `get_all_sessions_metadata`; no direct `AgentWebSocketServer._handle_session_list` test found.

## Detail Index

- Detail docs pending.
