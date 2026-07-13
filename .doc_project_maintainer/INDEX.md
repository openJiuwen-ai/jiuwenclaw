# Project Maintainer Index

Status: partial. First build is focused on AgentServer.

## Project

- [Overview](project/overview.md): product and runtime map.
- [Architecture](project/architecture.md): process and boundary sketch.
- [Build Plan](project/build-plan.md): coverage ledger, pending slices, and next work.
- [Open Questions](project/open-questions.md): unresolved evidence gaps.

## Modules

- [AgentServer Runtime](modules/agentserver-runtime/README.md): standalone agent server, WebSocket RPC dispatch, sessions, commands, server push, scheduler, sandbox, ACP.
- [Gateway And Channels](modules/gateway-and-channels/README.md): Gateway clients and channel surfaces that talk to AgentServer.
- [Agent Harness](modules/agent-harness/README.md): agent adapters, rails, team orchestration, memory, skills, and tools.
- [Project Packaging](modules/project-packaging/README.md): package metadata, launch scripts, deploy and installer assets.

## Directories

- [jiuwenswarm/server](directories/jiuwenswarm__server/README.md): AgentServer entrypoint, WebSocket server, runtime services, sandbox, hooks, utilities.
- [jiuwenswarm/gateway/routing](directories/jiuwenswarm__gateway__routing/README.md): AgentServer client and routing support.
- [tests/unit_tests/agentserver](directories/tests__unit_tests__agentserver/README.md): main AgentServer behavior test suite.

## Flows

- [Gateway AgentServer E2A Chat](project/flows/gateway-agentserver-e2a-chat.md)
- [AgentServer Session Lifecycle](project/flows/agentserver-session-lifecycle.md)
- [AgentServer Server Push](project/flows/agentserver-server-push.md)

## Priority Code Symbols

- app entrypoint: [file](code/jiuwenswarm/server/app_agentserver.py/app_agentserver.py.md), [_run](code/jiuwenswarm/server/app_agentserver.py/_run.md), [main](code/jiuwenswarm/server/app_agentserver.py/main.md)
- websocket server: [file](code/jiuwenswarm/server/agent_ws_server.py/agent_ws_server.py.md), [class](code/jiuwenswarm/server/agent_ws_server.py/Class%20AgentWebSocketServer/Class%20AgentWebSocketServer.md)
- audited methods: see [AgentServer method queue](audit-queues/server-method-audit-queue.json) and `code/jiuwenswarm/server/agent_ws_server.py/Class AgentWebSocketServer/`.
