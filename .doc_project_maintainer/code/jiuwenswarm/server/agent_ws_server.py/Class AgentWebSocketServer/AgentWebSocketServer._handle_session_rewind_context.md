---
symbol: AgentWebSocketServer._handle_session_rewind_context
kind: method
source: jiuwenswarm/server/agent_ws_server.py
source_role: runtime_source
audit_scope: default_health_audit
class: AgentWebSocketServer
signature: "_handle_session_rewind_context(ws: Any, request: AgentRequest, send_lock: asyncio.Lock) -> None"
health:
  overall: risky
  name_behavior_match: partial
  responsibility_focus: mixed
  length: long
  complexity: medium
  implementation_soundness: partial
  boundary_safety: risky
  input_contract: implicit
  output_contract: weak
  side_effects: explicit
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
    dimension: state_mutation
    severity: high
    status: open
    summary: "Non-atomic rewind can leave history truncated after context failure."
    evidence: "The handler calls rewind_session before rewind_session_context; later exceptions become error responses after durable history mutation."
    suggested_action: "Add per-session transaction/backup rollback or make partial state explicit."
  - id: ISSUE-002
    dimension: boundary_safety
    severity: medium
    status: open
    summary: "Target session is accepted without ownership check."
    evidence: "target_sid comes from params.session_id or request.session_id; agent resolution is channel-only, then history/context mutate target_sid."
    suggested_action: "Require param and envelope session ids to match, or validate ownership before mutation."
  - id: ISSUE-003
    dimension: test_coverage
    severity: medium
    status: open
    summary: "No direct handler tests cover rewind_context."
    evidence: "Only adapter/service tests were found; visible TUI paths use session.rewind rather than session.rewind_context."
    suggested_action: "Add async tests for success, no-agent, wire shape, and partial-failure semantics."
  - id: ISSUE-004
    dimension: output_contract
    severity: low
    status: open
    summary: "Error codes are inconsistent."
    evidence: "Bad params use BAD_REQUEST; no-agent and generic exception responses omit stable codes."
    suggested_action: "Add stable codes such as AGENT_UNAVAILABLE and INTERNAL_ERROR."
confidence: confirmed
details: {}
---

# `AgentWebSocketServer._handle_session_rewind_context`

## Actual Role

Handles `session.rewind_context` by validating `session_id` and `turn_index`, resolving the live channel agent, truncating persisted session history, rebuilding and persisting context from that truncated history, and sending one encoded E2A response.

## Key Signals

- Input: Request params or envelope session id, channel id, and send lock.
- Output: E2A AgentResponse; success payload merges rewind result with `rewind_context`.
- Main side effects: Truncates history, updates metadata/diff state, clears/recreates context, and saves DeepAgent/checkpointer state.
- Main risk: Durable history is changed before context rebuild is proven durable.
- Related tests: `rewind_session_context` callee tests exist; no direct handler test was found.

## Detail Index

- Detail docs pending.
