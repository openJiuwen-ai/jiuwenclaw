---
symbol: AgentWebSocketServer._should_sync_code_mode_state
kind: method
source: jiuwenswarm/server/agent_ws_server.py
source_role: runtime_source
audit_scope: default_health_audit
class: AgentWebSocketServer
signature: "_should_sync_code_mode_state(request: AgentRequest) -> bool"
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
    dimension: test_coverage
    severity: low
    status: open
    summary: "Direct tests cover one excluded RPC but not the full allowlist or legacy fallback."
    evidence: "test_plan_approval.py covers SKILLS_LIST returning False, and plan-mode orchestration tests indirectly use CHAT_SEND; no direct assertions were found for CHAT_RESUME, CHAT_ANSWER, or req_method is None."
    suggested_action: "Add a parameterized unit test covering None, CHAT_SEND, CHAT_RESUME, CHAT_ANSWER, SKILLS_LIST, and a representative command or history RPC."
confidence: confirmed
details: {}
---

# `AgentWebSocketServer._should_sync_code_mode_state`

## Actual Role

Pure gate used by `_ensure_code_mode_state` to decide whether a request is eligible to synchronize code-mode plan or normal state. It allows legacy requests with no `req_method`, and otherwise only allows chat-turn RPCs: `chat.send`, `chat.resume`, and `chat.user_answer`.

## Key Signals

- Input: `AgentRequest`; `req_method` may be `None`.
- Output: Boolean; true only for legacy/no-method requests or `_CODE_MODE_SYNC_METHODS`.
- Main side effects: None.
- Main risk: The helper is correct but only partially pinned by direct tests, leaving allowlist drift possible.
- Related tests: Direct coverage exists for excluding `skills.list`; `_ensure_code_mode_state` tests indirectly exercise `CHAT_SEND`.

## Detail Index

- Detail docs pending.
