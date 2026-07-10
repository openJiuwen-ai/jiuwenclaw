---
symbol: AgentWebSocketServer._send_error_response
kind: method
source: jiuwenswarm/server/agent_ws_server.py
source_role: runtime_source
audit_scope: default_health_audit
class: AgentWebSocketServer
signature: "_send_error_response(ws: Any, request: AgentRequest, send_lock: asyncio.Lock, error: str, code: str | None = None) -> str"
health:
  overall: watch
  name_behavior_match: partial
  responsibility_focus: single
  length: short
  complexity: low
  implementation_soundness: sound
  boundary_safety: safe
  input_contract: implicit
  output_contract: implicit
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
    dimension: name_behavior_match
    severity: low
    status: open
    summary: "Name and signature imply sending, but the helper only returns JSON."
    evidence: "The method accepts ws/send_lock and the docstring says Send, yet it only builds AgentResponse and returns json.dumps; callers send the result separately."
    suggested_action: "Rename to _build_error_response_wire and remove unused args, or make it own lock/send behavior."
  - id: ISSUE-002
    dimension: test_coverage
    severity: medium
    status: open
    summary: "No direct regression tests cover helper output."
    evidence: "No tests were found for _send_error_response or its rewind validation call sites; only generic codec and slash-command parsing tests are adjacent."
    suggested_action: "Add tests for payload, code, metadata preservation, and rewind bad-input branches."
confidence: confirmed
details: {}
---

# `AgentWebSocketServer._send_error_response`

## Actual Role

Builds and JSON-serializes a failed `AgentResponse` as an E2A wire response string for rewind validation and unavailable-agent paths. It does not send on the WebSocket itself.

## Key Signals

- Input: `AgentRequest` identity/metadata, an error string, and optional code.
- Output: E2A failed response JSON with `body.message` and details containing the error and optional code.
- Main side effects: None; `ws` and `send_lock` are unused.
- Main risk: The helper name/signature imply it sends, so callers must remember to lock and `ws.send` the returned wire.
- Related tests: No direct tests found; generic E2A error roundtrip and slash-command parsing are adjacent.

## Detail Index

- Detail docs pending.
