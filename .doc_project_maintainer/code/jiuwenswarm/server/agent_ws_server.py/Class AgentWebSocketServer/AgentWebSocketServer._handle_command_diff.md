---
symbol: AgentWebSocketServer._handle_command_diff
kind: method
source: jiuwenswarm/server/agent_ws_server.py
source_role: runtime_source
audit_scope: default_health_audit
class: AgentWebSocketServer
signature: "_handle_command_diff(ws: Any, request: AgentRequest, send_lock: asyncio.Lock) -> None"
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
  side_effects: explicit
  error_handling: partial
  state_mutation: none
  dependency_coupling: medium
  test_coverage: partial
  observability: clear
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
    severity: medium
    status: open
    summary: "Resolved project_dir is trusted before filesystem and git-diff reads."
    evidence: "resolve_request_project_dir accepts params/metadata project_dir/cwd/trusted_dirs; the handler passes it directly to DiffService, which reads .agent_history and runs git diff."
    suggested_action: "Validate canonical project_dir against trusted/session-bound directories, or derive it from server-side session metadata when available."
  - id: ISSUE-002
    dimension: test_coverage
    severity: medium
    status: open
    summary: "Direct coverage exercises only the empty default success path."
    evidence: "The handler test asserts {type:list, turns:[]} only; no direct test covers gitDiff inclusion, explicit project_dir/cwd routing, DiffService exceptions, or gateway/frontend command.diff flow."
    suggested_action: "Add handler tests with a fake DiffService for turns, gitDiff, and error paths, plus a gateway/TUI forwarding smoke test."
confidence: confirmed
details: {}
---

# `AgentWebSocketServer._handle_command_diff`

## Actual Role

Handles `command.diff` by resolving the request session and project directory, asking `DiffService` for turn diffs and the current git diff in parallel worker threads, and sending one response containing `type=list`, `turns`, and optional `gitDiff`.

## Key Signals

- Input: `request_id`, `channel_id`, optional `session_id`, and project metadata from params/metadata.
- Output: One websocket response with diff list payload, or `ok=false` with an error string.
- Main side effects: Reads history and git state through `DiffService`, logs response/failure, and sends a websocket frame.
- Main risk: Client-provided project directory metadata can drive filesystem and git reads.
- Related tests: One direct empty-success handler test exists; `DiffService` has separate git-diff tests.

## Detail Index

- Detail docs pending.
