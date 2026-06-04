# User Guide

This page collects common JiuwenSwarm usage instructions and feature documentation.

## Installation

| Feature | Documentation | Description |
|---|---|---|
| **Installation** | [Install Guide](InstallGuide.md)<br>[TUI Mode Install Guide](Quickstart_tui.md) | Installation documentation: basic installation flow, environment preparation, and startup methods.<br>TUI quick start: TUI mode installation, startup, and runtime configuration. |

## Basic Usage

| Feature | Documentation | Description |
|---|---|---|
| **Quick Start** | [Quick Start](Quickstart.md) | Beginner-friendly startup configuration, basic conversation flow, and common operations. |
| **Page Overview** | [Page Overview](Page-Overview.md) | Web UI layout, core areas, and feature entry points. |
| **Conversation** |   | Web conversation entry point, supporting message sending, new sessions, and planning / performance / cluster mode switching. |
| **Agent** | [Agent](Agent.md) | Agents with different roles, workspace creation, and workspace management flows. |
| **Session** |  | Session information management, viewing and restoring historical chats, and deleting session history. |
| **Heartbeat** | [Heartbeat](Heartbeat.md) | Background service keepalive, runtime status checks, and monitoring mechanisms. |
| **Scheduled Tasks** | [Scheduled Tasks](ScheduledTasks.md) | Configuration, execution, and management of scheduled tasks. |
| **Skills** | [Skills](Skills.md) | Agent skill mounting, invocation, and extension mechanisms. |
| **Channels** | [Channels](Channels.md)<br>[ACP Plugin Usage](ACP_Client_Config.md)<br>[TUI User Guide](CLI.md) | Channels: domestic and international channel integration and message flow configuration.<br>ACP Plugin Usage: ACP plugin configuration and integration for coding scenarios.<br>TUI User Guide: terminal interaction entry point, common operations, and runtime instructions. |
| **Configuration** | [Configuration](Configuration.md) | System parameters, LLM APIs, and runtime environment configuration. |
| **Browser Service** | [Browser](Browser.md) | Web access, information retrieval, and browser tool invocation capabilities. |
| **Logs** |   | System log paths, runtime records, and common troubleshooting entry points. |
| **MCP Service Settings** | [MCP Configuration](MCPConfiguration.md) | External tool integration and Model Context Protocol configuration. |

## Advanced Operations

| Feature | Documentation | Description |
|---|---|---|
| **Context Compression** | [Context Compression and Offload](ContextCompression.md) | Long-context handling, conversation compression, and context offload mechanisms. |
| **Skill Self-Evolution** | [Skill Self-Evolution](SkillSelfEvolution.md) | Skill iteration, self-optimization, and capability accumulation mechanisms. |
| **Tool Permissions and Security** | [Tool Permissions and Security](ToolPermissionsSecurity.md) | Security interception and permission control for system commands, file operations, and tool calls. |
| **E2A** | [E2A Protocol](E2A-protocol.md) | Unified request envelope protocol between Gateway and AgentServer. |
| **A2A** | [A2A](A2A.md) | Agent-to-Agent communication protocol and integration flow. |
| **Agent Team** | [Agent Teams](AgentTeam.md)<br>Team Skills<br>[Distributed Team](DistributedTeam.md) | Agent Teams: multi-agent collaboration, team organization, and task division mechanisms.<br>Team Skills: team-level skill orchestration, reuse, and sharing capabilities.<br>Distributed Team: multi-process distributed team mode. |
| **Memory** | [Memory](Memory.md)<br>[Coding Memory](CodingMemory.md)<br>[Task Memory](TaskMemory.md) | Memory: short-term and long-term memory management, retrieval, and reuse mechanisms.<br>Coding Memory: code-mode-specific coding memory accumulation.<br>Task Memory: task experience accumulation, retrieval, and reuse mechanisms. |
| **TUI Mode** | [Slash Command Architecture](SlashCommandArchitecture.md)<br>[Slash Command Reference](SlashCommands.md)<br>[Mode System](Modes.md)<br> | Slash Command Architecture: terminal slash command system design and extension structure.<br>Slash Command Reference: common slash commands, parameters, and usage scenarios.<br>Mode System: PLAN / AGENT / CODE / TEAM mode switching and configuration. |

## Appendix

| Category | Documentation | Description |
|---|---|---|
| **Package EXE** | [Package EXE Guide](PackExeGuide.md) | Windows standalone executable packaging flow. |
| **Auto Update** | [Windows Auto-Update Design](WindowsAutoUpdateDesign.md) | Windows client auto-update design, flow, and key modules. |
| **Developer Documentation** | [Developer Guide](developer_guide.md) | Source setup, debugging flow, and secondary development materials. |

## Development Practices

| Practice | Documentation | Description |
|---|---|---|
| **Code Review Assistant** | [Code Review Assistant](development-practices/JiuwenSwarm-Code-Review-Assistant.md) | Building a code review workflow. |
| **Daily Report Generator** | [Daily Report Generator](development-practices/JiuwenSwarm-Daily-Report-Generator.md) | Agent development case for automatically summarizing daily work reports. |
