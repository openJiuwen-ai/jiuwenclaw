---
id: ADR-0001
title: AgentServer Owns Prewarmed Session Identity
status: accepted
date: 2026-07-31
confidence: confirmed
modules:
  - agentserver-runtime
  - gateway-and-channels
  - agent-harness
flows:
  - session-prewarm-allocation
---

# ADR-0001: AgentServer Owns Prewarmed Session Identity

## Context

DeepAgent construction and interaction startup are expensive on the first message. Channel-generated IDs also create two owners for session identity and make a preinitialized, session-bound runtime impossible to claim safely.

## Decision

AgentServer is the sole allocator for new and fork target IDs. It maintains one READY single-Agent slot for each enabled channel/project/work-mode key. Slots already carry their final session ID because OpenJiuwen runtime state depends on that identity. Channels obtain the ID through `session.create`; external protocol IDs are aliases.

Configuration validity uses a full SHA-256 fingerprint and a per-process boot ID. Warm misses return immediately and initialize in the background; the first real message awaits the same task. Team/Swarm modes remain outside this optimization.

## Consequences

- Warm hits remove DeepAgent construction/startup from the first-message critical path.
- All creation callers require an AgentServer connection; Web/TUI local creation fallback is intentionally unavailable.
- Idempotency is process-local through `create_token`.
- Resource use scales with enabled channels times visible/default projects times two single-Agent work modes.
- Channel trajectory prediction and pool-size reduction can be layered later without changing identity ownership.

## Alternatives Rejected

- Rebinding a prewarmed runtime to a channel-generated ID: runtime state already depends on the original ID.
- Prewarming without `start_interaction`: leaves the expensive readiness boundary on `chat.send`.
- Sharing a generic runtime across projects: project workspace, rails, tools, and prompt state are part of initialization.
