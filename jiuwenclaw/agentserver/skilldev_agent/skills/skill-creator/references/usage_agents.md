# AGENT Definition Usage

Translate an agentDefinition into two things in the new skill:

1. **Call syntax** — an `invoke(funcName:"agent_as_a_tool", params:{...})` call.
2. **Tool-definitions block** — a `### Function: agent_as_a_tool` entry in the skill body's single **tool definitions** section.

Both are generated from the same source JSON below.

## Source & metadata

Read agent definitions from `<workspace>/resources/agents/available_agents.json`.

Declare every agent the skill actually calls in `SKILL.md` frontmatter (no empty placeholders; `agentId` must match the source definition exactly):

```yaml
metadata:
  agents:
    - agentId: "aaabbbccc"
```

## Example definition

This single definition drives both outputs below.

```json
{
  "agentId": "aaabbbccc",
  "name": "travelAgent",
  "description": "查询出行相关资讯与方案",
  "parameters": {
    "type": "object",
    "properties": {
      "query": {
        "type": "string",
        "description": "用户查询问题内容"
      },
      "filesInfo": {
        "type": "Array<Object>",
        "description": "附带的文件资料信息"
      }
    },
    "required": ["query"]
  }
}
```

## Output 1 — Call syntax

`funcName` is always the fixed string `agent_as_a_tool` (never the agent `name` or `agentId`). `params` carries the call inputs:

- `params.agentId` comes from `agentDefinition.agentId`.
- `params.query` is the task query to send to the agent — make it self-contained with the user's task, constraints, and expected output.
- Build the remaining params from the `parameters` JSON Schema; include every field in `parameters.required`.
- Optional fields, including `filesInfo`, are omitted unless the task needs them. When forwarding files, build `filesInfo` according to `parameters.properties.filesInfo` — do not assume a fixed shape beyond the schema.

From the example:

```text
invoke(funcName:"agent_as_a_tool", params:{agentId:"aaabbbccc", query:"查询北京三日游玩攻略"})
```

With files (only when required or task-relevant):

```text
invoke(funcName:"agent_as_a_tool", params:{agentId:"aaabbbccc", query:"分析这份行程文件", filesInfo:<value matching parameters.properties.filesInfo>})
```

## Output 2 — Tool-definitions block

Mapping:

| JSON field | Markdown field |
|------------|----------------|
| (固定) | `### Function: agent_as_a_tool` 标题（恒为此字符串，不用 `name`/`agentId`） |
| `agentId` | `- **toolName**: <agentId>`（运行时按它路由） |
| `description` | `- **description**` |
| `parameters` | `- **参数**: <parameters JSON 压缩平铺成一行>` |
| (n/a) | `- **约束**`：仅在有顺序或前置条件时写 |
| (n/a) | `- **语义**`：仅在触发措辞会路由到不同 agent 时写 |

- `- **参数**` 直接把 `parameters` JSON 体压缩平铺成一行，不要改写成自定义参数说明格式。`parameters` 为空对象时写 `{}`。

From the example:

```markdown
### Function: agent_as_a_tool
- **toolName**: aaabbbccc
- **description**: 查询出行相关资讯与方案
- **参数**: {"type":"object","properties":{"query":{"type":"string","description":"用户查询问题内容"},"filesInfo":{"type":"Array<Object>","description":"附带的文件资料信息"}},"required":["query"]}
```

## Field & safety reference

| Field | How to use it | Hard rule |
| --- | --- | --- |
| `agentId` | Into `invoke.params.agentId` and the block's `- **toolName**`. | Copy exactly; never use `name` as the call identifier. |
| `name` | Human-readable agent name only. | Never use as the call identifier. |
| `description` | Decide when delegation is appropriate. | — |
| `parameters` | JSON Schema for building `query`, `filesInfo`, and the `- **参数**` line. | Include all `required` fields. |
| `parameters.properties.query` | User task content → `invoke.params.query`. | Make it self-contained. |
| `parameters.properties.filesInfo` | File metadata → `invoke.params.filesInfo`. | Include only when required or task-relevant; follow this schema, don't assume a fixed shape. |

`funcName` is always `agent_as_a_tool` — never replace it with the agent `name` or `agentId`. Ask the user when a required value is missing and cannot be safely inferred.