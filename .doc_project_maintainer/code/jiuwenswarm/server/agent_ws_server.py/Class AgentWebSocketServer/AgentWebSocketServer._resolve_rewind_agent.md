---
symbol: AgentWebSocketServer._resolve_rewind_agent
kind: method
source: jiuwenswarm/server/agent_ws_server.py
source_role: runtime_source
audit_scope: default_health_audit
class: AgentWebSocketServer
signature: "_resolve_rewind_agent(channel_id: str) -> tuple[Any, Any] | None"
health:
  overall: risky
  name_behavior_match: partial
  responsibility_focus: single
  length: short
  complexity: low
  implementation_soundness: partial
  boundary_safety: risky
  input_contract: implicit
  output_contract: clear
  side_effects: none
  error_handling: partial
  state_mutation: none
  dependency_coupling: high
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
    severity: high
    status: open
    summary: "Resolver uses only channel_id, not session identity."
    evidence: "It calls get_agent_nowait(channel_id=...) without mode, project, sub-mode, or session filters; rewind_session_context then rebuilds context for the requested session on that live DeepAgent."
    suggested_action: "Resolve by session metadata or explicit session-to-agent mapping before context rebuild."
  - id: ISSUE-002
    dimension: error_handling
    severity: medium
    status: open
    summary: "Unexpected agent shape failures are not normalized."
    evidence: "agent.get_instance() and deep_agent.react_agent are direct dereferences; in full rewind this can fail after history has already been changed."
    suggested_action: "Guard lookup with getattr or catch resolver errors before destructive rewind steps."
  - id: ISSUE-003
    dimension: test_coverage
    severity: medium
    status: open
    summary: "No direct tests pin resolver behavior."
    evidence: "No tests were found for no wrapper, no DeepAgent, no react_agent, default channel fallback, or multi-agent channel selection."
    suggested_action: "Add focused fake-AgentManager tests for each resolver branch."
confidence: confirmed
details: {}
---

# `AgentWebSocketServer._resolve_rewind_agent`

## Actual Role

Synchronously finds an already-created AgentManager wrapper for a channel, unwraps its DeepAgent instance, requires a non-null `react_agent`, and returns both objects for rewind context rebuilding. It performs no creation or mutation; callers decide whether missing state is an error or partial rewind.

## Key Signals

- Input: `channel_id`; blank values become `"default"`.
- Output: `(deep_agent, react_agent)` tuple, or `None`.
- Main side effects: None directly; returned live objects are later used to rebuild and persist session context.
- Main risk: Channel-only lookup can select the wrong live agent when a channel has multiple agent instances.
- Related tests: Lower-level `rewind_session_context` tests exist; no direct resolver tests were found.

## Detail Index

- Detail docs pending.
