---
symbol: AgentWebSocketServer._trigger_before_chat_request_hook
kind: method
source: jiuwenswarm/server/agent_ws_server.py
source_role: runtime_source
audit_scope: default_health_audit
class: AgentWebSocketServer
signature: "_trigger_before_chat_request_hook(self, request: AgentRequest) -> None"
health:
  overall: watch
  name_behavior_match: good
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
    dimension: test_coverage
    severity: medium
    status: open
    summary: "Missing direct AgentServer before-chat hook tests."
    evidence: "Searches found no AgentWebSocketServer._trigger_before_chat_request_hook or AgentServerHookEvents.BEFORE_CHAT_REQUEST coverage; the only before-chat test hit stubs the analogous Gateway hook."
    suggested_action: "Add async AgentWebSocketServer tests for gated methods, non-chat no-op behavior, context fields, shared params mutation, non-dict params normalization, and hook exception behavior."
  - id: ISSUE-002
    dimension: error_handling
    severity: medium
    status: open
    summary: "Extension hook failure policy is implicit."
    evidence: "The method directly awaits ExtensionRegistry.get_instance().trigger(...) with no local handling; _handle_message catches the exception generically and sends an error response."
    suggested_action: "Make the intended policy explicit in tests or code: either confirm hook failures should fail the chat request with clear logging, or isolate optional extension failures so chat dispatch can continue."
confidence: confirmed
details: {}
---

# `AgentWebSocketServer._trigger_before_chat_request_hook`

## Actual Role

AgentServer-side extension bridge for `chat.send`, `chat.resume`, and `chat.user_answer` requests. It normalizes non-dict `request.params` to `{}`, passes the same mutable params object into `AgentServerChatHookContext`, and awaits the `agent_server:before_chat_request` hook before unary or stream dispatch continues.

## Key Signals

- Input: `AgentRequest`; only chat send, resume, and answer pass the eligibility gate.
- Output: None.
- Main side effects: May replace `request.params`; extensions can mutate the shared params dict before downstream agent handling.
- Main risk: Hook or registry failures are not handled locally and become generic request failures in `_handle_message`.
- Related tests: No direct AgentServer hook tests were found; only a Gateway-side analogous hook stub exists.

## Detail Index

- Detail docs pending.
