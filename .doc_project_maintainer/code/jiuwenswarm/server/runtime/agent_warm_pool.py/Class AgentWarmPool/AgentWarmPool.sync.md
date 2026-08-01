---
symbol: AgentWarmPool.sync
kind: method
source: jiuwenswarm/server/runtime/agent_warm_pool.py
class: AgentWarmPool
audit:
  status: unaudited
---

# AgentWarmPool.sync

## Actual Role

Builds desired keys from enabled channels and visible/default projects, advances the config fingerprint revision, stales old slots/tasks, schedules missing targets, and returns non-blocking pool statistics.
