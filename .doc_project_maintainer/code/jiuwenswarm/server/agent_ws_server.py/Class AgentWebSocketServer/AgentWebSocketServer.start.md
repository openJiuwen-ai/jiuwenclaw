---
symbol: AgentWebSocketServer.start
kind: method
source: jiuwenswarm/server/agent_ws_server.py
source_role: runtime_source
audit_scope: default_health_audit
class: AgentWebSocketServer
signature: "start(self) -> None"
health:
  overall: watch
  name_behavior_match: good
  responsibility_focus: mixed
  length: medium
  complexity: medium
  implementation_soundness: partial
  boundary_safety: partial
  input_contract: implicit
  output_contract: clear
  side_effects: explicit
  error_handling: partial
  state_mutation: shared
  dependency_coupling: high
  test_coverage: missing
  observability: clear
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
    dimension: performance_risk
    severity: medium
    status: open
    summary: "Best-effort sandbox bootstrap can delay post-start initialization."
    evidence: "start() awaits WebSocket serve, logs startup, then awaits _bootstrap_internal_jiuwenbox(); that callee can wait on JiuwenBoxRunner.ensure_running's default 30s startup timeout before app_agentserver continues proactive and teammate bootstrap setup."
    suggested_action: "Run jiuwenbox bootstrap in a named background task, or bound it with a shorter startup-specific timeout if server.start() should return promptly."
  - id: ISSUE-002
    dimension: test_coverage
    severity: medium
    status: open
    summary: "Missing direct lifecycle tests for start()."
    evidence: "test_app_agentserver.py mocks AgentWebSocketServer.get_instance and fake start/stop; no direct test invokes start() with monkeypatched serve/checkpointer/bootstrap paths."
    suggested_action: "Add async tests for normal start, duplicate-start no-op, legacy/fallback serve behavior, checkpointer failure propagation, and bootstrap call ordering."
confidence: confirmed
details: {}
---

# `AgentWebSocketServer.start`

## Actual Role

Starts the AgentServer WebSocket listener when no server is already running. It resets harness package state, initializes the persistent checkpointer, binds a WebSocket server through the legacy websockets API or fallback API, logs the listening address, and then runs best-effort internal jiuwenbox bootstrap.

## Key Signals

- Input: no explicit input; uses instance host, port, ping, and process config.
- Output: `_server` is set to the bound WebSocket server object or the method returns if already running.
- Main side effects: resets harness package state, initializes checkpointer state, opens a network listener, and may start/persist jiuwenbox sandbox state.
- Main risk: inline jiuwenbox bootstrap can delay the caller after the listener is already open.
- Related tests: `test_app_agentserver.py` covers the app-level fake start/stop path; direct `start()` tests are missing.

## Detail Index

- Detail docs pending.
