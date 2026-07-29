---
id: agentserver-session-lifecycle
name: AgentServer Session Lifecycle
status: partial
confidence: confirmed
last_updated: 2026-07-13
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

User and team session operations are exposed for create, switch, list, rename, delete, fork, rewind, compact, and history. Static analysis confirms the RPC paths but also shows that `session.create` does not itself reserve or persist a unique session and that caller-provided IDs reach later filesystem-backed operations.

## Causal Path

`_handle_message` routes session and history `ReqMethod` values to local handlers before generic chat handling. Session create chooses or accepts an ID and may prepare team switching, but does not durably reserve the ID. Fork copies filesystem session state, history/context, and DeepAgent state through multiple non-atomic steps. History handlers read persisted records, filter restorable records, enforce per-record/page limits, and encode sanitized records. Delete and team delete handlers cross metadata, filesystem, and active runtime state.

## State Classification

- Source of truth: session directories, metadata files, history records, checkpointer state.
- Runtime state: active agent/session instances, team managers, stream tasks.
- Derived output: paged and sanitized history payloads.

## Replay, Restore, Or Reconstruction

History paging rereads the full persisted history, filters restorable records, reverses them so latest records appear first, and slices a page. Fork and rewind reconstruct several stores independently; no transaction or recovery journal spans filesystem copies, history, checkpointer state, and active runtime state.

## Contract

Handlers take `AgentRequest.params` fields such as `session_id`, `source_session_id`, `target_session_id`, `title`, page parameters, and mode hints. Responses are `AgentResponse` payloads encoded as E2A wire. A strict normalized `sess_*` identifier and resolved-path containment contract was not found at the AgentWebSocketServer boundary.

## Verification

Tests cover ACP/session creation and switching, session/team delete, history payload limits, session operations, and AgentServer modes. The session-create success test is mock-heavy; direct hostile-ID containment, duplicate reservation, partial fork failure, wrong Agent variant, send failure, and full rewind/restore coverage remain missing.

## Known Gaps

Caller-controlled absolute or `..` session IDs can reach path composition in history/session helpers without a confirmed containment check. Create may report success without durable creation or uniqueness; fork can leave partial state while later failure is normalized ambiguously. Detailed downstream audits for rewind, metadata stores, history storage, checkpointer state, and team teardown remain pending.
