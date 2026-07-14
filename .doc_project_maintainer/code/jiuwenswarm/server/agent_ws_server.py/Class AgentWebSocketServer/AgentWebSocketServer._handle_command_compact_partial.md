---
symbol: AgentWebSocketServer._handle_command_compact_partial
kind: method
source: jiuwenswarm/server/agent_ws_server.py
source_role: runtime_source
audit_scope: default_health_audit
class: AgentWebSocketServer
signature: "_handle_command_compact_partial(ws: Any, request: AgentRequest, send_lock: asyncio.Lock) -> None"
health:
  overall: risky
  name_behavior_match: partial
  responsibility_focus: single
  length: medium
  complexity: low
  implementation_soundness: questionable
  boundary_safety: risky
  input_contract: weak
  output_contract: implicit
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
    dimension: boundary_safety
    severity: medium
    status: open
    summary: "turn_index is not validated as a positive 1-based value."
    evidence: "The handler defaults missing turn_index to 0 and passes it through; the adapter later indexes user_positions[turn_index - 1], so 0 selects the last user turn."
    suggested_action: "Require a present integer turn_index >= 1 before delegating to the adapter."
  - id: ISSUE-002
    dimension: implementation_soundness
    severity: high
    status: open
    summary: "The delegated adapter reads only legacy history.json while current session history prefers history.jsonl."
    evidence: "compact_partial builds sessions_dir/session_id/history.json directly, while session_history defaults read/write behavior to JSONL unless legacy mode is used."
    suggested_action: "Use the shared session history resolver/load helpers and add JSONL integration coverage."
  - id: ISSUE-003
    dimension: test_coverage
    severity: medium
    status: open
    summary: "No direct handler tests cover compact_partial routing, validation, response, and error behavior."
    evidence: "Existing compact_partial tests focus on service behavior rather than _handle_command_compact_partial or E2A routing."
    suggested_action: "Add handler tests for success, missing/invalid turn_index, adapter failures, and mode/project forwarding."
confidence: confirmed
details: {}
---

# `AgentWebSocketServer._handle_command_compact_partial`

## Actual Role

Handles `command.compact_partial` by parsing `session_id`, `turn_index`, `direction`, mode, sub-mode, channel, and project directory, then delegating to `agent.compact_partial`. It sends the adapter payload as a single response and preserves cancellation/keyboard interrupts while normalizing other failures to `status=failed`.

## Key Signals

- Input: `params.turn_index`, `params.direction`, optional `params.mode`, session id, channel id, and request project directory.
- Output: One websocket response containing the adapter result, or a failed status payload.
- Main side effects: Calls the scoped runtime adapter and sends a websocket frame.
- Main risk: Invalid turn indexes and legacy-history reads can target the wrong turn or miss current JSONL session history.
- Related tests: Service-level compact_partial tests exist; direct handler coverage is missing.

## Detail Index

- Detail docs pending.
