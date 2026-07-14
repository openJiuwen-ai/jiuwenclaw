---
symbol: AgentWebSocketServer._should_trigger_before_chat_request_hook
kind: method
source: jiuwenswarm/server/agent_ws_server.py
source_role: runtime_source
audit_scope: default_health_audit
class: AgentWebSocketServer
signature: "_should_trigger_before_chat_request_hook(request: AgentRequest) -> bool"
health:
  overall: watch
  name_behavior_match: good
  responsibility_focus: single
  length: short
  complexity: low
  implementation_soundness: sound
  boundary_safety: safe
  input_contract: implicit
  output_contract: clear
  side_effects: none
  error_handling: clear
  state_mutation: none
  dependency_coupling: low
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
    dimension: test_coverage
    severity: low
    status: open
    summary: "Missing direct AgentServer tests for before-chat hook eligibility."
    evidence: "Searches found no direct AgentServer predicate or hook-gating tests; only a Gateway-side hook stub was found in tests/unit_tests/gateway/test_message_handler_evolution.py."
    suggested_action: "Add parameterized tests for CHAT_SEND, CHAT_RESUME, CHAT_ANSWER, non-chat methods, and None, plus a hook-gating test through _trigger_before_chat_request_hook."
  - id: ISSUE-002
    dimension: dependency_coupling
    severity: low
    status: open
    summary: "AgentServer and Gateway maintain duplicate before-chat hook eligibility lists."
    evidence: "AgentWebSocketServer checks CHAT_SEND, CHAT_RESUME, and CHAT_ANSWER, while Gateway MessageHandler has a parallel predicate for the analogous before-chat hook."
    suggested_action: "Centralize the eligible method set or add parity tests so future chat method additions do not drift between Gateway and AgentServer."
confidence: confirmed
details: {}
---

# `AgentWebSocketServer._should_trigger_before_chat_request_hook`

## Actual Role

Pure static predicate that returns `True` only for AgentServer chat request methods that should run the `before_chat_request` extension hook: `CHAT_SEND`, `CHAT_RESUME`, and `CHAT_ANSWER`. It gates `_trigger_before_chat_request_hook` before the main request dispatch path continues.

## Key Signals

- Input: `AgentRequest` with `req_method` populated from `ReqMethod` or `None`.
- Output: Boolean; true only for the three chat-turn request methods.
- Main side effects: None.
- Main risk: Low implementation risk, but hook eligibility is duplicated with Gateway and lacks direct AgentServer tests.
- Related tests: No direct AgentServer predicate or hook-gating tests were found; a Gateway evolution test only stubs the analogous hook.

## Detail Index

- Detail docs pending.
