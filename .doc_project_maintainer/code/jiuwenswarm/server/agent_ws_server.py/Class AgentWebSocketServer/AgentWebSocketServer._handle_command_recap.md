---
symbol: AgentWebSocketServer._handle_command_recap
kind: method
source: jiuwenswarm/server/agent_ws_server.py
source_role: runtime_source
audit_scope: default_health_audit
class: AgentWebSocketServer
signature: "_handle_command_recap(ws: Any, request: AgentRequest, send_lock: asyncio.Lock) -> None"
health:
  overall: watch
  name_behavior_match: good
  responsibility_focus: single
  length: short
  complexity: low
  implementation_soundness: sound
  boundary_safety: partial
  input_contract: partial
  output_contract: partial
  side_effects: explicit
  error_handling: partial
  state_mutation: shared
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
    dimension: test_coverage
    severity: medium
    status: open
    summary: "No direct handler or adapter generate_recap status-path tests cover command.recap."
    evidence: "Tests cover recap prompts and shared model-call helpers, but not _handle_command_recap, handle_command_recap, or generate_recap status responses."
    suggested_action: "Add handler tests for success, no-turn, missing agent, exception, default session, auto_harness, and project_dir routing, plus adapter status-path tests."
  - id: ISSUE-002
    dimension: observability
    severity: low
    status: open
    summary: "Success and adapter-level failed statuses are not logged at the handler boundary."
    evidence: "_handle_command_recap logs only exceptions, while nearby _handle_command_btw logs receipt and result status."
    suggested_action: "Log recap result status after generate_recap without recording summary content."
confidence: confirmed
details: {}
---

# `AgentWebSocketServer._handle_command_recap`

## Actual Role

Handles `command.recap` by resolving session, channel, mode, sub-mode, and project directory, obtaining the scoped agent, delegating to `agent.generate_recap(session_id=...)`, and sending one response with the adapter payload. The actual recap generation and model interaction live below the adapter boundary.

## Key Signals

- Input: `session_id`, optional `params.mode`, channel id, and project directory routing.
- Output: One websocket response carrying the adapter recap payload, or `status=failed` on handler exceptions.
- Main side effects: Calls the runtime adapter and sends a websocket frame.
- Main risk: Status-path behavior is mostly delegated and not directly verified at this command boundary.
- Related tests: Prompt/model helper tests exist; direct handler and adapter status-path tests are missing.

## Detail Index

- Detail docs pending.
