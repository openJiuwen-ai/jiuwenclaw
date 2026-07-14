---
symbol: AgentWebSocketServer._handle_history_get
kind: method
source: jiuwenswarm/server/agent_ws_server.py
source_role: runtime_source
audit_scope: default_health_audit
class: AgentWebSocketServer
signature: "_handle_history_get(ws: Any, request: AgentRequest, send_lock: asyncio.Lock) -> None"
health:
  overall: risky
  name_behavior_match: good
  responsibility_focus: single
  length: short
  complexity: low
  implementation_soundness: partial
  boundary_safety: risky
  input_contract: weak
  output_contract: clear
  side_effects: hidden
  error_handling: partial
  state_mutation: global
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
    dimension: boundary_safety
    severity: high
    status: open
    summary: "Unvalidated session_id reaches history path helpers."
    evidence: "_handle_history_get passes params.session_id directly to get_conversation_history; session_history helpers build get_agent_sessions_dir() / session_id before checks."
    suggested_action: "Validate session_id as a safe single session name, make read helpers non-creating, verify containment, and add traversal tests."
  - id: ISSUE-002
    dimension: performance_risk
    severity: medium
    status: open
    summary: "Each page load scans the whole history."
    evidence: "get_conversation_history loads all records, filters all restorable records, reverses the full list, then slices one page."
    suggested_action: "Consider reverse JSONL/cursor paging or cached pagination metadata for large sessions."
  - id: ISSUE-003
    dimension: test_coverage
    severity: medium
    status: open
    summary: "No direct unary history handler tests were found."
    evidence: "Existing tests cover helper sanitization and gateway/E2A routing, but not _handle_history_get success/error/send_lock/wire behavior."
    suggested_action: "Add async handler tests with fake ws, patched history helper, invalid params, and path-safety cases."
confidence: confirmed
details: {}
---

# `AgentWebSocketServer._handle_history_get`

## Actual Role

Handles non-stream `history.get` by extracting `params.session_id` and `params.page_idx`, delegating validation, filtering, pagination, and sanitization to `get_conversation_history`, wrapping the result in an E2A AgentResponse, and sending one JSON frame.

## Key Signals

- Input: WebSocket, `AgentRequest.params.session_id`, `page_idx`, and send lock.
- Output: `ok=true` payload `{messages, total_pages, page_idx}`, or `ok=false` error for invalid page/session.
- Main side effects: Reads session history and sends a WebSocket frame; delegated helpers touch filesystem paths derived from caller input.
- Main risk: Caller-controlled session id is not validated before history path helpers receive it.
- Related tests: Indirect history sanitization, gateway registration, and E2A normalization tests exist; direct handler tests were not found.

## Detail Index

- Detail docs pending.
