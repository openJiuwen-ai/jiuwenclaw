---
symbol: AgentWebSocketServer.reset_instance
kind: method
source: jiuwenswarm/server/agent_ws_server.py
source_role: runtime_source
audit_scope: default_health_audit
class: AgentWebSocketServer
signature: "reset_instance(cls) -> None"
health:
  overall: watch
  name_behavior_match: partial
  responsibility_focus: single
  length: short
  complexity: low
  implementation_soundness: partial
  boundary_safety: partial
  input_contract: clear
  output_contract: clear
  side_effects: explicit
  error_handling: clear
  state_mutation: global
  dependency_coupling: medium
  test_coverage: missing
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
    dimension: boundary_safety
    severity: medium
    status: open
    summary: "Reset clears only the singleton reference, not the live server lifecycle."
    evidence: "reset_instance assigns cls._instance = None, while stop() owns server and JiuwenBox cleanup and get_instance() can create a replacement afterward."
    suggested_action: "Keep this helper test-only or require await server.stop() before reset; consider a lifecycle-safe reset helper if production reset is needed."
  - id: ISSUE-002
    dimension: state_mutation
    severity: low
    status: open
    summary: "Reset does not clear the constructor-registered ACP push callback."
    evidence: "The constructor registers an AcpOutputManager callback that closes over self, but reset_instance only drops the class-level _instance reference."
    suggested_action: "Tests touching ACP output should clear or replace the callback during fixture cleanup, or lifecycle reset should own that callback state."
  - id: ISSUE-003
    dimension: test_coverage
    severity: low
    status: open
    summary: "No focused singleton reset test covers the helper's limited cleanup contract."
    evidence: "No direct AgentWebSocketServer.reset_instance test was found; existing app server tests mock get_instance instead."
    suggested_action: "Add a small lifecycle test that asserts reset creates a fresh singleton and documents that old runtime resources are not stopped."
confidence: confirmed
details: {}
---

# `AgentWebSocketServer.reset_instance`

## Actual Role

Classmethod test-oriented singleton reset helper. It only drops the class-level cached `AgentWebSocketServer` reference by assigning `cls._instance = None`; it does not stop a running WebSocket server, close active connections, clear runtime tasks, or unregister constructor-installed global callbacks.

## Key Signals

- Input: No runtime input beyond the class object.
- Output: None.
- Main side effects: Mutates process-global singleton state by clearing `AgentWebSocketServer._instance`.
- Main risk: Using it outside controlled test isolation can orphan a live server instance and leave global callback state pointing at the old object.
- Related tests: No direct reset test was found; existing app-level server tests mock `AgentWebSocketServer.get_instance`.

## Detail Index

- Detail docs pending.
