# Agent

`workspace` is JiuwenSwarm’s runtime directory for agent memory, skills, session data, and configurable heartbeat tasks. On first run (via `init` or `app`), the template under `jiuwenswarm/resources/agent/workspace/` is copied to `~/.jiuwenswarm/agent/workspace/`. Built-in skills live in the package at `jiuwenswarm/resources/agent/workspace/skills/`; marketplace/installed skills are stored in the user workspace.

![Workspace](../assets/images/agent.png)

## Layout overview

The **installed** runtime layout (`~/.jiuwenswarm/`):

```
~/.jiuwenswarm/
├── .updates/             # Update state (generated)
│   └── web_process.json
├── agent/
│   ├── .checkpoint/       # Checkpoint database (created at runtime)
│   │   ├── checkpoint.db       # SQLite checkpoint
│   │   ├── checkpoint.db-shm   # SQLite shared memory
│   │   └── checkpoint.db-wal   # SQLite WAL log
│   ├── .logs/             # Agent process logs
│   │   ├── agent_server.log
│   │   ├── channel.log
│   │   ├── full.log
│   │   ├── gateway.log
│   │   ├── permissions.log
│   │   └── ws-dev.log
│   ├── sessions/          # Sessions (generated)
│   │   ├── sess_<id>/     # Normal sessions
│   │   │   ├── history.json  # Session history
│   │   │   ├── metadata.json # Session metadata
│   │   │   └── *.md, *.json
│   │   └── heartbeat_<id>/   # Heartbeat sessions
│   └── workspace/         # Agent workspace
│       ├── agents/            # Agent work area (DeepAgent standard node)
│       │   └── .workspace
│       ├── coding_memory/     # Coding memory
│       │   ├── .workspace
│       │   └── MEMORY.md
│       ├── context/           # Session context
│       │   ├── .workspace
│       │   └── session_memory.md
│       ├── memory/            # Memory system
│       │   ├── .workspace
│       │   ├── MEMORY.md      # Long-term memory (from MEMORY_ZH.md or MEMORY_EN.md at init)
│       │   ├── daily_memory/  # Daily memory files (created at runtime)
│       │   │   ├── .workspace
│       │   │   ├── YYYY-MM-DD.md
│       │   │   └── ...
│       │   ├── memory.db      # ChromaDB vector store (created at runtime)
│       │   ├── memory.db-shm
│       │   └── memory.db-wal
│       ├── messages/          # Message work area (DeepAgent standard node)
│       │   └── .workspace
│       ├── skills/            # Marketplace/installed skills
│       │   ├── .workspace
│       │   ├── skills_state.json # Skill install/market state (generated)
│       │   ├── _marketplace/     # Marketplace clone cache (created at runtime)
│       │   └── <skill-name>/
│       │       └── evolutions.json
│       ├── todo/              # Todo work area (DeepAgent standard node)
│       │   └── .workspace
│       ├── extensions/        # Extension plugins (created at runtime)
│       ├── interactions/      # Interaction contexts (created at runtime)
│       ├── agent-data.json    # Agent list metadata
│       ├── AGENT.md           # Agent identity
│       ├── HEARTBEAT.md       # Heartbeat tasks
│       ├── IDENTITY.md        # Identity
│       ├── SOUL.md            # Persona for system prompts
│       └── USER.md            # User profile
├── auto-harness/         # Auto CI workflow (generated)
│   └── config.yaml
├── config/               # Configuration
│   ├── config.yaml       # Main config
│   ├── .env              # Environment variables
│   ├── builtin_rules.yaml # Shell security rules
│   └── runtime_state.yaml # Runtime state (generated)
└── logs/                 # Business logs
    └── logs/
        ├── llm.log           # LLM call logs
        ├── memory.log        # Memory module logs
        ├── runner.log        # Runner logs
        ├── session.log       # Session logs
        ├── sys_operation.log # System operation logs
        ├── interface/
        │   ├── jiuwen_interface.log
        │   └── jiuwen_prompt_builder_interface.log
        ├── performance/
        │   └── jiuwen_performance.log
        └── run/
            └── jiuwen.log
```

Built-in skills template lives at `jiuwenswarm/resources/agent/workspace/skills/`. These built-in skills are not copied to the user workspace on init (loaded directly from the package in source mode).

---

## Pre-configured content

Shipped with the package or source; you can use or edit as needed:

| Path (in workspace) | Description |
|------|-------------|
| `AGENT.md` | Agent identity description. Copied from `AGENT_ZH.md`/`AGENT_EN.md` by language at init. |
| `IDENTITY.md` | Identity description. Copied from `IDENTITY_ZH.md`/`IDENTITY_EN.md` by language at init. |
| `SOUL.md` | Persona for system prompts. Copied from `SOUL_ZH.md`/`SOUL_EN.md` by language at init. |
| `USER.md` | User profile. Copied from template at init. |
| `HEARTBEAT.md` | Heartbeat template (`jiuwenswarm/resources/agent/workspace/HEARTBEAT_ZH.md` or `HEARTBEAT_EN.md`, copied by language at init). If present and valid, the agent reads it on each heartbeat; otherwise only `HEARTBEAT_OK` is returned. Editable in the web UI. |
| `skills/` | Marketplace/installed skills. Each skill has `SKILL.md`, `prompts/`, `references/`, etc. |
| `skills/_marketplace/` | Marketplace clone cache; empty by default. |
| `memory/` | Memory layout. `MEMORY.md` is copied from template at init. |

---

## Dynamically generated content

Created or updated at runtime:

| Path | Description |
|------|-------------|
| `agent/.checkpoint/checkpoint.db` | SQLite checkpoint database (.db-shm, .db-wal). |
| `agent/.logs/*.log` | Agent process logs (gateway, agent_server, channel, full, permissions, ws-dev). |
| `agent/sessions/<session_id>/` | One folder per session, containing `history.json`, `metadata.json`, and session-generated `*.md` / `*.json` files. |
| `agent/sessions/<session_id>/todo.md` | Todo list created by `TodoToolkit` (optional). |
| `agent/workspace/agent-data.json` | Generated by `scripts/generate-agent-folders.js` for the web UI. |
| `agent/workspace/coding_memory/MEMORY.md` | Coding memory content. |
| `agent/workspace/context/session_memory.md` | Session context memory. |
| `agent/workspace/memory/daily_memory/YYYY-MM-DD.md` | Daily memory files created by `write_memory` tool. |
| `agent/workspace/memory/memory.db` | ChromaDB vector store for `memory_search` (with `.db-shm`, `.db-wal`). |
| `agent/workspace/skills/skills_state.json` | Maintained by `SkillManager` for installed marketplace plugins. |
| `agent/workspace/skills/<skill>/evolutions.json` | Skill evolution records. |
| `agent/workspace/skills/_marketplace/` | Marketplace clone cache (created by `_sync_marketplace_repos`). |
| `agent/workspace/extensions/` | Extension plugins directory (created at runtime). |
| `agent/workspace/interactions/` | Interaction contexts (created at runtime). |
| `.updates/web_process.json` | Web process update state. |
| `auto-harness/config.yaml` | Auto CI workflow configuration. |
| `config/runtime_state.yaml` | Runtime state. |
| `logs/logs/*.log` | Business logs (llm, memory, runner, session, sys_operation). |
| `logs/logs/interface/jiuwen_interface.log` | Interface module logs. |
| `logs/logs/interface/jiuwen_prompt_builder_interface.log` | Prompt builder interface logs. |
| `logs/logs/performance/jiuwen_performance.log` | Performance logs. |
| `logs/logs/run/jiuwen.log` | Runner logs. |

---

## Related configuration

- **Skill root**: `skill_base_dir` in `config/config.yaml`, default `agent/skills`.
- **Agent workspace**: `~/.jiuwenswarm/agent/workspace/` (`get_agent_workspace_dir()`).
- **Sessions**: `~/.jiuwenswarm/agent/sessions/`, one subfolder per `session_id`.
- **SkillNet usage in Swarm**: see [Skills.md §5](Skills.md#5-how-skills-installed-via-skillnet-are-used-in-swarm).
