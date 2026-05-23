# AGENT Definition Usage

Translate agentDefinition entries from `<workspace>/resources/agents/available_agents.json` into `agent_as_a_tool(agentId="", query="", filesInfo=[])` calls in the new skill.

## Metadata Note

If the skill uses agent dependencies, declare them in `SKILL.md` frontmatter so packaging can copy the source definitions:

```yaml
metadata:
  agents:
    - agentId: "aaabbbccc"
```

Only declare agents the skill actually calls. Do not add empty placeholders. `agentId` must match the source definition exactly.

## Input shape

`agent_as_a_tool` input is a JSON object with exactly these fields:

```json
{
  "agentId": "aaabbbccc",
  "query": "查询北京三日游玩攻略",
  "filesInfo": []
}
```

`filesInfo` is an array of file metadata objects. Use `[]` when no files need to be forwarded.

Build `query` and `filesInfo` from the `agentDefinition.parameters` JSON Schema.

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
| `agentId` | Yes | Copy exactly into `agent_as_a_tool.agentId`. |
| `name` | No | Human-readable agent name; do not use as the call identifier. |
| `description` | Yes | Use to decide when delegation is appropriate. |
| `parameters` | Yes | JSON Schema used to construct `query`, `filesInfo`, and any required call inputs. |
| `parameters.properties.query` | Usually | User task content to pass as `agent_as_a_tool.query`. |
| `parameters.properties.filesInfo` | Usually | File metadata to pass as `agent_as_a_tool.filesInfo`; use `[]` when no files are needed. |
| `parameters.required` | Yes | Required fields that must be present before calling. |

## Safety

- Copy `agentId` exactly; never use `name` as the call identifier.
- Make `query` self-contained with the user's task, constraints, and expected output.
- Pass `filesInfo=[]` when no task-relevant files need to be forwarded.
- Include only task-relevant file metadata in `filesInfo`.
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
Call the agent_as_a_tool tool to execute:
```
    agent_as_a_tool(agentId="aaabbbccc", query="查询北京三日游玩攻略", filesInfo=[])
```

## Generating the tool-definitions entry

Agent tools sit in the skill body's single **tool definitions** section. Each agent becomes a `### Function: <name>（平台注册）` sub-block.

Mapping rules:

| JSON field | Markdown field |
|------------|----------------|
| `name` (或缺失时用 `agentId`) | `### Function: <name>（平台注册）` heading |
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
### Function: travelAgent（平台注册）
- **toolName**: aaabbbccc
- **description**: 查询出行相关资讯与方案
- **参数**: （由平台自动注入）