---
id: agentserver-runtime
name: AgentServer Runtime
confidence: confirmed
last_updated: 2026-07-07
read_when: "Working on AgentServer startup, Gateway WebSocket handling, sessions, commands, server push, ACP, scheduler, sandbox, or runtime services."
---

# AgentServer Runtime

## Responsibility

Hosts the standalone AgentServer process and the WebSocket RPC surface used by Gateway. It decodes E2A or legacy request payloads, resolves channel/session/mode context, routes special control methods, invokes agent adapters through `AgentManager`, streams responses, and pushes agent-originated events back to Gateway.

## Boundaries

- Owns: `jiuwenswarm.server.app_agentserver`, `AgentWebSocketServer`, server runtime helpers, gateway push wire encoding, session command handlers, runtime command handlers, scheduler entrypoints, sandbox command boundary.
- Does not own: channel-specific ingestion, frontend rendering, most agent/tool implementation internals, and external protocol semantics before Gateway normalization.

## Entry Points

- `jiuwenswarm/server/app_agentserver.py`: standalone process startup, extension loading, WebSocket server startup, proactive engine, teammate bootstrap daemon, shutdown.
- `jiuwenswarm/server/agent_ws_server.py`: WebSocket server, E2A/legacy request parsing, method dispatch, session/command handlers, stream/cancel logic, server push.
- `jiuwenswarm/server/gateway_push/wire.py`: converts server-originated push messages into E2A response wire frames.
- `jiuwenswarm/server/runtime/agent_manager.py`: creates, initializes, reloads, and retrieves agent instances.
- `jiuwenswarm/server/runtime/proactive_adapter.py`: attaches proactive recommendation engine to the AgentServer instance.

## Related Flows

- `gateway-agentserver-e2a-chat`: Gateway request -> AgentServer dispatch -> agent response.
- `agentserver-session-lifecycle`: session metadata/history/checkpointer/runtime state.
- `agentserver-server-push`: agent-originated downstream push events.

## Related Code Symbols

- `_run`: startup lifecycle for the standalone process.
- `AgentWebSocketServer._handle_message`: central request parser and dispatcher.
- `AgentWebSocketServer._handle_stream`: stream response producer with heartbeat and session task tracking.
- `AgentWebSocketServer._handle_unary`: unary response path.
- `AgentWebSocketServer._handle_cancel`: interrupt/cancel path.
- `AgentWebSocketServer.send_push`: server-originated downstream events.

## Verification Evidence

- `tests/unit_tests/test_app_agentserver.py` checks startup/shutdown does not delete agent team directories.
- `tests/unit_tests/agentserver/test_agentserver_modes.py` covers mode resolution, project directory resolution, and stream/mode behavior.
- `tests/unit_tests/agentserver/test_agentserver_acp.py` covers ACP initialization, sessions, team delete, capabilities, and tool response paths.
- `tests/unit_tests/agentserver/test_agentserver_cli_commands.py` covers slash-command handlers.
- `tests/unit_tests/agentserver/test_agent_ws_connection_close.py` covers disconnect cleanup behavior.

## Known Gaps

- No full live WebSocket integration evidence found yet for real `websockets.serve`, origin rejection, concurrent inbound frames, ack timing, heartbeat, and disconnect cleanup together.
- `send_push` tracks one current Gateway WebSocket; multiple Gateway connections need explicit ownership rules.
- Broad runtime mutation handlers need per-symbol audit: MCP config, sandbox config/process, model env mutation, agent reload, scheduler actions.
