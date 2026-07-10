---
symbol: AgentWebSocketServer._handle_session_delete
kind: method
source: jiuwenswarm/server/agent_ws_server.py
source_role: runtime_source
audit_scope: default_health_audit
class: AgentWebSocketServer
signature: "_handle_session_delete(ws: Any, request: AgentRequest, send_lock: asyncio.Lock) -> None"
health:
  overall: risky
  name_behavior_match: good
  responsibility_focus: mixed
  length: long
  complexity: medium
  implementation_soundness: partial
  boundary_safety: risky
  input_contract: weak
  output_contract: clear
  side_effects: explicit
  error_handling: partial
  state_mutation: global
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
    dimension: boundary_safety
    severity: high
    status: open
    summary: "Unvalidated session_id is recursively deleted."
    evidence: "target is only stripped before get_agent_sessions_dir() / target reaches shutil.rmtree; no absolute path, separator, '..', reparse-link, or containment guard was found."
    suggested_action: "Validate session IDs as single safe names, verify the delete target stays under the sessions root, and add traversal/absolute-path tests."
  - id: ISSUE-002
    dimension: state_mutation
    severity: medium
    status: open
    summary: "Successful delete does not clear all session-scoped in-process state."
    evidence: "The handler clears _plan_exited_sessions and metadata cache only; _session_mode_sync_locks has no observed pop, and _session_stream_tasks are only popped by stream finalization."
    suggested_action: "On successful deletion, remove safe session-scoped locks and define/cancel active stream-task behavior."
  - id: ISSUE-003
    dimension: error_handling
    severity: medium
    status: open
    summary: "Filesystem deletion failure lacks a delete-specific response."
    evidence: "Runtime cleanup maps to DELETE_FAILED, but shutil.rmtree(session_dir) is outside that try; failures fall to the generic dispatcher after runtime cleanup may have occurred."
    suggested_action: "Catch rmtree errors locally, return a stable error code, and clear metadata/global state only after confirmed filesystem deletion."
  - id: ISSUE-004
    dimension: test_coverage
    severity: medium
    status: open
    summary: "Handler-level team-session delete and destructive-path edge cases are not directly covered."
    evidence: "Direct handler tests cover agent-mode success and checkpointer rejection; team deletion is covered mostly through _handle_team_delete and TeamManager.delete_session_runtime."
    suggested_action: "Add direct tests for team-mode success/failure, missing and non-directory sessions, path containment, runtime cleanup failure, and rmtree failure."
confidence: confirmed
details: {}
---

# `AgentWebSocketServer._handle_session_delete`

## Actual Role

Handles AgentServer `session.delete` for one session: validates `params.session_id`, checks the session directory, requires persistent checkpointer availability, releases runtime state, deletes the directory, clears selected in-process/cache state, and sends one E2A response.

## Key Signals

- Input: `AgentRequest.params.session_id`; metadata `mode == "team"` chooses team-runtime deletion.
- Output: Coded validation/checkpointer/runtime cleanup errors, or success payload `{session_id: target}`.
- Main side effects: Deletes runtime/checkpointer state, removes a local session directory, discards `_plan_exited_sessions`, and clears metadata cache.
- Main risk: A caller-controlled session id reaches recursive deletion without containment validation, and some cleanup failures happen after runtime release.
- Related tests: Direct tests cover non-team success and checkpointer failure; adjacent tests cover TeamManager delete-session runtime and team.delete.

## Detail Index

- Detail docs pending.
