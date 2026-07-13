---
symbol: AgentWebSocketServer._handle_command_model
kind: method
source: jiuwenswarm/server/agent_ws_server.py
source_role: runtime_source
audit_scope: default_health_audit
class: AgentWebSocketServer
signature: "_handle_command_model(ws: Any, request: AgentRequest, send_lock: asyncio.Lock) -> None"
health:
  overall: risky
  name_behavior_match: partial
  responsibility_focus: mixed
  length: medium
  complexity: medium
  implementation_soundness: questionable
  boundary_safety: risky
  input_contract: weak
  output_contract: implicit
  side_effects: explicit
  error_handling: partial
  state_mutation: global
  dependency_coupling: high
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
    dimension: implementation_soundness
    severity: medium
    status: open
    summary: "add_model and list behavior are stubs on the direct AgentServer path."
    evidence: "add_model returns model_added without validating target or using params.config; the default branch returns available=['default-model'] while gateway-local handling performs real config validation and listing."
    suggested_action: "Route model management through the gateway-local handler, or make AgentServer behavior match the durable config contract."
  - id: ISSUE-002
    dimension: error_handling
    severity: high
    status: open
    summary: "switch_model can report applied=true after partial failure."
    evidence: "os.environ is mutated before cache clear/reload; clear_config_cache and reload_agents_config exceptions are swallowed before an ok=true applied=true response is sent."
    suggested_action: "Propagate reload failure or return applied=false/partial status, and validate env_updates before arbitrary global env writes."
  - id: ISSUE-003
    dimension: test_coverage
    severity: medium
    status: open
    summary: "The direct AgentServer switch_model path lacks coverage."
    evidence: "Direct tests cover only no-action and add_model; they do not cover env mutation, missing env_updates, reload failure, or non-dict env_updates."
    suggested_action: "Add focused unit tests for switch_model success, missing env_updates, reload failure, and config/list contract expectations."
confidence: confirmed
details: {}
---

# `AgentWebSocketServer._handle_command_model`

## Actual Role

Handles direct `command.model` requests by acknowledging `add_model`, applying `switch_model` environment updates to `os.environ`, clearing the memory config cache, asking `AgentManager` to reload config, or returning the current `MODEL_NAME` with a hard-coded fallback model list.

## Key Signals

- Input: `params.action` plus `target`, `model`, and `env_updates` for supported actions.
- Output: One websocket response with model status, or an error payload.
- Main side effects: Mutates process environment, clears config cache, calls agent config reload, logs, and sends a frame.
- Main risk: Direct AgentServer behavior diverges from gateway/frontend model management and can report success after partial reload failure.
- Related tests: Direct tests cover no-action and add_model only; gateway tests cover a separate model switch path.

## Detail Index

- Detail docs pending.
