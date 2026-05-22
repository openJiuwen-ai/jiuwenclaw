# AGENT Definition Usage
Translate agentDefinition entries from `<workspace>/resources/agents/available_agents.json` into `agent_as_a_tool(agentId = "", query="", filesInfo = [])` calls in the new skill.

## Input shape
```json
{
  "agentId": "string",
  "name": "string",
  "description": "string",
  "parameters": {
    "type": "object",
    "properties": {},
    "required": []
  }
}
```

### 字段说明
|字段|必填|说明|
| ---- | ---- | ---- |
|agentId|是|Agent唯一标识，调用入参匹配该值|
|name|否|Agent业务名称|
|description|是|Agent能力描述|
|parameters|是|入参JSON Schema，兼容OpenAI参数格式|
|properties|是|参数属性定义|
|required|是|标记必填参数名数组|

### 标准参数释义
- query：字符串类型，用户实际查询指令内容
- filesInfo：对象数组类型，关联上传文件信息集合

## Safety
- 严格依据入参`agentId`匹配调用对象，不可篡改标识
- 必填参数必须传值，缺失则无法正常发起Agent调用
- 文件信息按需传入，无文件场景默认空数组`[]`

## Example
### 原始Agent定义
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
agent_as_a_tool(agentId = "aaabbbccc", query="查询北京三日游玩攻略", filesInfo = [])