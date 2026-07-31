---
symbol: JiuWenSwarmDeepAdapter.prepare_session
kind: method
source: jiuwenswarm/server/runtime/agent_adapter/interface_deep.py
class: JiuWenSwarmDeepAdapter
audit:
  status: unaudited
---

# JiuWenSwarmDeepAdapter.prepare_session

## Actual Role

Creates or reuses the session-scoped adapter, completes `create_instance` and `start_interaction`, then delegates stable workspace, rails, tools, and prompt configuration through the child's public `configure_session_runtime` boundary. It does not attach output or send input.
