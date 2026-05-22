# TOOL Definition Usage

Translate toolDefinition entries from `<workspace>/resources/available-tools/<pluginId>__<toolName>.json` into `function_call_tool(pluginId="...", toolName="...", arguments={...})` calls in the new skill.


## Input shape

JSON array; only `pluginId`, `toolName`, and `arguments` drive the translation:

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
  }
}
```

字段说明：

字段	必填	说明
schemaVersion	是	当前 "1.3"
generatedAt	是	生成时间
pluginId	是	与文件名前缀一致
toolName	是	与文件名后缀一致
toolType	否	后端工具类型说明
pluginType	是	Cloud / Device / MCP
protocol	Cloud/MCP 必填	REST / SSE / Websocket
description	是	工具描述
arguments	是	JSON Schema
deviceCommand	Device 必填	端命令模板
arguments schema 使用 OpenAI 工具参数兼容写法：

用 required 明确必填字段。
不使用组合 schema。


## Example

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
    function_call_tool(pluginId="plugin_001", toolName="weather_query", arguments={"city": "北京"})

​
