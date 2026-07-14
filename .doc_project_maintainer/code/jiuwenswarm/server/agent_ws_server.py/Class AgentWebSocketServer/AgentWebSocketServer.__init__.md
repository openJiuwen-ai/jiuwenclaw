---
symbol: AgentWebSocketServer.__init__
kind: method
source: jiuwenswarm/server/agent_ws_server.py
source_role: runtime_source
audit_scope: default_health_audit
class: AgentWebSocketServer
signature: "__init__(self, host: str = \"127.0.0.1\", port: int = 18000, *, ping_interval: float | None = 30.0, ping_timeout: float | None = 300.0) -> None"
health:
  overall: watch
  name_behavior_match: good
  responsibility_focus: mixed
  length: medium
  complexity: low
  implementation_soundness: partial
  boundary_safety: partial
  input_contract: implicit
  output_contract: clear
  side_effects: explicit
  error_handling: missing
  state_mutation: shared
  dependency_coupling: high
  test_coverage: partial
  observability: not_applicable
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
    dimension: side_effects
    severity: medium
    status: open
    summary: "Constructor overwrites a process-global ACP output callback and captures this instance."
    evidence: "Lines 897-899 register a singleton ACP output callback that creates send_push tasks; stop() and reset_instance() do not clear that callback."
    suggested_action: "Clear or replace the ACP callback during server stop/reset, or centralize callback registration in singleton lifecycle code."
  - id: ISSUE-002
    dimension: test_coverage
    severity: low
    status: open
    summary: "Constructor behavior is only indirectly covered."
    evidence: "Harness tests call super().__init__(), and test_app_agentserver.py mocks get_instance; no focused test asserts initialized fields or callback lifecycle."
    suggested_action: "Add a focused constructor test for bind settings, mutable state, runtime manager wiring, jiuwenbox runner access, and ACP callback cleanup."
confidence: confirmed
details: {}
---

# `AgentWebSocketServer.__init__`

## Actual Role

Initializes the WebSocket server instance with bind and keepalive settings, per-connection state, stream task tracking, runtime managers, scheduler/model caches, jiuwenbox runner access, and proactive engine storage. It also registers a process-global ACP output callback that schedules `send_push` on this instance.

## Key Signals

- Input: host, port, ping interval, and ping timeout values.
- Output: initialized in-memory server object.
- Main side effects: creates runtime manager objects and replaces the singleton ACP output push callback.
- Main risk: global ACP callback can keep pointing at a stopped or test-created instance.
- Related tests: indirect construction in `test_agentserver_acp.py`, `test_agent_reload_scope.py`, and `test_app_agentserver.py`; no direct constructor lifecycle test found.

## Detail Index

- Detail docs pending.
