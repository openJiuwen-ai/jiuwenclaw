---
symbol: AgentWebSocketServer._prepare_code_mode_chat_turn
kind: method
source: jiuwenswarm/server/agent_ws_server.py
source_role: runtime_source
audit_scope: default_health_audit
class: AgentWebSocketServer
signature: "_prepare_code_mode_chat_turn(request: AgentRequest, channel_id: str) -> tuple[str, str | None, Any]"
health:
  overall: watch
  name_behavior_match: partial
  responsibility_focus: single
  length: short
  complexity: low
  implementation_soundness: sound
  boundary_safety: partial
  input_contract: implicit
  output_contract: clear
  side_effects: explicit
  error_handling: partial
  state_mutation: shared
  dependency_coupling: medium
  test_coverage: partial
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
    dimension: test_coverage
    severity: medium
    status: open
    summary: "Direct tests do not lock AgentManager selection arguments."
    evidence: "Tests assert returned mode/sub_mode and only that get_agent was awaited, not channel_id, mapped mode, project_dir, or sub_mode. The interrupt-resume case now duplicates the happy path because approval gates were removed."
    suggested_action: "Add direct _prepare_code_mode_chat_turn assertions for get_agent(channel_id, mode, project_dir, sub_mode), including params project_dir, metadata project_dir, auto_harness.plan mapping, and None-agent failure."
  - id: ISSUE-002
    dimension: observability
    severity: low
    status: open
    summary: "No-agent failure loses selection context."
    evidence: "The method raises ValueError('Failed to get agent') after get_agent returns None; the outer handler reports only request_id plus the generic message."
    suggested_action: "Include channel_id, logical mode, agent_mode, sub_mode, and project_dir in the raised error or a structured log before propagating."
  - id: ISSUE-003
    dimension: name_behavior_match
    severity: low
    status: open
    summary: "The method name understates its all-mode selection role."
    evidence: "Unary and stream callers use it for every non-stateless chat turn; it resolves agent, team, code, and auto_harness modes rather than only code mode."
    suggested_action: "Rename it to describe general chat-turn agent selection, or narrow its callers and contract to code mode."
confidence: confirmed
details: {}
---

# `AgentWebSocketServer._prepare_code_mode_chat_turn`

## Actual Role

Resolves and canonicalizes a chat request's mode, maps logical `auto_harness` to AgentManager mode `"agent"` for instance selection, resolves the stable project directory, and returns the logical mode, sub-mode, and selected agent for unary or streaming chat handling. It mutates `request.params["mode"]` through `_apply_resolved_mode_to_request` and raises `ValueError` if AgentManager returns no agent.

## Key Signals

- Input: `AgentRequest` plus resolved `channel_id`.
- Output: Tuple of logical mode, sub-mode, and resolved agent.
- Main side effects: Canonicalizes `request.params["mode"]`; may create or reuse an agent through `AgentManager.get_agent`.
- Main risk: Its broad all-mode selection contract and exact manager arguments are only partially pinned by tests.
- Related tests: Two direct tests exist, but one is a legacy duplicate and neither asserts manager arguments or failure. Local execution is blocked at collection by missing `openjiuwen.auto_harness`; the dedicated plan-exit flow remains pending.

## Detail Index

- Detail docs pending.
