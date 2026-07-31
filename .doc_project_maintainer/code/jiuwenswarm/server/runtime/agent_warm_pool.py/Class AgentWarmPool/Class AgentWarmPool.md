---
symbol: AgentWarmPool
kind: class
source: jiuwenswarm/server/runtime/agent_warm_pool.py
source_role: runtime_source
audit_scope: default_health_audit
health:
  overall: unknown
audit:
  status: unaudited
  confidence: confirmed
---

# AgentWarmPool

## Actual Role

Maintains one unclaimed READY session per desired channel/project/work-mode key, prevents obsolete configuration revisions from publishing, and hands claimed initialization Futures to the chat path.

## Key Contracts

- Only single-Agent keys enter READY.
- A claim is atomic and immediately schedules replenishment.
- Preparation failure cannot publish READY.
- Markers never create normal user metadata.

Health remains unaudited; see the build plan for the pending audit slice.
