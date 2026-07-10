---
symbol: AgentWebSocketServer._handle_command_compact
kind: method
source: jiuwenswarm/server/agent_ws_server.py
source_role: runtime_source
audit_scope: default_health_audit
class: AgentWebSocketServer
signature: "_handle_command_compact(ws: Any, request: AgentRequest, send_lock: asyncio.Lock) -> None"
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
  side_effects: implicit
  error_handling: partial
  state_mutation: shared
  dependency_coupling: high
  test_coverage: partial
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
    dimension: output_contract
    severity: medium
    status: open
    summary: "Emits an apparently unconsumed context.compressed push."
    evidence: "The handler sends event_type=context.compressed, while inspected TUI/Web consumers handle context.usage and context.compression_state instead."
    suggested_action: "Align event naming with consumers, or remove the redundant push and rely on context.compression_state/RPC stats with tests."
  - id: ISSUE-002
    dimension: boundary_safety
    severity: medium
    status: open
    summary: "Summary display depends on best-effort push delivery."
    evidence: "The RPC includes compact_summary, but the TUI suppresses local stats/summary when compact_summary exists because it expects a push; send_push can log and drop when no socket or send fails."
    suggested_action: "Add a TUI RPC fallback or make push delivery report failure so the handler can include fallback display data."
  - id: ISSUE-003
    dimension: test_coverage
    severity: medium
    status: open
    summary: "Important side effects and failure branches are under-tested."
    evidence: "Direct tests cover one compressed response and one compression-state push, but not busy/noop, missing agent, adapter exception, project/mode forwarding, push failure, or history append/no-summary behavior."
    suggested_action: "Add focused handler tests for the missing result modes, error branches, project routing, push failure, and compact-history persistence."
confidence: confirmed
details: {}
---

# `AgentWebSocketServer._handle_command_compact`

## Actual Role

Handles `command.compact` by resolving session, channel, mode, sub-mode, and project directory, obtaining the scoped agent, delegating to `agent.compress_context(..., return_state=True)`, and sending a compact result response. When compression succeeds with stats, it may send context-compression pushes and append compact history if a non-empty summary exists.

## Key Signals

- Input: `session_id`, optional `params.mode`, channel id, and project directory routing from the request.
- Output: One websocket response with result, stats, and optional summary fields, or an error payload.
- Main side effects: Calls the runtime adapter, may send push events, and may append compact-history records.
- Main risk: UI-visible summary/state depends on loosely coupled push behavior and event naming.
- Related tests: Some direct compact handler tests exist, but multiple routing, failure, and persistence paths are missing.

## Detail Index

- Detail docs pending.
