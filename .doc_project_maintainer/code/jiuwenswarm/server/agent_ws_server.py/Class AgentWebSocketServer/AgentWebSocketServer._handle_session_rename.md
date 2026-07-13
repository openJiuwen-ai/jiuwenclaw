---
symbol: AgentWebSocketServer._handle_session_rename
kind: method
source: jiuwenswarm/server/agent_ws_server.py
source_role: runtime_source
audit_scope: default_health_audit
class: AgentWebSocketServer
signature: "_handle_session_rename(ws: Any, request: AgentRequest, send_lock: asyncio.Lock) -> None"
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
  state_mutation: shared
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
    summary: "Missing direct rename-handler and helper tests."
    evidence: "Search found no tests for session.rename, apply_session_rename, or _handle_session_rename; only lower-level session_metadata tests cover adjacent persistence semantics."
    suggested_action: "Add focused tests for query, set, clear, missing session_id/BAD_REQUEST, non-dict params, and WebSocket response encoding for _handle_session_rename."
confidence: confirmed
details: {}
---

# `AgentWebSocketServer._handle_session_rename`

## Actual Role

Handles a unary `session.rename` WebSocket request by delegating query, clear, and set semantics to `apply_session_rename`. It wraps the helper result in an `AgentResponse`, encodes it to E2A wire form, and sends exactly one JSON response under `send_lock`.

## Key Signals

- Input: `AgentRequest.params` may contain `session_id` and/or `title`; `request.session_id` is the fallback session id; blank `channel_id` becomes helper `init_channel_id="tui"`.
- Output: Success payload contains `session_id`, `title`, and `previous_title`; failure payload contains `error` and `code`.
- Main side effects: For title-present requests, may initialize or update session metadata; query-only requests avoid creating missing session directories.
- Main risk: No direct handler/helper tests pin the adapter and shared rename semantics, and metadata persistence details are delegated.
- Related tests: No direct `session.rename`, `apply_session_rename`, or `_handle_session_rename` tests found; adjacent metadata tests cover init/update/get behavior.

## Detail Index

- Detail docs pending.
