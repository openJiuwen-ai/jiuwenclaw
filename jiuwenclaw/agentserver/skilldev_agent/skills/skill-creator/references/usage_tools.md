# TOOL Definition Usage

Translate toolDefinition entries from `<workspace>/resources/available-tools/<pluginId>__<toolName>.json` into `function_call_tool(pluginId="...", toolName="...", arguments={...})` calls in the new skill.

## Metadata Note

If the skill uses function tool dependencies, declare each tool in `SKILL.md` frontmatter so packaging can copy the source definitions:

```yaml
metadata:
  tools:
    - pluginId: "plugin_001"
      toolName: "weather_query"
```

Only declare tools the skill actually calls. Do not add empty placeholders. `pluginId` and `toolName` must match the source filename and JSON exactly.

## Input shape

`function_call_tool` input is a JSON object with exactly these fields:

```json
{
  "pluginId": "plugin_001",
  "toolName": "weather_query",
  "arguments": {
    "<param>": "<value>"
  }
}
```

`arguments` is the actual argument object passed to the tool. Build it from the `toolDefinition.arguments` JSON Schema.

## toolDefinition fields

Read the source toolDefinition from `<workspace>/resources/available-tools/<pluginId>__<toolName>.json`:

```json
{
  "schemaVersion": "1.3",
  "generatedAt": "2026-04-30T00:00:00Z",
  "pluginId": "plugin_001",
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
| `pluginId` | Yes | Copy exactly into `function_call_tool.pluginId`. |
| `toolName` | Yes | Copy exactly into `function_call_tool.toolName`. |
| `description` | Yes | Use to decide when the tool should be called. |
| `arguments` | Yes | JSON Schema used to construct `function_call_tool.arguments`. |
| `schemaVersion` | Yes | Definition version; do not pass to `function_call_tool`. |
| `generatedAt` | Yes | Definition timestamp; do not pass to `function_call_tool`. |
| `toolType` | No | Backend tool type; do not pass to `function_call_tool`. |
| `pluginType` | Yes | Backend category such as `Cloud`, `Device`, or `MCP`; do not pass to `function_call_tool`. |
| `protocol` | Cloud/MCP required | Transport detail; do not pass to `function_call_tool`. |
| `deviceCommand` | Device required | Device command template; do not call it directly from the skill. |

## Safety

- Copy `pluginId` and `toolName` exactly; never invent IDs or names.
- Build `function_call_tool.arguments` as structured JSON, never as command text.
- Include all required fields from `toolDefinition.arguments.required`.
- Preserve schema value types.
- Ask the user when a required value is missing and cannot be safely inferred.

## Example

toolDefinition:

```json
{
  "schemaVersion": "1.3",
  "generatedAt": "2026-04-28T10:00:00Z",
  "pluginId": "plugin_001",
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
Call the function_call_tool tool to execute:
```
    function_call_tool(pluginId="plugin_001", toolName="weather_query", arguments={"city": "北京"})
```
