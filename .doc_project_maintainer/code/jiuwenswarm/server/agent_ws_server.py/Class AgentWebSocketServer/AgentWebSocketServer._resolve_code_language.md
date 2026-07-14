---
symbol: AgentWebSocketServer._resolve_code_language
kind: method
source: jiuwenswarm/server/agent_ws_server.py
source_role: runtime_source
audit_scope: default_health_audit
class: AgentWebSocketServer
signature: "_resolve_code_language() -> str"
health:
  overall: watch
  name_behavior_match: partial
  responsibility_focus: single
  length: short
  complexity: low
  implementation_soundness: questionable
  boundary_safety: partial
  input_contract: implicit
  output_contract: implicit
  side_effects: none
  error_handling: partial
  state_mutation: none
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
    dimension: implementation_soundness
    severity: medium
    status: open
    summary: "No production call site remains for the helper."
    evidence: "Search found only the definition and two test monkeypatch assignments; the current code-mode chat preparation path does not call this helper."
    suggested_action: "Remove the helper and stale monkeypatches if plan-approval language is no longer server-owned, or wire it into a real call path if still required."
  - id: ISSUE-002
    dimension: input_contract
    severity: medium
    status: open
    summary: "The helper reads a noncanonical top-level language key."
    evidence: "The method returns config.get(\"language\", \"cn\"), while default config and mutators use preferred_language and current code-runtime language policy normalizes preferred_language."
    suggested_action: "If retained, resolve from preferred_language with the same normalization and mapping used by current code-mode language policy."
  - id: ISSUE-003
    dimension: test_coverage
    severity: low
    status: open
    summary: "Missing direct tests for configured language, fallback, and failure behavior."
    evidence: "Existing tests only assign server._resolve_code_language = MagicMock(return_value=\"cn\"); no test calls the real method."
    suggested_action: "Add direct unit tests for preferred/default language behavior and get_config() failure, or delete the helper with the stale test setup."
confidence: confirmed
details: {}
---

# `AgentWebSocketServer._resolve_code_language`

## Actual Role

Small static helper that reads global config through `get_config()` and returns `config["language"]`, falling back to `"cn"` on a missing key or any exception. Despite the docstring, no production caller was found; current code-mode language policy elsewhere uses `preferred_language` and normalization.

## Key Signals

- Input: None; depends on global config file access through `get_config()`.
- Output: String intended as `"cn"` or `"en"`, though the implementation can return any configured `language` value.
- Main side effects: None directly, aside from config reads inside `get_config()`.
- Main risk: The helper appears orphaned and reads a stale config key.
- Related tests: No direct test invokes the method; two plan-mode tests only monkeypatch the attribute.

## Detail Index

- Detail docs pending.
