---
symbol: AgentWebSocketServer._handle_history_get_stream
kind: method
source: jiuwenswarm/server/agent_ws_server.py
source_role: runtime_source
audit_scope: default_health_audit
class: AgentWebSocketServer
signature: "_handle_history_get_stream(ws: Any, request: AgentRequest, send_lock: asyncio.Lock) -> None"
health:
  overall: risky
  name_behavior_match: good
  responsibility_focus: single
  length: short
  complexity: low
  implementation_soundness: partial
  boundary_safety: risky
  input_contract: weak
  output_contract: partial
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
    summary: "Unvalidated session_id flows into session history path construction."
    evidence: "The handler passes params directly to get_conversation_history; session_history helpers build get_agent_sessions_dir()/session_id and may create dirs by default."
    suggested_action: "Validate session_id as a safe single session name, prevent read-path mkdirs, enforce resolved-path containment, and add traversal tests."
  - id: ISSUE-002
    dimension: error_handling
    severity: medium
    status: open
    summary: "Invalid stream history emits chat.error instead of a history-scoped terminal frame."
    evidence: "The error path sends event_type=chat.error, while TUI page restore waits for history.message status=done."
    suggested_action: "Emit a history-scoped error/done frame or make the frontend resolve pending history page waiters on history errors."
  - id: ISSUE-003
    dimension: performance_risk
    severity: medium
    status: open
    summary: "Each page request loads, filters, reverses, and slices the full history."
    evidence: "get_conversation_history loads all records, builds all restorable records, reverses the list, then slices one page."
    suggested_action: "Consider reverse JSONL/cursor paging or cached pagination metadata for large sessions."
  - id: ISSUE-004
    dimension: test_coverage
    severity: medium
    status: open
    summary: "No direct stream-handler test evidence was found."
    evidence: "Existing tests cover sanitization and generic history.message wire roundtrip, not success/error/done sequencing for this handler."
    suggested_action: "Add async tests with fake websocket and send lock for valid page, invalid page, sequence numbers, and final done-frame publication."
confidence: confirmed
details: {}
---

# `AgentWebSocketServer._handle_history_get_stream`

## Actual Role

Streams paged session history for `history.get` requests with `is_stream=True`. It delegates filtering, reverse paging, and sanitization to `get_conversation_history`, then sends one `history.message` chunk per record plus a final `history.message` done chunk; invalid page/history sends a terminal `chat.error` chunk.

## Key Signals

- Input: `params.session_id` and `params.page_idx`.
- Output: E2A chunks for each history record plus a final done frame, or a final `chat.error`.
- Main side effects: Reads session history and sends websocket stream frames.
- Main risk: Caller-controlled session id reaches filesystem history helpers without path-safety validation.
- Related tests: Helper and wire tests exist; no direct stream-handler test was found.

## Detail Index

- Detail docs pending.
