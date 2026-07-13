---
symbol: AgentWebSocketServer.get_instance
kind: method
source: jiuwenswarm/server/agent_ws_server.py
source_role: runtime_source
audit_scope: default_health_audit
class: AgentWebSocketServer
signature: "get_instance(cls, *, host='127.0.0.1', port=18000, ping_interval=30.0, ping_timeout=300.0) -> AgentWebSocketServer"
health:
  overall: watch
  name_behavior_match: good
  responsibility_focus: single
  length: medium
  complexity: low
  implementation_soundness: partial
  boundary_safety: partial
  input_contract: implicit
  output_contract: clear
  side_effects: implicit
  error_handling: missing
  state_mutation: global
  dependency_coupling: medium
  test_coverage: partial
  observability: missing
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
    dimension: input_contract
    severity: medium
    status: open
    summary: "First caller silently fixes server bind settings for the process."
    evidence: "get_instance returns an existing _instance without comparing later kwargs, while startup passes configured host/port and several runtime callers invoke get_instance() with defaults."
    suggested_action: "Make the first-call contract explicit, or reject/log conflicting kwargs after initialization."
  - id: ISSUE-002
    dimension: boundary_safety
    severity: low
    status: open
    summary: "Singleton mutation is unlocked."
    evidence: "The class has _instance state but no instance lock; creation is a check-then-assign sequence."
    suggested_action: "If this can be called from multiple threads, guard creation with a class lock; otherwise document the single-threaded asyncio boundary."
  - id: ISSUE-003
    dimension: state_mutation
    severity: low
    status: open
    summary: "Singleton lifecycle is decoupled from stop/reset cleanup."
    evidence: "reset_instance only sets _instance to None, stop closes runtime resources but does not clear _instance, and the constructor registers a global ACP callback."
    suggested_action: "Add direct singleton lifecycle tests or cleanup guidance; consider clearing or replacing process-global callbacks as part of lifecycle reset."
confidence: confirmed
details: {}
---

# `AgentWebSocketServer.get_instance`

## Actual Role

Classmethod lazy singleton factory for `AgentWebSocketServer`. It returns the existing class-level `_instance` or constructs one with bind and keepalive kwargs, which also runs constructor side effects such as AgentManager creation, JiuwenBox runner lookup, and global ACP push callback registration.

## Key Signals

- Input: Keyword-only bind and keepalive settings used only on first creation.
- Output: Process-global `AgentWebSocketServer` instance.
- Main side effects: Mutates `AgentWebSocketServer._instance`; first creation also invokes constructor side effects.
- Main risk: First-call-wins configuration is implicit, no-arg callers can create a default-bound singleton before configured startup, and creation is not locked.
- Related tests: Existing tests mock `get_instance` or direct-construct harnesses; direct singleton creation, argument retention, reset, and post-stop reuse tests are pending.

## Detail Index

- Detail docs pending.
