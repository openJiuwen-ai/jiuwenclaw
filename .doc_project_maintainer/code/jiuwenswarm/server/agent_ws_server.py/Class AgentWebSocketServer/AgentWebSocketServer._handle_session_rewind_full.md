---
symbol: AgentWebSocketServer._handle_session_rewind_full
kind: method
source: jiuwenswarm/server/agent_ws_server.py
source_role: runtime_source
audit_scope: default_health_audit
class: AgentWebSocketServer
signature: "_handle_session_rewind_full(ws: Any, request: AgentRequest, send_lock: asyncio.Lock, restore_files: bool = False, compact: bool = False) -> None"
health:
  overall: risky
  name_behavior_match: partial
  responsibility_focus: overloaded
  length: excessive
  complexity: high
  implementation_soundness: questionable
  boundary_safety: risky
  input_contract: weak
  output_contract: weak
  side_effects: implicit
  error_handling: partial
  state_mutation: shared
  dependency_coupling: high
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
    severity: high
    status: open
    summary: "Compact-from rebuilds context before summary records."
    evidence: "The handler calls rewind_session_context before appending compact_boundary, rewind_summary, and compact_summary."
    suggested_action: "Append compact records before context rebuild, or rebuild after compact_partial_session."
  - id: ISSUE-002
    dimension: output_contract
    severity: high
    status: open
    summary: "Context failure can still return success."
    evidence: "Missing active agent leaves context_ok false; context exceptions are swallowed; response remains ok=True."
    suggested_action: "Reject before mutation, or return explicit partial/error status."
  - id: ISSUE-003
    dimension: boundary_safety
    severity: medium
    status: open
    summary: "restore_files mutates files before rewind completion."
    evidence: "restore_session_files runs before history truncation and context rebuild, and writes or unlinks files directly."
    suggested_action: "Prevalidate rewind/context first, then restore files, or add rollback."
  - id: ISSUE-004
    dimension: input_contract
    severity: medium
    status: open
    summary: "Compact-only inputs are weakly validated."
    evidence: "direction is unconstrained, and summarized_count is cast before handler-local try/except."
    suggested_action: "Validate compact fields inside the BAD_REQUEST path before side effects."
  - id: ISSUE-005
    dimension: test_coverage
    severity: medium
    status: open
    summary: "No direct tests cover the three rewind modes."
    evidence: "Service tests cover helpers, but no _handle_session_rewind_full or SESSION_REWIND* handler tests were found."
    suggested_action: "Add handler tests for plain, restore, compact, no-agent, failure, and bad direction."
confidence: confirmed
details: {}
---

# `AgentWebSocketServer._handle_session_rewind_full`

## Actual Role

Handles AgentServer `session.rewind`, `session.rewind_and_restore`, and `session.rewind_compact`. It validates session/turn input, optionally restores files, truncates history, rebuilds active context/checkpointer state, appends compact records, and sends one E2A response.

## Key Signals

- Input: `params.session_id`, `turn_index`, optional compact fields.
- Output: One encoded AgentResponse, usually `ok: true` with rewind/restore/context payload.
- Main side effects: Rewrites history, updates metadata/diff state, may write/delete files, rebuilds context/checkpointer state, and appends compact records.
- Main risk: Partial success can leave history, files, context, and checkpointer out of sync.
- Related tests: Service-level compact/context tests exist; direct handler tests were not found.

## Detail Index

- Detail docs pending.
