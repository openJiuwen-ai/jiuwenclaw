---
symbol: AgentWebSocketServer._handle_command_add_dir
kind: method
source: jiuwenswarm/server/agent_ws_server.py
source_role: runtime_source
audit_scope: default_health_audit
class: AgentWebSocketServer
signature: "_handle_command_add_dir(ws: Any, request: AgentRequest, send_lock: asyncio.Lock) -> None"
health:
  overall: risky
  name_behavior_match: partial
  responsibility_focus: mixed
  length: medium
  complexity: medium
  implementation_soundness: partial
  boundary_safety: risky
  input_contract: weak
  output_contract: implicit
  side_effects: explicit
  error_handling: partial
  state_mutation: global
  dependency_coupling: high
  test_coverage: partial
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
    summary: "Durable trust mutation accepts any non-empty path from the command route."
    evidence: "The handler routes COMMAND_ADD_DIR directly, stringifies path, and persist_cli_trusted_directory resolves strict=False then writes external_directory allow."
    suggested_action: "Gate this command by channel, auth, or permission policy and validate intended directory scope/existence before persistence."
  - id: ISSUE-002
    dimension: input_contract
    severity: medium
    status: open
    summary: "remember is echoed but ignored."
    evidence: "The handler reads remember but persists whenever path is non-empty; the direct test only asserts the echo."
    suggested_action: "Honor remember=false as non-persistent behavior, or remove/document it as response metadata only."
  - id: ISSUE-003
    dimension: output_contract
    severity: low
    status: open
    summary: "Successful persistence can hide failed live reload."
    evidence: "reload_agents_config exceptions are debug-logged and the response still reports ok=true from persist.ok."
    suggested_action: "Expose reload_applied/reload_error in payload or document that ok only means config write succeeded."
  - id: ISSUE-004
    dimension: test_coverage
    severity: medium
    status: open
    summary: "Direct tests do not cover real YAML write, invalid input, reload failure, or boundary policy."
    evidence: "The direct add-dir test monkeypatches persist_cli_trusted_directory and asserts only the success payload shape."
    suggested_action: "Add tests for empty path, helper failure, temp config persistence, reload failure, and unauthorized routing."
confidence: confirmed
details: {}
---

# `AgentWebSocketServer._handle_command_add_dir`

## Actual Role

Handles `command.add_dir` by taking `params.path`, persisting it as an allowed external directory in config, best-effort reloading live agents with the latest config, and sending one response containing the original path, echoed `remember`, and persistence result.

## Key Signals

- Input: `params.path`; optional `remember` is echoed but does not change behavior.
- Output: One AgentResponse whose `ok` mirrors `persist.ok`.
- Main side effects: Mutates config permissions, may reload agents, logs failures, and sends a websocket frame.
- Main risk: A routed command can durably trust an arbitrary resolved path with only non-empty validation.
- Related tests: One direct success-payload test exists; real persistence, failure, reload, and authorization paths are missing.

## Detail Index

- Detail docs pending.
