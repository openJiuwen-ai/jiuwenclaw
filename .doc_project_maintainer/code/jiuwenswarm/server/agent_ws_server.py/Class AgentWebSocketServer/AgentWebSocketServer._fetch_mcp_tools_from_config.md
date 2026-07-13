---
symbol: AgentWebSocketServer._fetch_mcp_tools_from_config
kind: method
source: jiuwenswarm/server/agent_ws_server.py
source_role: runtime_source
audit_scope: default_health_audit
class: AgentWebSocketServer
signature: "_fetch_mcp_tools_from_config(entry: dict[str, Any]) -> list[dict[str, Any]]"
health:
  overall: risky
  name_behavior_match: good
  responsibility_focus: mixed
  length: medium
  complexity: medium
  implementation_soundness: partial
  boundary_safety: risky
  input_contract: implicit
  output_contract: weak
  side_effects: explicit
  error_handling: partial
  state_mutation: isolated
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
    dimension: boundary_safety
    severity: high
    status: open
    summary: "Temporary MCP probe has no explicit timeout around connect, list_tools, or disconnect."
    evidence: "The method directly awaits client.connect, client.list_tools, and client.disconnect; stdio/list_tools paths can await MCP session operations while the gateway has only an outer wait limit."
    suggested_action: "Wrap connect/list/disconnect with bounded timeouts or reliably pass/apply the MCP timeout settings for this helper."
  - id: ISSUE-002
    dimension: implementation_soundness
    severity: medium
    status: open
    summary: "MCP config construction is duplicated and diverges from the shared builder."
    evidence: "The method rebuilds McpServerConfig inline and omits timeout_s/server_id handling from common.mcp_config.build_mcp_server_config; http/streamable-http support depends on process-global patch state."
    suggested_action: "Delegate to build_mcp_server_config and make unsupported transport behavior explicit."
  - id: ISSUE-003
    dimension: output_contract
    severity: medium
    status: open
    summary: "Empty list conflates invalid config, connection failure, unsupported transport, and genuinely zero tools."
    evidence: "Invalid inputs and connect=false return []; callers catch temp-connection exceptions and still return tool_count 0 or tools []."
    suggested_action: "Return structured status/error metadata or surface a degraded/error state distinct from an empty tool catalog."
  - id: ISSUE-004
    dimension: test_coverage
    severity: medium
    status: open
    summary: "No direct coverage exists for the helper or list_tools/show fallback behavior."
    evidence: "Existing command.mcp tests cover list/add/update/remove/enable/minimal flow but not _fetch_mcp_tools_from_config, timeout behavior, unsupported transport, headers/timeout_s propagation, or list_tools fallback."
    suggested_action: "Add unit tests with fake ToolMgr clients for success, invalid config, connect false, list_tools error, timeout_s propagation, and caller fallback responses."
confidence: confirmed
details: {}
---

# `AgentWebSocketServer._fetch_mcp_tools_from_config`

## Actual Role

Builds a temporary MCP client from one config entry, connects to the server, lists tool cards, converts them to `command.mcp` wire dictionaries, and disconnects. It is used as a fallback when cached `ToolMgr` data is unavailable for `/mcp show` and interactive `list_tools`.

## Key Signals

- Input: MCP config entry with name/transport and stdio command/args/cwd/env or URL/headers; enabled is checked by callers.
- Output: List of tool dictionaries, or `[]` for invalid config, missing command/url, connect false, or no cards.
- Main side effects: Opens a temporary MCP network/subprocess session, logs warnings, and disconnects the client.
- Main risk: Temporary MCP operations are unbounded here and empty output hides failure modes.
- Related tests: No direct helper or temp-fallback tests were found; command.mcp tests cover other flows.

## Detail Index

- Detail docs pending.
