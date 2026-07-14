---
id: agent-harness
name: Agent Harness
confidence: inferred
last_updated: 2026-07-07
read_when: "Working on agent adapters, rails, tools, team runtime, skills, memory, or AutoHarness execution."
---

# Agent Harness

## Responsibility

Provides the agent execution machinery behind AgentServer: adapters, rails, memory, tools, skill management, code mode, team orchestration, distributed members, and AutoHarness scheduler/package workflows.

## Boundaries

- Owns: adapter behavior after AgentServer dispatch, in-agent state, team manager behavior, skill and memory runtime, tool rails.
- Does not own: WebSocket protocol parsing or Gateway channel output.

## Current Evidence

AgentServer calls this module family through `AgentManager`, adapter methods such as `process_message` and `process_message_stream`, team manager helpers, scheduler services, ACP output callbacks, and sandbox runtime patches.

## Pending

This module has not been fully analyzed. Next high-value slices are `server/runtime/agent_adapter/interface_deep.py`, `server/runtime/skill/skill_manager.py`, and `agents/harness/team`.
