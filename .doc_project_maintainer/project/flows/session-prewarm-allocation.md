---
id: session-prewarm-allocation
name: Session Prewarm And Allocation
status: partial
confidence: confirmed
last_updated: 2026-08-01
user_visible_surface: "Low-latency creation of single-Agent work/code sessions across enabled channels."
source_of_truth:
  - "AgentServer session metadata"
  - "AgentServer process-local warm pool"
modules:
  - agentserver-runtime
  - gateway-and-channels
  - agent-harness
directories:
  - jiuwenswarm/server
  - jiuwenswarm/gateway
  - jiuwenswarm/channels
code_symbols:
  - AgentWarmPool
  - AgentWarmPool.sync
  - AgentWarmPool.claim
  - AgentWebSocketServer._handle_agent_prewarm_sync
  - AgentWebSocketServer._handle_session_create
  - JiuWenSwarmDeepAdapter.prepare_session
  - JiuWenSwarmDeepAdapter.configure_session_runtime
entrypoints:
  - jiuwenswarm/gateway/app_gateway.py
  - jiuwenswarm/server/agent_ws_server.py
---

# Session Prewarm And Allocation

## Outcome

For each enabled channel and visible/default project, AgentServer maintains one unclaimed, session-bound READY DeepAgent for single-Agent work or code mode. A new session receives its ID from AgentServer. A warm hit avoids `create_instance` and `start_interaction` on the first `chat.send`; a miss returns an ID immediately, initializes in the background, and makes the first message await that same task.

Team, `code.team`, and Swarm creation bypass the warm pool.

## Causal Path

1. Gateway finishes channel registration and sends `agent.prewarm.sync` with the deduplicated live channel IDs. Configuration/channel changes trigger another non-blocking sync; a periodic scan is the fallback.
2. AgentServer resolves visible projects and default work/code projects into `WarmKey` values. `AgentWarmPool` reconciles one READY slot per key with bounded initialization concurrency.
3. Preparation gets the root adapter, creates the session-scoped DeepAdapter, runs `create_instance`, applies session-stable rails/tools/workspace through `configure_session_runtime`, and calls `start_interaction(session_id)`. It never calls `attach_output` or `send_input`.
4. `session.create` validates project/work-mode binding before allocation. `create_token` retries reuse the same claim. A READY slot is atomically removed and replenished; a miss starts one claimed-session preparation task.
5. AgentServer writes normal metadata only after claim. The prewarm marker is retained through the claim and removed only after metadata commits, closing the crash gap without exposing blank slots in normal session listings.
6. `_resolve_adapter`, unary chat, and streaming chat await the claimed session's preparation task. Request IDs, input, attachments, skills, permissions, request workspace/trusted dirs, ACP capabilities, output attachment, and real input remain request-bound.
7. Web, TUI, IM, ACP, A2A, SSH, and single-Agent Cron use the returned ID. ACP/A2A/SSH retain protocol IDs as Gateway aliases. Fork IDs are also AgentServer allocated but do not consume blank warm slots.

## State And Identity

- Source of truth: claimed session metadata and history under AgentServer ownership.
- Cache: READY slots, preparation tasks, `create_token` results, and external-channel aliases are process-local.
- Disposable state: `.prewarm/<session_id>.json` markers identify unclaimed slots and include `boot_id`.
- Revision: `boot_id + SHA-256(config/env) + sequence`; only matching current fingerprints may publish READY.
- ID format: `<channel>_<timestamp/random>`.

On startup, old-boot markers and unclaimed metadata-less directories are removed. Claimed/history sessions are not removed. Configuration changes stale only unclaimed slots; active sessions continue through existing reload behavior.

## Failure, Ordering, And Idempotency

- Project validation precedes allocation.
- Explicit IDs are rejected by `session.create`; existing-session restoration uses `session.switch`.
- `create_token` is required by adapted frontends and enables response-loss retry.
- Gateway-owned Web creation overwrites any request-body `user_id` with the authenticated connection identity before forwarding to AgentServer.
- Initialization exceptions are logged and never publish READY.
- Continuous reconciles cancel superseded warm tasks; late old-revision completion cannot enter the pool.
- A READY root stays pinned until the claimed session reaches its first AgentServer request, preventing idle retirement in the create-to-send gap.

## Verification

- `tests/unit_tests/agentserver/test_agent_warm_pool.py`: key normalization, one READY slot, atomic concurrent claims, replenishment, config replacement, failed preparation, marker activation, and boot cleanup.
- Web `test:create-conversation-session`: AgentServer response ID and stable-token retry.
- Web production build and TUI typecheck cover channel call-site contracts.
- `tests/unit_tests/gateway/test_cron_scheduler.py`: single-Agent Cron performs `session.create` before chat and uses the returned ID.
- AgentServer ACP/plan-mode, Web identity, project binding, and TUI forwarding/KVC ownership suites: 139 passed together.
- The full Windows unit run completed with 3,791 passed, 4 skipped, and 14 failures outside this flow's changed surfaces.
- CodeCheck cleanup removed obsolete local Web/TUI creation fallbacks, reduced warm-pool condition complexity without changing guards, and replaced protected child access with a public stable-config boundary; 166 related tests passed.

## Known Gaps

The pool is process-local and does not preserve idempotency tokens across AgentServer restart. Full live multi-channel integration, load/resource limits, checkpointer cleanup validation, long-lived unclaimed-claim pin expiry, and real OpenJiuwen timing metrics remain pending. The repository-wide symbol audit was not expanded; changed symbols remain unaudited or audit-expired.
