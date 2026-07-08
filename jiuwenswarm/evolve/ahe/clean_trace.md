一、整体架构

  ┌──────────────────────────────────────────────────────────────────┐
  │  Pipeline 入口                                                    │
  │                                                                    │
  │  TraceBatch                                                        │
  │    │ trace_ids: ["abc123...", "def456..."]                         │
  │    │ source: "manual"                                              │
  │    ▼                                                               │
  │  SqliteStore.query_spans(trace_id)                                 │
  │    │ 返回 List[OTEL span row dict]                                 │
  │    ▼                                                               │
  │  OtelTraceAdapter.convert_trace(trace_id, db_path)                 │
  │    │ ① 读 spans → ② 拍平+排序 → ③ 逐 span 转为 observation        │
  │    │ ④ 组装 trace dict → ⑤ 传入 _extract_trace_data_impl          │
  │    ▼                                                               │                                                            │
  │  ProposalGenerator 使用 cleaned_trace                              │
  └──────────────────────────────────────────────────────────────────┘

  adapter 是唯一的桥梁——OTEL 侧不改、trace_converter 侧不改，所有映射逻辑集中在一处。

  二、新增文件与模块位置

  ┌──────────────────────┬────────────────────────────────────────┬──────────────┐
  │         文件         │                  位置                  │     职责     │
  ├──────────────────────┼────────────────────────────────────────┼──────────────┤
  │ otel_adapter.py      │ jiuwenswarm/evolve/otel_adapter.py     │ 核心转换逻辑 │
  ├──────────────────────┼────────────────────────────────────────┼──────────────┤
  │ test_otel_adapter.py │ jiuwenswarm/tests/test_otel_adapter.py │ 单元测试     │
  └──────────────────────┴────────────────────────────────────────┴──────────────┘

  不改 models.py 的 TraceBatch——它已经足够，trace_ids + SqliteStore 的模式不需要扩展。

让 `otel_adapter.py` 的 `convert_trace()` 返回的数据结构与 `ahe代码仓中的_extract_trace_data_impl` 完全一致，并尽可能包含更多有用的信息（但不冗余）。

---

## 二、数据结构对比

### 2.1 _extract_trace_data_impl 返回的 cleaned_trace

```python
{
    # 必需字段
    "id": trace_id,
    "timestamp": ISO 8601,
    "name": trace_name,
    "input": {...},
    "output": {...},
    "latency": ms数值,
    
    # Langfuse metadata (可选)
    "totalCost": "N/A",
    "sessionId": "N/A",
    "userId": "N/A",
    "projectId": "N/A",
    
    # 从observations提取
    "system_prompt": str,           # 从LLM span的system message提取
    "messages_count": int,          # 总消息数
    "messages": list[dict],         # system + user + agent_turns
    "total_tokens": int,            # 聚合所有LLM span的tokens
    "observation_count": int,       # span总数
    "generation_count": int,        # LLM span数量
    "subagents": list[dict],        # 子agent轨迹
    "tool_definitions": list[dict], # 工具定义
    
    # 可选字段
    "user_message": str,
    "calculated_total_cost": float,
    "model": str                    # 第一个LLM span的model
}
```

### 2.2 我们的OTEL数据优势

| 数据项 | OTEL attributes来源 | 完整度 | 备注 |
|--------|-------------------|--------|------|
| **messages** | `gen_ai.input.messages` | ✅ 完整 | 包含system/user/assistant完整对话 |
| **tool_definitions** | `gen_ai.tool.definitions` | ✅ 完整schema | 37个工具的完整参数定义 |
| **usage** | `gen_ai.usage.*` | ✅ 完整 | input/output/cache_read tokens |
| **model参数** | `gen_ai.request.temperature` 等 | ✅ 完整 | temperature, top_p, streaming等 |
| **统计信息** | `gen_ai.input.messages.count` 等 | ✅ 完整 | messages_count, total_length |
| **iteration** | `jiuwenclaw.iteration` | ✅ 独有 | LLM调用迭代次数 |

**关键差异**：
- ✅ **我们的数据更完整**：不需要从events重建，直接从attributes读取
- ✅ **有完整tool schema**：比原方案的"简化版parameters={}"更丰富
- ✅ **有额外参数**：temperature, top_p, streaming, iteration等

---

## 三、改进方案核心思路

### 3.1 关键改动

#### ✅ **改进方案**：
```python
# 直接从attributes读取完整数据
def _reconstruct_llm_input(attrs, events):
    # 优先从attributes读取
    if 'gen_ai.input.messages' in attrs:
        messages_raw = attrs['gen_ai.input.messages']
        if isinstance(messages_raw, str):
            messages = json.loads(messages_raw)  # 二次解码
        else:
            messages = messages_raw  # 已经是list
        
        # 构建完整input
        return {
            'model': attrs.get('gen_ai.request.model'),
            'messages': messages,  # ✅ 完整对话
            'tools': attrs.get('gen_ai.tool.definitions', [])  # ✅ 完整schema
        }
    
    # Fallback到events重建（保留兼容性）
    messages = []
    for ev in events:
        # ... 原方案的重建逻辑
```

### 3.2 新增字段（OTEL特有，更丰富）

在保持与 `_extract_trace_data_impl` 兼容的基础上，增加以下字段：

```python
{
    # ... 标准 cleaned_trace 字段 ...
    
    # 新增字段（OTEL特有，不冗余）
    "temperature": 0.95,                    # 模型参数
    "top_p": 0.1,
    "streaming": False,
    "iteration_count": 1,                   # LLM调用迭代次数
    "input_messages_total_length": 3378,    # messages总长度统计
    "cache_read_tokens": 1024,              # 缓存读取tokens
    "response_model": "deepseek-v4-pro",    # 实际使用的模型
    "agent_name": "jiuwenswarm",            # agent名称
    "conversation_id": "sess_19ef...",      # 会话ID
}
```

**为什么这些字段不冗余**：
- ✅ **temperature/top_p/streaming**: 影响模型行为的重要参数，对诊断和优化有用
- ✅ **iteration_count**: 表示agent的迭代次数，反映任务复杂度
- ✅ **cache_read_tokens**: 成本优化指标，影响pricing
- ✅ **response_model**: 实际调用的模型（可能与request_model不同）
- ✅ **agent_name/conversation_id**: trace归属信息，便于分类和追溯

---

## 四、字段映射表（完整版）

### 4.1 Trace顶层字段

| cleaned_trace字段 | OTEL来源 | 提取方法 | 优先级 |
|-------------------|----------|---------|--------|
| `id` | `trace_id` | 直取 | 必需 |
| `timestamp` | `root.start_time_ns` | `_ns_to_iso()` | 必需 |
| `name` | `root.name` | 直取 | 必需 |
| `input` | 从LLM span提取 | `_reconstruct_trace_input()` | 必需 |
| `output` | 从LLM span提取 | `_reconstruct_trace_output()` | 必需 |
| `latency` | `root.duration_ns` | `_ns_to_ms()` | 必需 |
| `system_prompt` | 第一个LLM span | 从messages提取 | 必需 |
| `messages_count` | 聚合计算 | `len(messages)` | 必需 |
| `messages` | 从LLM span提取 | 组装完整messages数组 | 必需 |
| `total_tokens` | 聚合所有LLM spans | 从`gen_ai.usage.total_tokens` | 必需 |
| `observation_count` | span总数 | `len(flat_spans)` | 必需 |
| `generation_count` | LLM span数 | 计数`gen_ai.span.type=="model"` | 必需 |
| `subagents` | agent span嵌套 | 检测agent层级关系 | 必需 |
| `tool_definitions` | LLM span attributes | `gen_ai.tool.definitions` | 必需 |
| `model` | 第一个LLM span | `gen_ai.request.model` | 可选 |
| `temperature` | LLM span attributes | `gen_ai.request.temperature` | 新增 |
| `top_p` | LLM span attributes | `gen_ai.request.top_p` | 新增 |
| `streaming` | LLM span attributes | `gen_ai.request.streaming` | 新增 |
| `iteration_count` | 聚合LLM spans | `jiuwenclaw.iteration` 最大值 | 新增 |
| `cache_read_tokens` | 聚合LLM spans | `gen_ai.usage.cache_read_tokens` | 新增 |
| `agent_name` | agent span | `gen_ai.agent.name` | 新增 |
| `conversation_id` | agent span | `gen_ai.conversation.id` | 新增 |

### 4.2 Observation字段（单个span）

保持原方案不变，但修改input/output的提取方法：

| 字段 | LLM span提取方法 | Tool span提取方法 |
|------|------------------|------------------|
| `input` | **优先从attributes读取**<br>`gen_ai.input.messages` + `gen_ai.tool.definitions` | 从events或attributes提取 |
| `output` | **优先从attributes读取**<br>最后一条assistant message + usage | 从events或attributes提取 |

---

## 五、实现步骤

### 步骤1：修改 `_reconstruct_llm_input`

```python
def _reconstruct_llm_input(self, attrs: dict, events: list) -> dict:
    """Reconstruct LLM span input - 优先从attributes读取完整数据."""
    
    # 优先方案：从attributes直接读取
    if 'gen_ai.input.messages' in attrs:
        messages_raw = attrs['gen_ai.input.messages']
        # 处理双重编码
        if isinstance(messages_raw, str):
            try:
                messages = json.loads(messages_raw)
                if isinstance(messages, str):  # 三重编码
                    messages = json.loads(messages)
            except json.JSONDecodeError:
                messages = []
        else:
            messages = messages_raw if isinstance(messages_raw, list) else []
        
        # 读取tools（完整schema）
        tools = []
        if 'gen_ai.tool.definitions' in attrs:
            tools_raw = attrs['gen_ai.tool.definitions']
            if isinstance(tools_raw, str):
                try:
                    tools = json.loads(tools_raw)
                except:
                    tools = []
            else:
                tools = tools_raw if isinstance(tools_raw, list) else []
        
        return {
            'model': attrs.get('gen_ai.request.model', ''),
            'messages': messages,
            'tools': tools  # ✅ 完整schema，不是简化版
        }
    
    # Fallback方案：从events重建（保留兼容性）
    messages = []
    for ev in events:
        ev_attrs = _parse_attrs(ev.get('attributes'))
        ev_name = ev.get('name', '')
        
        if ev_name == "gen_ai.system.message":
            messages.append({"role": "system", "content": ev_attrs.get("content", "")})
        elif ev_name == "gen_ai.user.message":
            messages.append({"role": "user", "content": ev_attrs.get("content", "")})
        # ... 其他event类型
    
    return {
        'model': attrs.get('gen_ai.request.model', ''),
        'messages': messages,
        'tools': []
    }
```

### 步骤2：修改 `_reconstruct_llm_output`

```python
def _reconstruct_llm_output(self, attrs: dict, events: list) -> dict:
    """Reconstruct LLM output - 从最后一个assistant message."""
    
    # 尝试从events找assistant message
    assistant_events = [ev for ev in events if ev.get('name') == 'gen_ai.assistant.message']
    
    if assistant_events:
        last = assistant_events[-1]
        ev_attrs = _parse_attrs(last.get('attributes'))
        content = ev_attrs.get('content', '')
        tool_calls_raw = ev_attrs.get('tool_calls', '')
        
        result = {"role": "assistant", "content": content}
        if tool_calls_raw:
            parsed_tc = _parse_tool_calls_repr(tool_calls_raw)
            if parsed_tc:
                result['tool_calls'] = parsed_tc
        
        # 添加usage
        usage = {}
        if attrs.get('gen_ai.usage.total_tokens'):
            usage['total_tokens'] = attrs['gen_ai.usage.total_tokens']
        if attrs.get('gen_ai.usage.input_tokens'):
            usage['input_tokens'] = attrs['gen_ai.usage.input_tokens']
        if attrs.get('gen_ai.usage.output_tokens'):
            usage['output_tokens'] = attrs['gen_ai.usage.output_tokens']
        if usage:
            result['usage'] = usage
        
        return result
    
    # Fallback: 返回空assistant message
    return {"role": "assistant", "content": ""}
```

### 步骤3：在 `convert_trace` 中增加字段

```python
def convert_trace(self, trace_id: str) -> dict[str, Any]:
    spans = self._read_flat_spans(trace_id)
    if not spans:
        return {"id": trace_id, "observations": []}
    
    root = self._find_root_span(spans)
    observations = [self._span_to_observation(s) for s in spans]
    
    # 基础trace dict
    trace_dict = {
        "id": trace_id,
        "trace_id": trace_id,
        "timestamp": _ns_to_iso(root.get("start_time_ns")),
        "name": root.get("name", "N/A"),
        "input": self._reconstruct_trace_input(root, spans),
        "output": self._reconstruct_trace_output(root, spans),
        "latency": _ns_to_ms(root.get("duration_ns")),
        "observations": observations,
        
        # 新增OTEL特有字段
        "agent_name": root.get('attributes', {}).get('gen_ai.agent.name'),
        "conversation_id": root.get('attributes', {}).get('gen_ai.conversation.id'),
    }
    
    return trace_dict
```

### 步骤4：修改 `convert_trace` 返回cleaned_trace

**选项A**：直接返回cleaned_trace格式

```python
def convert_to_cleaned_trace(self, trace_id: str) -> dict[str, Any]:
    """直接返回与 _extract_trace_data_impl 一致的 cleaned_trace."""
    
    spans = self._read_flat_spans(trace_id)
    if not spans:
        return {
            "id": trace_id,
            "timestamp": "N/A",
            "name": "N/A",
            "input": "N/A",
            "output": "N/A",
            "latency": "N/A",
            "system_prompt": "",
            "messages_count": 0,
            "messages": [],
            "total_tokens": "N/A",
            "observation_count": 0,
            "generation_count": 0,
            "subagents": [],
            "tool_definitions": []
        }
    
    # ... 提取和计算逻辑
    
    # 构建cleaned_trace
    cleaned = {
        # 标准字段
        "id": trace_id,
        "timestamp": ...,
        "name": ...,
        "input": ...,
        "output": ...,
        "latency": ...,
        "system_prompt": ...,
        "messages_count": ...,
        "messages": ...,
        "total_tokens": ...,
        "observation_count": len(spans),
        "generation_count": llm_count,
        "subagents": ...,
        "tool_definitions": ...,
        "model": ...,
        
        # 新增字段
        "temperature": ...,
        "top_p": ...,
        "streaming": ...,
        "iteration_count": ...,
        "cache_read_tokens": ...,
        "agent_name": ...,
        "conversation_id": ...
    }
    
    return cleaned
```

**选项B**：保持trace_dict + observations格式，由外部调用`extract_trace_data`

保持原方案，让 `convert_trace` 返回标准trace dict，外部代码调用：
```python
trace_dict = adapter.convert_trace(trace_id)
cleaned = extract_trace_data(trace_dict, include_system_prompt_message=True)
```

---

## 六、测试验证

### 6.1 验证与 `_extract_trace_data_impl` 的兼容性

```python
# 测试代码
from jiuwenswarm.evolve.ahe.otel_adapter import OtelTraceAdapter
from trace_converter import extract_trace_data

adapter = OtelTraceAdapter('traces.db')
trace_dict = adapter.convert_trace('0dc2aa...')
cleaned = extract_trace_data(trace_dict, include_system_prompt_message=True)

# 验证必需字段存在
required_fields = ['id', 'timestamp', 'name', 'input', 'output', 'latency',
                   'system_prompt', 'messages_count', 'messages', 'total_tokens',
                   'observation_count', 'generation_count', 'subagents', 'tool_definitions']

for field in required_fields:
    assert field in cleaned, f"缺少必需字段: {field}"

# 验证新增字段
new_fields = ['temperature', 'top_p', 'iteration_count', 'cache_read_tokens']
for field in new_fields:
    if cleaned.get(field) is not None:  # 可选字段可能为None
        print(f"✅ 新增字段 {field}: {cleaned[field]}")
```

### 6.2 验证数据完整性

```python
# 验证messages完整性
messages = cleaned['messages']
assert len(messages) > 0, "messages为空"
assert messages[0]['role'] == 'system', "第一条不是system message"

# 验证tool_definitions完整性
tools = cleaned['tool_definitions']
assert len(tools) > 0, "tool_definitions为空"
assert tools[0].get('function', {}).get('name'), "tool没有name"
# 检查是否是完整schema（不是简化版）
if 'parameters' in tools[0]['function']:
    print("✅ tool_definitions包含完整schema")
```

---

## 七、不做的改动

| 排除项 | 原因 |
|--------|------|
| 修改OTEL instrumentor | 保持OTEL标准不变 |
| 修改trace_converter.py | 上游代码不动，适配层消化差异 |
| 删除fallback逻辑 | 保留events重建作为兼容性fallback |
| 添加cost字段 | OTEL不含pricing信息，设"N/A" |
| 添加所有attributes字段 | 避免冗余，只选有用字段 |

---

## 八、总结

### 核心改动：
1. ✅ **优先从attributes读取**messages和tools，不是events重建
2. ✅ **保留完整tool schema**，不是简化版
3. ✅ **新增有用的OTEL特有字段**（temperature等）
4. ✅ **保持与`_extract_trace_data_impl`完全兼容**

### 数据质量提升：
- messages: 从空列表 → ✅ 完整对话
- tool_definitions: 从简化版 → ✅ 完整schema（37个工具）
- 新增字段: temperature, iteration_count, cache_read_tokens等

### 实现建议：
- **推荐选项B**：保持 `convert_trace` 返回标准trace_dict格式，让外部调用 `extract_trace_data`
- 这样可以保持灵活性，让用户选择需要哪些字段（`include_system_prompt_message`等参数）