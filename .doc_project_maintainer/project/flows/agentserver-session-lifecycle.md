---
id: agentserver-session-lifecycle
name: AgentServer Session Lifecycle
status: partial
confidence: confirmed
last_updated: 2026-07-07
user_visible_surface: "Session create, switch, list, fork, rewind, delete, history, and team session operations."
source_of_truth:
  - "agent session directories"
  - "session metadata"
  - "history records"
  - "OpenJiuwen checkpointer"
modules:
  - agentserver-runtime
  - agent-harness
directories:
  - jiuwenswarm/server
code_symbols:
  - AgentWebSocketServer._handle_session_create
  - AgentWebSocketServer._handle_session_fork
  - AgentWebSocketServer._handle_history_get_stream
entrypoints:
  - jiuwenswarm/server/agent_ws_server.py
---

# AgentServer Session Lifecycle

## Outcome

User and team sessions can be created, switched, listed, renamed, deleted, forked, rewound, compacted, and paged through history from Gateway requests.

## Causal Path

`_handle_message` routes session and history `ReqMethod` values to local handlers before generic chat handling. Session create calls `AgentManager.create_session` and, for team mode, prepares team session switching. Fork copies filesystem session state, in-memory context, and DeepAgent state. History handlers read persisted history records, filter restorable records, enforce page/byte limits, and encode sanitized records. Delete and team delete handlers cross both session metadata and active runtime state.

## State Classification

- Source of truth: session directories, metadata files, history records, checkpointer state.
- Runtime state: active agent/session instances, team managers, stream tasks.
- Derived output: paged and sanitized history payloads.

## Replay, Restore, Or Reconstruction

History paging reverses persisted restorable records so latest records appear first. Rewind and rewind-restore paths depend on session operations and active adapter state; full flow detail is pending.

## Contract

Handlers take `AgentRequest.params` fields such as `session_id`, `source_session_id`, `target_session_id`, `title`, page parameters, and mode hints. Responses are `AgentResponse` payloads encoded as E2A wire.

## Verification

Tests cover ACP/session creation and switching, session/team delete, history payload limits, session operations, and agentserver modes. Direct full rewind/restore flow coverage still needs review.

## Known Gaps

Detailed documentation for `_handle_session_rewind_full`, `_handle_session_rewind_context`, `_handle_team_delete`, and `_handle_session_delete` is pending.
