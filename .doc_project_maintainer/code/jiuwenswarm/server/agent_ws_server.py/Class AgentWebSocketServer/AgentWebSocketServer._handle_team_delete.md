---
symbol: AgentWebSocketServer._handle_team_delete
kind: method
source: jiuwenswarm/server/agent_ws_server.py
source_role: runtime_source
audit_scope: default_health_audit
class: AgentWebSocketServer
signature: "_handle_team_delete(ws: Any, request: AgentRequest, send_lock: asyncio.Lock) -> None"
health:
  overall: risky
  name_behavior_match: good
  responsibility_focus: mixed
  length: long
  complexity: medium
  implementation_soundness: partial
  boundary_safety: partial
  input_contract: clear
  output_contract: implicit
  side_effects: explicit
  error_handling: partial
  state_mutation: global
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
    severity: medium
    status: open
    summary: "Runner.delete_agent_team false result is ignored."
    evidence: "_handle_team_delete awaits Runner.delete_agent_team without checking its boolean result; adjacent OpenJiuwen manager code returns a deleted flag."
    suggested_action: "Check the returned value and return a stable failure response before deleting local session directories when persistent team deletion fails."
  - id: ISSUE-002
    dimension: error_handling
    severity: medium
    status: open
    summary: "Local session directory deletion failure is masked as successful team deletion."
    evidence: "The handler logs shutil.rmtree failures and continues, then returns deleted=true for the full session_ids list."
    suggested_action: "Track failed local removals and return a partial/failure response, or retry/rollback according to a documented cleanup contract."
  - id: ISSUE-003
    dimension: test_coverage
    severity: low
    status: open
    summary: "Missing direct tests for partial and lower-level failure paths."
    evidence: "Direct tests cover success, checkpointer failure, non-team mode, and missing team_name; no NOT_FOUND, Runner false/exception, or rmtree failure coverage found."
    suggested_action: "Add tests for no matching sessions, Runner false/exception, and rmtree failure so destructive partial-cleanup behavior is pinned."
confidence: confirmed
details: {}
---

# `AgentWebSocketServer._handle_team_delete`

## Actual Role

Handles AgentServer `team.delete` requests by validating team-mode params, requiring a persistent checkpointer, finding all local team sessions whose metadata matches `team_name`, stopping active team runtimes, delegating persistent team/session cleanup to OpenJiuwen `Runner.delete_agent_team`, deleting local session directories, clearing metadata cache, and sending one E2A-encoded `AgentResponse`.

## Key Signals

- Input: `AgentRequest.params` must provide `team_name` and team-mode indicators.
- Output: Validation/checkpointer/not-found failures return coded error payloads; success returns `team_name`, `session_ids`, and `deleted: true`.
- Main side effects: Stops team runtimes, deletes OpenJiuwen team/session state, removes session directories, and clears metadata cache.
- Main risk: Destructive cleanup spans runtime, checkpoint/database, filesystem, and metadata cache without atomicity, and some partial failures are reported as success.
- Related tests: Direct team-delete tests cover success, missing team_name, non-team mode, and checkpointer failure; partial cleanup and lower-level failure paths are missing.

## Detail Index

- Detail docs pending.
