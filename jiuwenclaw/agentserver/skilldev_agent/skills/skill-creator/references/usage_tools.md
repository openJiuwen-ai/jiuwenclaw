# TOOL Definition Usage

Translate toolDefinition entries from `<workspace>/resources/available-tools/<bundleName>__<toolName>.json` into `invoke(funcName:"toolName", params:{bundleName:"...", ...})` calls in the new skill.

## Metadata Note

If the skill uses function tool dependencies, declare each tool in `SKILL.md` frontmatter so packaging can copy the source definitions:

```yaml
metadata:
  tools:
    - bundleName: "bundle_001"
      toolName: "weather_query"
```

Only declare tools the skill actually calls. Do not add empty placeholders. `bundleName` and `toolName` must match the source filename and JSON exactly.

## Input shape

`invoke` uses the toolDefinition `toolName` as `funcName` and passes tool arguments through `params`:

```text
invoke(funcName:"weather_query", params:{bundleName:"bundle_001", city:"北京"})
```

`params.bundleName` is always required and comes from `toolDefinition.bundleName`. The remaining params are the actual argument object passed to the tool. Build them from the `toolDefinition.arguments` JSON Schema. If `toolDefinition.arguments.required` lists fields, include every required field in `params`.

## toolDefinition fields

Read the source toolDefinition from `<workspace>/resources/available-tools/<bundleName>__<toolName>.json`:

```json
{
  "schemaVersion": "1.3",
  "generatedAt": "2026-04-30T00:00:00Z",
  "bundleName": "bundle_001",
  "toolName": "weather_query",
  "toolType": "XiaoYiPlugin",
  "pluginType": "Cloud",
  "protocol": "REST",
  "description": "查询指定城市的实时天气信息",
  "arguments": {
    "type": "object",
    "properties": {},
    "required": []
  },
  "deviceCommand": "optional for Device tools"
}
```

Field notes:

| Field | Required | How to use it |
| --- | --- | --- |
| `bundleName` | Yes | Copy exactly into metadata, source filename, and `invoke.params.bundleName`. |
| `toolName` | Yes | Copy exactly into `invoke.funcName`. |
| `description` | Yes | Use to decide when the tool should be called. |
| `arguments` | Yes | JSON Schema used to construct `invoke.params`. |
| `schemaVersion` | Yes | Definition version; do not pass to `invoke`. |
| `generatedAt` | Yes | Definition timestamp; do not pass to `invoke`. |
| `toolType` | No | Backend tool type; do not pass to `invoke`. |
| `pluginType` | Yes | Backend category such as `Cloud`, `Device`, or `MCP`; do not pass to `invoke`. |
| `protocol` | Cloud/MCP required | Transport detail; do not pass to `invoke`. |
| `deviceCommand` | Device required | Device command template; do not call it directly from the skill. |

## Safety

- Copy `bundleName` and `toolName` exactly; never invent IDs or names.
- Always include `bundleName` in `invoke.params`.
- Build `invoke.params` as structured data, never as command text.
- Include all required fields from `toolDefinition.arguments.required`.
- Preserve schema value types.
- Ask the user when a required value is missing and cannot be safely inferred.

## Example

toolDefinition:

```json
{
  "schemaVersion": "1.3",
  "generatedAt": "2026-04-28T10:00:00Z",
  "bundleName": "bundle_001",
  "toolName": "weather_query",
  "toolType": "XiaoYiPlugin",
  "description": "查询指定城市的实时天气信息",
  "arguments": {
    "type": "object",
    "properties": {
      "city": {
        "type": "string",
        "description": "城市名称。支持中文（北京、上海）或拼音（beijing）或英文（Beijing）。"
      }
    },
    "required": ["city"]
  },
  "protocol": "REST",
  "pluginType": "Cloud"
}
```

Generated:
Call the platform tool to execute:
```
    invoke(funcName:"weather_query", params:{bundleName:"bundle_001", city:"北京"})
```

## Generating the tool-definitions entry

Inside the skill body's single **tool definitions** section (see SKILL.md → Writing principles), each function tool becomes a `### Function: <toolName>（平台注册）` sub-block. 

Mapping rules:

| JSON field | Markdown field |
|------------|----------------|
| `toolName` | `### Function: <toolName>（平台注册）` heading + `- **toolName**` |
| `description` | `- **description**` |
| `arguments` | `- **参数**: （由平台自动注入）` |
| (n/a) | `- **约束**` |
| (n/a) | `- **语义**` |

- `- **参数**` 不要内联 JSON schema，运行时会注入。
- `- **约束**` 仅在工具有顺序、幂等性或前置条件时写。
- `- **语义**` 仅在触发措辞会路由到不同工具时写（如"删除" vs "取消"）。

Example — given the `weather_query` JSON above, generate:

```markdown
### Function: weather_query（平台注册）
- **toolName**: weather_query
- **description**: 查询指定城市的实时天气信息
- **参数**: （由平台自动注入）
```

No 约束 / 语义 line because the definition implies no special preconditions or trigger-phrase routing.
