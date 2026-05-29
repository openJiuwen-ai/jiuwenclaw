# TOOL Definition Usage

Translate toolDefinition entries from `<workspace>/resources/available-tools/<bundleName>__<toolName>.json` into the correct generated skill instructions.

- `pluginType: "Cloud"` or `"MCP"`: use `invoke(funcName:"toolName", params:{bundleName:"...", ...})`.
- `pluginType: "Device"`: `toolName` is the platform tool to use; do not write a function-style call. In the generated skill body, explain the tool's inputs from `arguments.properties`.

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

For Cloud/MCP tools, `invoke` uses the toolDefinition `toolName` as `funcName` and passes tool arguments through `params`:

```text
invoke(funcName:"weather_query", params:{bundleName:"bundle_001", city:"北京"})
```

For Cloud/MCP tools, `params.bundleName` is always required and comes from `toolDefinition.bundleName`. The remaining params are the actual argument object passed to the tool. Build them from the `toolDefinition.arguments` JSON Schema. If `toolDefinition.arguments.required` lists fields, include every required field in `params`.

For Device tools, do not use `invoke`, do not pass `bundleName`, and do not write `toolName(...)`. The `toolDefinition.toolName` value is the tool name. In the generated `SKILL.md`, tell the agent to use that tool and document the input parameters from `toolDefinition.arguments.properties`, including all fields listed in `toolDefinition.arguments.required`.

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
| `bundleName` | Yes | Copy exactly into metadata and source filename. For Cloud/MCP only, also copy into `invoke.params.bundleName`. Device tool instructions do not include it as an input. |
| `toolName` | Yes | Copy exactly into `invoke.funcName` for Cloud/MCP. For Device, treat this value as the tool name and document its inputs; do not wrap it in a call expression. |
| `description` | Yes | Use to decide when the tool should be called. |
| `arguments` | Yes | JSON Schema used to construct Cloud/MCP `invoke.params` or document Device tool inputs. |
| `schemaVersion` | Yes | Definition version; do not include in generated usage instructions. |
| `generatedAt` | Yes | Definition timestamp; do not include in generated usage instructions. |
| `toolType` | No | Backend tool type; do not include in generated usage instructions. |
| `pluginType` | Yes | Backend category such as `Cloud`, `Device`, or `MCP`; choose the generated usage pattern from this field and do not include it as an input. |
| `protocol` | Cloud/MCP required | Transport detail; do not include in generated usage instructions. |
| `deviceCommand` | Device required | Device command template; do not call it directly from the skill. |

## Safety

- Copy `bundleName` and `toolName` exactly; never invent IDs or names.
- For Cloud/MCP tools, always include `bundleName` in `invoke.params`.
- For Device tools, never use `invoke`, never pass `bundleName`, and never write `toolName(...)`; document `toolName` as the tool and list its inputs.
- Build Cloud/MCP tool arguments as structured data, never as command text. For Device tools, describe inputs as parameters, not as command text.
- Include all required fields from `toolDefinition.arguments.required`.
- Preserve schema value types.
- Ask the user when a required value is missing and cannot be safely inferred.

## Cloud/MCP example

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

## Device example

toolDefinition:

```json
{
  "schemaVersion": "1.3",
  "generatedAt": "2026-04-28T10:00:00Z",
  "bundleName": "bundle_002",
  "toolName": "set_screen_brightness",
  "toolType": "XiaoYiPlugin",
  "description": "设置设备屏幕亮度",
  "arguments": {
    "type": "object",
    "properties": {
      "level": {
        "type": "integer",
        "description": "屏幕亮度，范围 0 到 100。"
      }
    },
    "required": ["level"]
  },
  "deviceCommand": "settings display brightness <level>",
  "pluginType": "Device"
}
```

Generated:
Document the Device tool in the skill body:
```markdown
### Function: set_screen_brightness（平台注册）
- **toolName**: set_screen_brightness
- **description**: 设置设备屏幕亮度
- **参数**:
  - `level` (integer, required): 屏幕亮度，范围 0 到 100。
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

- For Cloud/MCP tools, `- **参数**` 不要内联 JSON schema，运行时会注入。
- For Device tools, `- **参数**` must list the actual inputs from `arguments.properties`, marking fields in `arguments.required` as required. Do not include `bundleName`, `pluginType`, or `deviceCommand` as parameters.
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
