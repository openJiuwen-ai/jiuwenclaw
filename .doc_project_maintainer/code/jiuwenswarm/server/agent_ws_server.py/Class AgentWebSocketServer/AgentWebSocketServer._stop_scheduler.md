---
symbol: AgentWebSocketServer._stop_scheduler
kind: method
source: jiuwenswarm/server/agent_ws_server.py
source_role: runtime_source
audit_scope: default_health_audit
class: AgentWebSocketServer
signature: "_stop_scheduler(self) -> None"
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
  state_mutation: shared
  dependency_coupling: medium
  test_coverage: missing
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
    dimension: error_handling
    severity: medium
    status: open
    summary: "Failed scheduler stop discards the service reference."
    evidence: "_stop_scheduler catches any Exception from stop_scheduler(), logs a warning, and still sets self._scheduler_service = None in the final assignment."
    suggested_action: "Clear the reference only after confirmed stop, or retain/report failed-stop state so callers can retry or inspect the failure."
  - id: ISSUE-002
    dimension: state_mutation
    severity: medium
    status: open
    summary: "Scheduler shutdown is wired through per-connection cleanup instead of server stop."
    evidence: "_connection_handler finally calls _stop_scheduler(), while AgentWebSocketServer.stop() closes the server and JiuwenBox runner without calling _stop_scheduler()."
    suggested_action: "Document and test the connection-scoped scheduler lifetime, or move scheduler cleanup into explicit server shutdown."
  - id: ISSUE-003
    dimension: test_coverage
    severity: medium
    status: open
    summary: "No direct tests cover scheduler stop success, failure, no-service, or lifecycle caller behavior."
    evidence: "No direct _stop_scheduler test was found; related shutdown tests use fake server paths or cover message handling rather than this cleanup method."
    suggested_action: "Add focused async tests with a fake scheduler service for success, raised exception, no-op, and stop()/connection cleanup interaction."
confidence: confirmed
details: {}
---

# `AgentWebSocketServer._stop_scheduler`

## Actual Role

Stops the server-held `AutoHarnessService` scheduler when `self._scheduler_service` is set. It awaits `stop_scheduler()`, logs success or failure, and then clears the server's scheduler-service reference.

## Key Signals

- Input: Implicit shared state in `self._scheduler_service`.
- Output: None.
- Main side effects: Awaits scheduler shutdown, writes logs, and mutates `self._scheduler_service`.
- Main risk: A failed stop is swallowed and the service reference is discarded, which can hide incomplete scheduler cleanup.
- Related tests: No direct `_stop_scheduler` or `AutoHarnessService.stop_scheduler` lifecycle tests were found.

## Detail Index

- Detail docs pending.
