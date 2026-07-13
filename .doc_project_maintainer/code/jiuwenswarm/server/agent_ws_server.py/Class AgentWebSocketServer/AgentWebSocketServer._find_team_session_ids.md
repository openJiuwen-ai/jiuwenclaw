---
symbol: AgentWebSocketServer._find_team_session_ids
kind: method
source: jiuwenswarm/server/agent_ws_server.py
source_role: runtime_source
audit_scope: default_health_audit
class: AgentWebSocketServer
signature: "_find_team_session_ids(team_name: str) -> list[str]"
health:
  overall: watch
  name_behavior_match: good
  responsibility_focus: single
  length: short
  complexity: low
  implementation_soundness: partial
  boundary_safety: partial
  input_contract: implicit
  output_contract: clear
  side_effects: none
  error_handling: partial
  state_mutation: none
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
    severity: medium
    status: open
    summary: "Team-delete selection may use stale cached metadata."
    evidence: "_find_team_session_ids calls get_session_metadata(session_id) without cache_bust=True, while nearby session.list explicitly cache-busts for cross-process freshness; this helper feeds destructive team.delete cleanup."
    suggested_action: "Use cache_bust=True here or document why team metadata for deletion is guaranteed to be AgentServer-local and fresh."
  - id: ISSUE-002
    dimension: error_handling
    severity: low
    status: open
    summary: "Filesystem scan errors are not locally normalized."
    evidence: "The helper handles a missing sessions root but does not catch iterdir/is_dir failures; dispatcher-level exception handling would turn this into a generic request failure."
    suggested_action: "Wrap the scan with targeted logging and return or raise a deliberate team-delete error shape."
  - id: ISSUE-003
    dimension: test_coverage
    severity: low
    status: open
    summary: "Boundary and freshness cases are not directly covered."
    evidence: "Direct test covers mode/team_name filtering only; team-delete tests override the helper."
    suggested_action: "Add direct tests for missing/non-directory roots, stale cache versus disk metadata, malformed metadata, and deterministic sorting."
confidence: confirmed
details: {}
---

# `AgentWebSocketServer._find_team_session_ids`

## Actual Role

Enumerates the AgentServer sessions directory and returns a deterministic list of session IDs whose metadata says `mode: team` and whose trimmed `team_name` exactly matches the caller-provided team name. It is a read-only selector used by `team.delete` before runtime shutdown, OpenJiuwen team deletion, session directory removal, and metadata-cache clearing.

## Key Signals

- Input: Pre-trimmed team name string from `_handle_team_delete`.
- Output: Sorted unique `list[str]` of matching local session directory names; empty list when the sessions root is absent.
- Main side effects: No writes; performs synchronous filesystem iteration and metadata reads.
- Main risk: Cached metadata and unhandled scan errors can affect destructive team-delete selection.
- Related tests: Direct happy-path filter test exists; team-delete tests stub this helper.

## Detail Index

- Detail docs pending.
