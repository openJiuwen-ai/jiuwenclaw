---
id: gateway-and-channels
name: Gateway And Channels
confidence: inferred
last_updated: 2026-07-13
read_when: "Working on Gateway routing, channel adapters, frontend/TUI command forwarding, or AgentServer client behavior."
---

# Gateway And Channels

## Responsibility

Accepts user/channel/front-end input, normalizes it into E2A or legacy-compatible request data, forwards it to AgentServer, receives unary or streamed responses, and dispatches visible output back to the appropriate channel.

## Boundaries

- Owns: channel ingress, Gateway message queues, WebSocket AgentServer client, routing/session map helpers, frontend/TUI transport surfaces.
- Does not own: AgentServer adapter execution, agent tool semantics, or final durable session ownership once delegated to AgentServer.

## Current Evidence

- `jiuwenswarm/gateway/routing/agent_client.py` receives AgentServer frames, routes normal responses by request ID, and treats server-push frames as out-of-band events.
- `docs/en/E2A-protocol.md` describes Gateway -> AgentServer E2A field contracts.
- Tests outside the AgentServer directory cover AgentServer client queueing, reconnect/close behavior, stream tail grace, and timeout policy.

## Related Flows

- `gateway-agentserver-e2a-chat` and `agentserver-server-push`
- `agentserver-command-mcp` and `agentserver-sandbox-runtime`
- `agentserver-plan-mode-exit`
- `agentserver-schedule-auto-harness`
- `agentserver-history-stream`

## Pending

Document the remaining Gateway message handlers and channel-specific identity/ownership rules; the new flow docs trace only the AgentServer-facing paths needed for the scoped delivery.
