---
symbol: AgentWebSocketServer.stop
kind: method
source: jiuwenswarm/server/agent_ws_server.py
source_role: runtime_source
audit_scope: default_health_audit
class: AgentWebSocketServer
signature: "stop(self) -> None"
health:
  overall: watch
  name_behavior_match: good
  responsibility_focus: single
  length: short
  complexity: low
  implementation_soundness: partial
  boundary_safety: partial
  input_contract: clear
  output_contract: clear
  side_effects: explicit
  error_handling: partial
  state_mutation: shared
  dependency_coupling: medium
  test_coverage: missing
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
    dimension: error_handling
    severity: medium
    status: open
    summary: "WebSocket close or wait failures can skip JiuwenBox cleanup."
    evidence: "server.close() and await server.wait_closed() run before self._server is cleared and before the best-effort _jiuwenbox_runner.stop() block."
    suggested_action: "Use finally-style cleanup so runner shutdown is attempted even when close() or wait_closed() raises."
  - id: ISSUE-002
    dimension: boundary_safety
    severity: low
    status: open
    summary: "The no-op branch skips JiuwenBox runner cleanup when _server is None."
    evidence: "stop() returns immediately if self._server is None, before checking whether the shared JiuwenBox runner needs cleanup."
    suggested_action: "Stop the runner independently where appropriate, or document that this method only cleans the runner after successful listener startup."
  - id: ISSUE-003
    dimension: test_coverage
    severity: medium
    status: open
    summary: "Missing direct lifecycle tests for stop cleanup and failure paths."
    evidence: "Existing app-level tests use a fake server start/stop path; no direct tests were found for close/wait, idempotent no-op, runner stop calls, runner stop exception logging, or server close failure cleanup."
    suggested_action: "Add focused async lifecycle tests with fake server and runner objects covering normal, no-op, runner failure, and server close failure cases."
confidence: confirmed
details: {}
---

# `AgentWebSocketServer.stop`

## Actual Role

Stops the bound WebSocket server when `self._server` is present, waits for listener shutdown, clears the server reference, then best-effort stops the shared JiuwenBox runner and logs shutdown. If no server is present, it returns without additional cleanup.

## Key Signals

- Input: Implicit runtime state in `self._server` and `self._jiuwenbox_runner`.
- Output: None.
- Main side effects: Closes the WebSocket listener, mutates `self._server`, may stop the JiuwenBox subprocess runner, and writes logs.
- Main risk: Cleanup is gated by `_server is None`, and WebSocket close failures can prevent JiuwenBox cleanup from running.
- Related tests: No direct `AgentWebSocketServer.stop` lifecycle tests were found; current app-level tests use fake or mocked server instances.

## Detail Index

- Detail docs pending.
