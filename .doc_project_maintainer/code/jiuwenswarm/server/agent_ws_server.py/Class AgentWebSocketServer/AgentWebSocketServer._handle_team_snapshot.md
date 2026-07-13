---
symbol: AgentWebSocketServer._handle_team_snapshot
kind: method
source: jiuwenswarm/server/agent_ws_server.py
source_role: runtime_source
audit_scope: default_health_audit
class: AgentWebSocketServer
signature: "_handle_team_snapshot(ws: Any, request: AgentRequest, send_lock: asyncio.Lock) -> None"
health:
  overall: watch
  name_behavior_match: good
  responsibility_focus: single
  length: short
  complexity: low
  implementation_soundness: partial
  boundary_safety: partial
  input_contract: implicit
  output_contract: partial
  side_effects: hidden
  error_handling: partial
  state_mutation: global
  dependency_coupling: medium
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
    dimension: test_coverage
    severity: medium
    status: open
    summary: "No direct handler or dispatch-route coverage for team.snapshot."
    evidence: "Existing tests cover TeamMonitorHandler.get_team_snapshot and a broadcast helper, but not _handle_team_snapshot or TEAM_SNAPSHOT dispatch."
    suggested_action: "Add async websocket-handler tests for active snapshot, missing/stopped monitor fallback, callee failure fallback, and dispatcher routing."
  - id: ISSUE-002
    dimension: output_contract
    severity: low
    status: open
    summary: "Different states collapse into the same successful empty response."
    evidence: "Missing monitor, stopped monitor, None snapshot, and snapshot exception all return ok=true with empty members/tasks/team_id."
    suggested_action: "Add snapshot_status metadata if clients need diagnostics, or document the refresh-tolerant empty response contract."
  - id: ISSUE-003
    dimension: state_mutation
    severity: low
    status: open
    summary: "Read-style snapshot requests can create a channel-scoped TeamManager."
    evidence: "_handle_team_snapshot calls get_team_manager(channel_id), whose registry helper creates and stores a manager when absent."
    suggested_action: "Use a non-creating lookup for read-only snapshots, or document registry creation as acceptable."
confidence: confirmed
details: {}
---

# `AgentWebSocketServer._handle_team_snapshot`

## Actual Role

Handles `team.snapshot` by resolving channel/session, looking up the active team monitor handler, and sending one encoded response. Missing, stopped, failing, or `None` snapshot paths are intentionally tolerant and produce `ok: true` with an empty `{members, tasks, team_id}` payload.

## Key Signals

- Input: Request id, optional `session_id`, optional `channel_id`; no request payload schema.
- Output: One E2A AgentResponse frame with a snapshot-shaped payload.
- Main side effects: Sends on websocket, may create a TeamManager registry entry, and logs snapshot exceptions.
- Main risk: Clients cannot distinguish no team, stopped monitor, empty team, and snapshot failure.
- Related tests: Monitor snapshot tests exist; direct handler and dispatch tests were not found.

## Detail Index

- Detail docs pending.
