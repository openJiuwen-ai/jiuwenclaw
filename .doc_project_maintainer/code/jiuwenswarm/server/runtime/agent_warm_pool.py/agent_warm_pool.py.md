---
source: jiuwenswarm/server/runtime/agent_warm_pool.py
source_role: runtime_source
audit_scope: default_health_audit
language: python
confidence: confirmed
last_updated: 2026-08-01
---

# `agent_warm_pool.py`

## Actual Role

Owns process-local reconciliation and atomic claiming of session-bound READY DeepAgents. It defines immutable warm identity/revision records, guarded marker cleanup, bounded background initialization, stale revision disposal, claimed-session waiting, and root-agent pinning.

## Symbol Inventory

- Records: `WarmKey`, `WarmRevision`, `WarmSlot`, `WarmClaim`.
- Runtime class: `AgentWarmPool`.
- Key methods documented for this scoped change: `sync`, `claim`, `wait_for_session`, and `close`.

## Health

Audit status: `unaudited`. Focused behavior tests exist, but no independent symbol health audit was performed.
