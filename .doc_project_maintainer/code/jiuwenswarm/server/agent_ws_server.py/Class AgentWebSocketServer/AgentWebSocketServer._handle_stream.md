---
symbol: AgentWebSocketServer._handle_stream
kind: method
source: jiuwenswarm/server/agent_ws_server.py
source_role: runtime_source
audit_scope: default_health_audit
class: AgentWebSocketServer
signature: "_handle_stream(self, ws: Any, request: AgentRequest, send_lock: asyncio.Lock) -> None"
health:
  overall: risky
  name_behavior_match: good
  responsibility_focus: mixed
  length: long
  complexity: high
  implementation_soundness: questionable
  boundary_safety: risky
  input_contract: implicit
  output_contract: clear
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
    severity: medium
    status: open
    summary: "Stream task registration can leak stale session entries before cleanup starts."
    evidence: "The method registers _session_stream_tasks before agent resolution and mode sync, but the cleanup finally is only entered after heartbeat setup and stream iteration begin."
    suggested_action: "Wrap registration, agent resolution, heartbeat setup, streaming, and cleanup in one outer try/finally."
  - id: ISSUE-002
    dimension: performance_risk
    severity: medium
    status: open
    summary: "Heartbeat loop creates wait tasks without canceling pending waiters."
    evidence: "Each heartbeat iteration creates heartbeat_event.wait() and stream_stop_event.wait() tasks via asyncio.ensure_future; pending tasks from asyncio.wait are ignored before the next loop."
    suggested_action: "Cancel and await pending wait tasks, or restructure the loop around a reusable timeout/wait path."
confidence: confirmed
details: {}
---

# `AgentWebSocketServer._handle_stream`

## Actual Role

Runs a streaming AgentServer request by registering the current task in `_session_stream_tasks`, resolving the agent/mode path, optionally restoring code-mode state, iterating agent stream chunks, sending E2A wire chunks, and running a heartbeat loop while long agent work is active.

## Key Signals

- Input: `AgentRequest` with stream flag and session context.
- Output: multiple E2A chunk frames plus completion/error frames.
- Main side effects: `_session_stream_tasks` mutation, heartbeat task, agent stream execution.
- Main risk: stream registration and heartbeat cleanup span agent resolution, cancellation, WebSocket, and Gateway timeout boundaries.
- Related tests: `test_agentserver_modes.py` covers selected stream mode paths; server-side heartbeat and cleanup tests are missing.

## Detail Index

- Detail docs pending.
