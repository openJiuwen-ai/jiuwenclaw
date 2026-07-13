---
path: jiuwenswarm/server
encoded: jiuwenswarm__server
modules:
  - agentserver-runtime
confidence: confirmed
last_updated: 2026-07-07
read_when: "Editing AgentServer entrypoints, WebSocket dispatch, runtime services, sandbox, hooks, or gateway push."
---

# `jiuwenswarm/server`

## Purpose

Contains the AgentServer process entrypoint, the WebSocket server used by Gateway, runtime services behind agent execution, sandbox integration, hooks, utility services, and server-push helpers.

## Important Files

- `app_agentserver.py`: standalone AgentServer CLI/process lifecycle.
- `agent_ws_server.py`: central Gateway WebSocket server and AgentServer request dispatch surface.
- `gateway_push/wire.py`: server-push E2A response wire encoder.
- `gateway_push/transport.py`: default in-process transport that forwards push messages through the AgentServer singleton.
- `runtime/agent_manager.py`: channel/session agent instance management and config reload boundary.
- `runtime/proactive_adapter.py`: proactive recommendation engine attachment.
- `runtime/session/*`: session history and metadata support.
- `sandbox/jiuwenbox_runner.py`: jiuwenbox process runner.

## Related Flows

- `gateway-agentserver-e2a-chat`: normal request/response and streaming chat.
- `agentserver-session-lifecycle`: session state, history, rewind, delete, fork.
- `agentserver-server-push`: out-of-band downstream events.

## Related Code Symbols

- `_run`, `main`
- `AgentWebSocketServer._handle_message`
- `AgentWebSocketServer._handle_stream`
- `AgentWebSocketServer._handle_unary`
- `AgentWebSocketServer._handle_cancel`
- `AgentWebSocketServer.send_push`

## Coverage

Partial. `agent_ws_server.py` has 152 discovered symbols; selected high-risk cards exist, but most entry docs and all trusted audits remain pending.
