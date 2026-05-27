# Progressive tool visibility

When JiuwenClaw registers many built-in tools, skill tools, or MCP tools, sending the full tool schema on every model call increases context token usage and makes wrong tool selection more likely. **Progressive tool visibility** compresses the model-side `tools` list: frequently used tools stay visible; others are discovered and loaded on demand via `search_and_load_tools`.

This feature only changes **which tool schemas are sent to the model**. It does not change tool registration, permission checks, or real execution paths. This version also does **not** defer MCP server connections—MCP servers still register and connect through the existing flow.

## How it works

After enabling, tools fall into two categories:

| Type | Description |
| :--- | :--- |
| **Always visible** | Listed in `eager_tools`; full schema is sent on every model call. Use for files, shell, sub-agents, skills, user prompts, etc. |
| **On-demand visible** | Registered at runtime but hidden from the model `tools` list until loaded via `search_and_load_tools` |

When the model needs a capability that is not currently visible, it calls `search_and_load_tools`. That meta tool searches registered tools, adds matches to the current session’s visible set, and on the **next** model step those tools appear in `tools` so the model can call them directly.

## Configuration

Settings live under `react.tool_lazy_load` in `config.yaml`:

```yaml
react:
  tool_lazy_load:
    enabled: true
    search_max_results: 5
    default_load_limit: 3
    max_loaded_tools: 1024
    defer_mcp: false
    defer_builtin: false
    eager_tools:
      - search_and_load_tools
      - ask_user_question
      - spawn_subagent
      - fork_agent
      - read_file
      - write_file
      - edit_file
      - grep
      - glob
      - bash
    subagents:
      enabled: true
      inherit_parent_eager_tools: false
      eager_tools:
        - search_and_load_tools
```

### Main switch

| Setting | Description |
| :--- | :--- |
| `enabled` | Turn progressive visibility on (`true`) or off (`false`) |
| `JIUWENCLAW_TOOL_LAZY_LOAD` | Environment override; `true` / `false` |

When disabled, behavior reverts to exposing the full tool list to the model.

### Search and load limits

| Setting | Description |
| :--- | :--- |
| `search_max_results` | Maximum candidates returned by one `search_and_load_tools` search |
| `default_load_limit` | Default Top N tools to load when the model omits `limit` |
| `max_loaded_tools` | Maximum on-demand visible tools kept in the current session |

If the visible set exceeds `max_loaded_tools` after loading, earlier entries are kept and overflow names go to `skipped_tools`; they will not become callable on the next model step. Tools in `eager_tools` remain visible regardless.

### Always-visible tools (`eager_tools`)

Use `eager_tools` for tools that should be fully exposed on the first turn. Recommended minimum:

| Category | Recommendation |
| :--- | :--- |
| Discovery | Keep `search_and_load_tools`—without it the model cannot load hidden tools |
| User interaction | `ask_user_question` |
| Files & shell | Common read/write/search/shell tools |
| Sub-agents | `spawn_subagent`, `fork_agent` if the main agent should delegate |
| Skills | `skill_tool`, `skill_complete` if skills are core to your workflow |

If `search_and_load_tools` is missing from `eager_tools`, the runtime adds it automatically.

## Sub-agents

`spawn_subagent` and `fork_agent` child agents can use progressive visibility:

```yaml
subagents:
  enabled: true
  inherit_parent_eager_tools: false
  eager_tools:
    - search_and_load_tools
```

| Setting | Description |
| :--- | :--- |
| `subagents.enabled` | Enable progressive visibility for spawn/fork sub-agents |
| `subagents.inherit_parent_eager_tools` | Reuse the main agent’s `eager_tools` |
| `subagents.eager_tools` | Sub-agent-specific always-visible tools |

The default is conservative: only `search_and_load_tools` for sub-agents, so inherited tools do not flood the child’s schema. Each sub-agent task uses its own session; loaded-tool state does not leak to the parent or other sub-agents.

## Difference from MCP lazy loading

This release only compresses **model-side** tool visibility:

| Capability | This release |
| :--- | :--- |
| Smaller `tools` schema in LLM requests | Yes |
| Less tool-selection noise | Yes |
| Skip MCP connection at startup | No |
| MCP catalog-only index | No |
| Connect MCP on first tool hit | No |

Startup time dominated by MCP connections is unchanged; the main win is fewer tool-schema tokens per model call and clearer tool choice.

## Troubleshooting

Look for log lines prefixed with `[ProgressiveTool]`:

| Log fragment | Meaning |
| :--- | :--- |
| `enabled profile=...` | Progressive rail mounted for main or sub-agent |
| `invoke registered=... visible=... always=...` | Registered tool count, session visible count, always-visible (`eager_tools`) count |
| `filter tools X -> Y` | Tools filtered before the model call (`X` → `Y`) |
| `search_and_load query=... matched=... loaded=... skipped=...` | Result of `search_and_load_tools` (search, load, cap overflow) |

- **`registered`**: tools known to the agent this turn.
- **`visible`**: names in session state (`__progressive_visible_tool_names__`), including loaded on-demand tools.
- **`always`**: size of configured `eager_tools`; these stay callable even if not listed in session `visible`.

For full messages and tools sent to the model, enable LLM IO trace at debug level. Day-to-day checks usually start with `[ProgressiveTool]` logs.

## FAQ

### Why does the model call `search_and_load_tools` first?

The target tool is not in the current `tools` list. The model must search and load it; the next turn can call the real tool. This reduces first-turn schema size.

### Why was a tool found but not callable on the next turn?

Check `skipped_tools` in the tool result. Names over `max_loaded_tools` are skipped and will not appear in the next `tools` list.

### Does this bypass tool permissions?

No. It only controls schema visibility. Permission rails, `disabled_tools`, and execution behavior are unchanged.

### Are sessions isolated?

Yes. Loaded-tool state lives in per-session state. User sessions, main agent, and sub-agent tasks do not share visible-tool lists.
