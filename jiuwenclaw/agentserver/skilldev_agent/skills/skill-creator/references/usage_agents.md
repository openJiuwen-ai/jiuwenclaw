# AGENT Definition Usage

Translate agentDefinition entries from `<workspace>/resources/agents/available_agents.json` into `invoke(funcName:"agent_as_a_tool", params:{...})` calls in the new skill.

## Metadata Note

If the skill uses agent dependencies, declare them in `SKILL.md` frontmatter so packaging can copy the source definitions:

```yaml
metadata:
  agents:
    - agentId: "aaabbbccc"
```

Only declare agents the skill actually calls. Do not add empty placeholders. `agentId` must match the source definition exactly.

## Input shape

`invoke` always uses the fixed function name `agent_as_a_tool`. Put agent call inputs in `params`:

```text
invoke(funcName:"agent_as_a_tool", params:{agentId:"aaabbbccc", query:"查询北京三日游玩攻略"})
```

`params.agentId` comes from `agentDefinition.agentId`. `params.query` is the task query to send to the agent. `params.filesInfo` is built according to `agentDefinition.parameters.properties.filesInfo`.

Build all params from the `agentDefinition.parameters` JSON Schema. Include fields listed in `agentDefinition.parameters.required`. Optional fields, including `filesInfo`, should be omitted unless the task needs them.

## agentDefinition fields

Read the source agent definitions from `<workspace>/resources/agents/available_agents.json`:

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
    "required": [
      "query"
    ]
  }
}
```

Field notes:

| Field | Required | How to use it |
| --- | --- | --- |
| `agentId` | Yes | Copy exactly into `invoke.params.agentId`. |
| `name` | No | Human-readable agent name; do not use as the call identifier. |
| `description` | Yes | Use to decide when delegation is appropriate. |
| `parameters` | Yes | JSON Schema used to construct `query`, `filesInfo`, and any required call inputs. |
| `parameters.properties.query` | Usually | User task content to pass as `invoke.params.query`. |
| `parameters.properties.filesInfo` | Optional unless required | File metadata to pass as `invoke.params.filesInfo`; follow this schema when files must be forwarded. |
| `parameters.required` | Yes | Required fields that must be present before calling. |

## Safety

- Copy `agentId` exactly; never use `name` as the call identifier.
- Use `funcName:"agent_as_a_tool"` exactly; do not replace it with the agent name or agentId.
- Make `query` self-contained with the user's task, constraints, and expected output.
- Include `filesInfo` only when it is required by `agentDefinition.parameters.required` or when task-relevant files need to be forwarded.
- When including `filesInfo`, construct it according to `agentDefinition.parameters.properties.filesInfo`; do not assume a fixed shape beyond the schema.
- Include all required fields from `agentDefinition.parameters.required`.
- Ask the user when a required value is missing and cannot be safely inferred.

## Example

agentDefinition:

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
    "required": [
      "query"
    ]
  }
}
```

Generated:
Call the platform tool to execute:
```
    invoke(funcName:"agent_as_a_tool", params:{agentId:"aaabbbccc", query:"查询北京三日游玩攻略"})
```

If `filesInfo` is required or files must be forwarded, include it using the schema from `parameters.properties.filesInfo`, for example:

```text
invoke(funcName:"agent_as_a_tool", params:{agentId:"aaabbbccc", query:"分析这份行程文件", filesInfo:<value matching parameters.properties.filesInfo>})
```

## Generating the tool-definitions entry

Agent tools sit in the skill body's single **tool definitions** section. Each agent becomes a `### Function: <name>` sub-block.

Mapping rules:

| JSON field | Markdown field |
|------------|----------------|
| `name` (或缺失时用 `agentId`) | `### Function: <name>` heading |
| `agentId` | `- **toolName**: <agentId>` |
| `description` | `- **description**` |
| `parameters` | `- **参数**: （由平台自动注入）` |
| (n/a) | `- **约束**` |
| (n/a) | `- **语义**` |

- `- **toolName**` 必须填 `agentId`，运行时按它路由。
- `- **参数**` 不要内联 JSON schema。
- `- **约束**` 仅在有顺序或前置条件时写。
- `- **语义**` 仅在触发措辞会路由到不同 agent 时写。

Example — given the `travelAgent` definition above, generate:

```markdown
### Function: travelAgent
- **toolName**: aaabbbccc
- **description**: 查询出行相关资讯与方案
- **参数**: （由平台自动注入）
```