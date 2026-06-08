# Progressive Tool Visibility: Show Only Needed Tools to the Model

When JiuwenClaw integrates many built-in tools, skill tools, or MCP tools, sending the complete tool schema on every model call creates two problems: increased context token usage and higher likelihood of the model selecting the wrong tool from a large pool. **Progressive tool visibility** compresses the model-side `tools` list: always-visible tools are directly accessible, while other registered tools are accessed on-demand via `tools_search` + `invoke_tool`.

This capability only changes "which tool schemas are sent to the model" — it does not affect tool registration, permission validation, or actual execution pipelines. MCP servers still register and connect through the existing flow.

## How It Works

When enabled, the system divides tools into two categories:

| Type | Description |
| :--- | :--- |
| **Always Visible Tools** | Listed in `eager_tools`, full schema included in every model call, can be invoked directly |
| **On-Demand Visible Tools** | Registered at runtime but not included in the model's `tools` list; summarized in the "On-Demand Tool Navigation" section of the system prompt |

The model-side `tools` schema is **fixed to** `eager_tools` (beneficial for LLM prefix caching). When using on-demand tools:

1. Call `tools_search` with the `tool_name` matching the navigation list to get the complete `input_schema`
2. Call `invoke_tool` with the exact `tool_name` and `arguments`

There's no need to load on-demand tools into the next round's schema.

## Configuration

Configuration is located at `react.tool_lazy_load` in `config.yaml` (consistent with the default example in `config.yaml`):

```yaml
react:
  tool_lazy_load:
    enabled: true
    enable_for_models:
      - glm
    eager_tools:
      - tools_search
      - invoke_tool
      - bash
      - read_file
      # ...
    subagents:
      enabled: true
      inherit_parent_eager_tools: false
      eager_tools:
        - tools_search
        - invoke_tool
```

### Main Switch

| Configuration | Description |
| :--- | :--- |
| `enabled` | Whether to enable progressive tool visibility |

### Model whitelist

| Configuration | Description |
| :--- | :--- |
| `enable_for_models` | Substring whitelist on `model_name` (case-insensitive). **Empty list** = progressive visibility for all models. **Non-empty** = only matched models get filtered schema; others see the full tools list |

Log `[ProgressiveToolRail] lazy load bypassed for model=...` means the current model did not match the whitelist and schema filtering was skipped.

When `enabled` is false, model calls see the complete tool list.

### Always Visible Tools

`eager_tools` declares the tools exposed to the model every round. The system automatically adds `tools_search` and `invoke_tool`.

| Tool Type | Recommendation |
| :--- | :--- |
| Tool Discovery & Invocation | `tools_search`, `invoke_tool` (auto-added) |
| User Interaction | `ask_user_question` |
| File & Command | Common file read/write, search, shell tools |
| Sub-Agent | `spawn_subagent`, `fork_agent` (when main Agent needs delegation) |

On-demand visible tools = all registered tools − `eager_tools`, no separate configuration list needed.

## Sub-Agent Behavior

`spawn_subagent` / `fork_agent` can be enabled separately via `subagents.enabled`. Sub-agents by default exclude `spawn_subagent` and `fork_agent` from `eager_tools` (to avoid nested delegation). Each sub-agent uses `agent_id=task_id` to register an independent meta tool instance.

## Troubleshooting

Key log prefix: `[ProgressiveToolRail]`

| Log Fragment | Meaning |
| :--- | :--- |
| `enabled profile=...` | Main Agent or sub-Agent has mounted the rail |
| `invoke total=... eager=... deferred=...` | Registered / always-visible / on-demand tool counts |
| `filter tools X -> Y` | Schema filtering before sending to model |
| `lazy load bypassed for model=...` | Current model not in `enable_for_models`; schema not filtered |
| `search tool_name=... matches=...` | `tools_search` exact lookup by registered name |

## FAQ

### Why does the model call `tools_search` / `invoke_tool` first?

The target tool is not in the current `tools` schema and must be invoked indirectly through meta tools.

### Does this affect tool permissions?

No. It only controls the model-side schema and navigation prompts, and does not bypass permission rails or disabled tools.

### What's the difference from the legacy `search_and_load_tools` approach?

The current implementation uses **fixed schema + meta tool indirect invocation**, and no longer dynamically adds on-demand tools to the next round's `tools` list. The configuration key remains `tool_lazy_load`, and the implementation class is `JiuWenProgressiveToolRail`.