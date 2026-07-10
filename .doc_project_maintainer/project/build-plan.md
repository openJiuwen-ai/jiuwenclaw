---
last_updated: 2026-07-08
sync_status: partial
coverage_status: partial
flow_coverage_status: partial
code_symbol_coverage_status: partial
---

# Build Plan

## Current State

- Artifact status: initialized with AgentServer-first documentation.
- Trusted coverage: root project map, AgentServer runtime module, three AgentServer flows, selected AgentServer code symbol cards, and 63 trusted AgentWebSocketServer method audits.
- Known incomplete areas: most modules outside AgentServer, most directories, most source symbol entry docs, trusted health audits.
- Every stable source file inventoried: yes, by `inventory_symbols.py`.
- Every top-level class documented with `Actual Role` and health: no.
- Every top-level function documented with `Actual Role` and health: no.
- Every class method documented with `Actual Role` and health: no.
- Every required symbol audited or out of scope: no.
- Inventory command: `inventory_symbols.py --verify-docs`; compact/full ledgers are under `project/` and `ledger-archive/`.
- Coverage map recommended mode: multi-agent.
- Git head inventoried: `af779aa6742969e46005a2a94f49d42d7a3b443a`.
- Dirty worktree state: artifact files are untracked after this build.

## Inventory Summary

- Source files: 1236.
- Required repository symbols: 14746.
- Documented source files: 1.
- Documented required symbols: 66.
- Pending required symbols: 14680.
- Default health audit symbols: 8737.
- Repository coverage only symbols: 6009.
- Pending files: 985.
- Pending review files: 250.
- Recorded source directories: 275.
- Excluded directories: 1.
- Skipped non-source directories: 95.
- Suggested repository slices: 282.
- Suggested default health audit slices: 164.
- Audit statuses: 14683 unaudited, 63 trusted agent audited, 0 human audited, 0 out of scope.
- Default health audit statuses: 8674 unaudited, 63 trusted agent audited.
- Open symbol issue records in current docs: 160.
- Full generated ledgers are archived under `ledger-archive/*.full.json.gz`; `project/*.json` holds compact summaries to satisfy artifact size budgets.
- AgentServer method audit queue: 785 default-health methods under `jiuwenswarm/server/`, 63 documented method cards, 63 trusted agent audits, 722 unaudited methods.

## Completed Slices

- 2026-07-07: `agentserver-entrypoint` - documented `app_agentserver.py`, process startup, extension loading, server start/stop, proactive adapter, teammate bootstrap, and CLI args.
- 2026-07-07: `agentserver-dispatch-core` - documented `AgentWebSocketServer` file-level behavior plus selected dispatch, unary, stream, cancel, session, and push methods.
- 2026-07-07: `agentserver-flows` - documented Gateway E2A chat, session lifecycle, and server push flows.
- 2026-07-08: `agentserver-audit-batches-001..011` - 63 trusted AgentWebSocketServer method audits from lifecycle/dispatch/session/history commands through diff/simplify/model and MCP prelude helpers.

## Pending Slices

- `jiuwenswarm/server/runtime/agent_adapter`: inspect interface, code, deep, evolution, sysop, and team helper adapters.
- `jiuwenswarm/gateway/message_handler`: document channel message queues and Gateway-to-AgentServer forwarding.
- `jiuwenswarm/agents/harness/team`: document distributed team lifecycle and remote member bootstrap.
- `jiuwenswarm/server/runtime/skill`: document skill manager and skilldev flows.
- `jiuwenswarm/channels/web` and `jiuwenswarm/channels/tui`: document UI state and E2A-facing commands.
- `jiuwenbox`: document sandbox service boundary and policy runtime.

## Pending Flow Slices

- `agentserver-command-mcp`: config mutation, tool discovery, reload behavior, and error codes.
- `agentserver-sandbox-runtime`: jiuwenbox process startup, policy mutation, Landlock status, and runtime patching.
- `agentserver-plan-mode-exit`: plan mode state restore, exit event push, and race prevention.
- `agentserver-schedule-auto-harness`: lazy scheduler startup and task execution.
- `agentserver-history-stream`: history pagination, sanitization, size limits, and frontend reconstruction.

## Pending Code Symbol Slices

- `jiuwenswarm/server/agent_ws_server.py`: 152 symbols total; file, class, and 63 method cards exist; 88 function and method entry docs remain pending.
- `jiuwenswarm/server/runtime/agent_adapter/interface_deep.py`: 215 symbols pending.
- `jiuwenswarm/server/runtime/skill/skill_manager.py`: 152 symbols pending.
- `tests/unit_tests/agentserver`: many test-source symbols pending as repository coverage only.

## Pending Symbol Audit Slices

- Default health audit remains partial. Current active target is the 785-method `jiuwenswarm/server/` AgentServer runtime queue.
- Completed trusted batches: AgentWebSocketServer lifecycle, dispatch, push, singleton/proactive/ACP capability, hooks, code-mode/plan-mode sync, session list, session/team delete, rewind helpers/handlers, permissions config, history.get, proactive/team/workflow/history stream/add-dir, chrome/compact/context/recap/BTW/diff/simplify/model/MCP prelude handlers and helpers.
- Remaining AgentServer method audit queue: 722 methods are still unaudited and need per-symbol review.
- Next trusted audit candidates: `_normalize_mcp_payload`, `_normalize_mcp_add_payload`, `_normalize_mcp_update_payload`, `_handle_command_mcp`, `_handle_command_sandbox`, `_handle_sandbox_enable`, then sandbox/schedule/adapter/skill methods.

## Coverage Closure Audit

- Audit source: `git ls-files` plus `git status --short`, via `inventory_symbols.py`.
- Tracked path audit: partial.
- Unmapped stable tracked paths: many stable directories remain intentionally pending.
- Untracked path disposition: `.doc_project_maintainer/` is artifact output.
- Flow trace disposition: three flows documented, five AgentServer flow slices pending, wider project flows pending.
- Code symbol disposition: inventory complete, entry docs incomplete.
- Symbol audit disposition: 63 default-health symbols are closure-eligible trusted agent audits; 8674 default-health symbols remain pending, including 722 AgentServer runtime methods.
- Criteria to mark `current`: no suggested repository slices, no pending review files, every required symbol has an entry doc with `Actual Role` and health, every requested-scope audit symbol is closure eligible, and all required flows are documented or out of scope.

## Suggested Subagent Queue

- `jiuwenswarm-129`: `jiuwenswarm/server/agent_ws_server.py`, 152 symbols, runtime source, default health audit.
- `jiuwenswarm-130`: `app_agentserver.py`, gateway push, hooks, and a2ui startup files, 47 symbols.
- `tests-023..tests-046`: `tests/unit_tests/agentserver` behavior evidence, repository coverage only.
