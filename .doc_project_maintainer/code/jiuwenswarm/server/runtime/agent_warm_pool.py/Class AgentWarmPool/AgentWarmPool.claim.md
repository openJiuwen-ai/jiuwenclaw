---
symbol: AgentWarmPool.claim
kind: method
source: jiuwenswarm/server/runtime/agent_warm_pool.py
class: AgentWarmPool
audit:
  status: unaudited
---

# AgentWarmPool.claim

## Actual Role

Atomically removes a READY slot, promotes a matching warming task with its existing Session ID, or creates one claimed-session initialization task on the foreground lane. READY/promoted claims retain the root pin until the first AgentServer request, prioritize their key for replenishment, and do not start new speculative work during active chat.
