---
symbol: AgentWebSocketServer._pre_check_mcp_server
kind: method
source: jiuwenswarm/server/agent_ws_server.py
source_role: runtime_source
audit_scope: default_health_audit
class: AgentWebSocketServer
signature: "_pre_check_mcp_server(server_payload: dict[str, Any]) -> tuple[bool, str]"
health:
  overall: watch
  name_behavior_match: good
  responsibility_focus: single
  length: medium
  complexity: medium
  implementation_soundness: partial
  boundary_safety: partial
  input_contract: implicit
  output_contract: clear
  side_effects: hidden
  error_handling: partial
  state_mutation: global
  dependency_coupling: high
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
    dimension: state_mutation
    severity: medium
    status: open
    summary: "Global logging suppression spans awaited connect/disconnect work."
    evidence: "The method uses logging.disable(CRITICAL) before awaiting client.connect and resets to NOTSET in finally, affecting concurrent server tasks."
    suggested_action: "Use targeted logger filtering or preserve and restore the prior disable level instead of process-wide logging.disable."
  - id: ISSUE-002
    dimension: test_coverage
    severity: medium
    status: open
    summary: "No direct tests cover pass, fail, timeout, or cleanup behavior."
    evidence: "The MCP add test monkeypatches _pre_check_mcp_server, so real temporary-client behavior is not exercised."
    suggested_action: "Add focused async tests with fake ToolMgr/client for success, refused, timeout, exception, and disconnect failure."
  - id: ISSUE-003
    dimension: implementation_soundness
    severity: low
    status: open
    summary: "MCP config construction is duplicated instead of using the shared config builder."
    evidence: "This method builds McpServerConfig inline while runtime registration uses common.mcp_config.build_mcp_server_config, which also handles timeout_s."
    suggested_action: "Share the MCP config builder path or factor a single helper for pre-check, fetch, and runtime config conversion."
confidence: confirmed
details: {}
---

# `AgentWebSocketServer._pre_check_mcp_server`

## Actual Role

Builds a temporary MCP client from a normalized server payload, attempts to connect with a 15 second timeout, disconnects with a 5 second timeout, and returns an `(ok, message)` tuple. It is used by `command.mcp add` to block failed enabled stdio server additions when pre-check is deemed necessary.

## Key Signals

- Input: MCP server payload with name/transport and stdio command/args/cwd/env or URL/headers.
- Output: `(True, message)` for skipped or passed checks; `(False, message)` for refused, timeout, or exception.
- Main side effects: May launch stdio/network MCP clients, globally disables Python logging during awaited work, and disconnects a temporary client.
- Main risk: Process-wide logging suppression and real temporary-client behavior are untested.
- Related tests: MCP add tests stub this method; no direct helper tests were found.

## Detail Index

- Detail docs pending.
