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

Atomically removes a READY slot or creates one claimed-session initialization task. READY claims retain the root pin until the first AgentServer request and schedule a replacement slot.
