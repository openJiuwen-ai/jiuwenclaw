# OpenJiuWen LLM 模块使用分析报告

## 目录

1. [模块概述](#1-模块概述)
2. [架构设计](#2-架构设计)
3. [核心组件详解](#3-核心组件详解)
4. [配置规范](#4-配置规范)
5. [消息类型规范](#5-消息类型规范)
6. [调用方式规范](#6-调用方式规范)
7. [输出解析器规范](#7-输出解析器规范)
8. [Tool 调用规范](#8-tool-调用规范)
9. [流式输出规范](#9-流式输出规范)
10. [扩展开发规范](#10-扩展开发规范)
11. [最佳实践](#11-最佳实践)
12. [常见问题与注意事项](#12-常见问题与注意事项)

---

## 1. 模块概述

### 1.1 模块位置
```
openjiuwen/core/foundation/llm/
```

### 1.2 模块职责
LLM 模块提供统一的大语言模型调用入口，主要负责：
- 封装不同服务提供商（OpenAI、SiliconFlow 等）的 API 调用
- 提供统一的消息格式和接口规范
- 支持同步/异步调用和流式输出
- 内置输出解析器，支持 JSON格式解析
- 支持 Function Calling / Tool Calling

### 1.3 目录结构
```
llm/
├── __init__.py                    # 公共 API 导出
├── model.py                       # 统一调用入口 Model 类
├── model_clients/                 # 模型客户端实现
│   ├── base_model_client.py       # 基类
│   ├── openai_model_client.py     # OpenAI 兼容客户端
│   ├── siliconflow_model_client.py # SiliconFlow 客户端
│   └── dashscope_model_client.py  # DashScope 客户端
├── output_parsers/                # 输出解析器
│   ├── output_parser.py           # 基类
│   ├── json_output_parser.py      # JSON 解析器
│   └── markdown_output_parser.py  # Markdown 解析器
└── schema/                        # 数据模型定义
    ├── config.py                  # 配置类
    ├── message.py                 # 消息类
    ├── message_chunk.py           # 流式消息块
    └── tool_call.py               # 工具调用
```

---

## 2. 架构设计

### 2.1 类层次结构

```
┌─────────────────────────────────────────────────────────────┐
│                         Model                                │
│  (统一入口，负责创建和管理 ModelClient)                       │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                    BaseModelClient                           │
│  (抽象基类，定义接口规范)                                     │
└─────────────────────────────────────────────────────────────┘
           ┌────────────────┼────────────────┐
           ▼                ▼                ▼
   ┌───────────────┐ ┌───────────────┐ ┌───────────────┐
   │ OpenAIModel   │ │ SiliconFlow   │ │ DashScope     │
   │   Client      │ │  ModelClient  │ │  ModelClient  │
   └───────────────┘ └───────────────┘ └───────────────┘
```

### 2.2 数据流

```
用户请求 → Model.invoke() / Model.stream()
    │
    ▼
ModelClient._build_request_params()  ← 参数合并（调用参数 > ModelRequestConfig 配置）
    │
    ▼
API 调用 (OpenAI SDK / HTTP Client)
    │
    ▼
响应解析 → OutputParser.parse() (可选)
    │
    ▼
返回 AssistantMessage / AsyncIterator[AssistantMessageChunk]
```

---

## 3. 核心组件详解

### 3.1 Model 类

**位置**: `model.py`

**职责**: 统一的 LLM 调用入口，负责根据配置创建对应的 ModelClient

**核心方法**:
| 方法 | 描述 | 返回类型 |
|------|------|----------|
| `invoke()` | 异步单次调用 | `AssistantMessage` |
| `stream()` | 异步流式调用 | `AsyncIterator[AssistantMessageChunk]` |

**初始化参数**:
```python
Model(
    model_client_config: ModelClientConfig,  # 必填，客户端配置
    model_config: ModelRequestConfig = None  # 可选，模型请求配置
)
```

### 3.2 BaseModelClient 抽象基类

**位置**: `model_clients/base_model_client.py`

**职责**: 定义所有 ModelClient 的接口规范和通用功能

**核心功能**:
- 配置验证 (`_validate_config`)
- 消息格式转换 (`_convert_messages_to_dict`)
- 工具格式转换 (`_convert_tools_to_dict`)
- 请求参数构建 (`_build_request_params`)

### 3.3 已支持的 ModelClient 实现

| Client | Provider | 说明 |
|--------|----------|------|
| `OpenAIModelClient` | OpenAI | 支持 OpenAI 官方 API 及兼容接口 |
| `SiliconFlowModelClient` | SiliconFlow | 使用 aiohttp 实现的原生 HTTP 客户端 |
| `DashScopeModelClient` | DashScope | 阿里云灵积模型服务 |

---

## 4. 配置规范

### 4.1 ModelClientConfig (客户端配置)

**位置**: `schema/config.py`

| 字段 | 类型 | 默认值 | 必填 | 描述 |
|------|------|--------|------|------|
| `client_id` | `str` | UUID自动生成 | 否 | 客户端唯一标识 |
| `client_provider` | `str` | - | **是** | 服务提供商，枚举值：`OpenAI`、`SiliconFlow` |
| `api_key` | `str` | - | **是** | API 密钥 |
| `api_base` | `str` | - | **是** | API 基础 URL |
| `timeout` | `float` | `60.0` | 否 | 请求超时时间（秒） |
| `max_retries` | `int` | `3` | 否 | 最大重试次数 |
| `verify_ssl` | `bool` | `True` | 否 | 是否验证 SSL 证书 |
| `ssl_cert` | `str` | `None` | 条件必填 | SSL 证书路径（verify_ssl=True 时必填） |

**示例**:
```python
from openjiuwen.core.foundation.llm import ModelClientConfig

model_client_config = ModelClientConfig(
    client_provider="OpenAI",
    api_key="sk-xxx",
    api_base="https://api.openai.com/v1",
    timeout=60.0,
    verify_ssl=False
)
```

### 4.2 ModelRequestConfig (请求配置)

**位置**: `schema/config.py`

| 字段 | 类型 | 默认值 | 描述 |
|------|------|--------|------|
| `model` / `model_name` | `str` | `""` | 模型名称，如 `gpt-4`、`qwen-plus` |
| `temperature` | `float` | `0.95` | 温度参数，控制输出随机性 |
| `top_p` | `float` | `0.1` | Top-p 采样参数 |
| `max_tokens` | `int` | `None` | 最大生成 token 数 |
| `stop` | `str` | `None` | 停止序列 |

**特性**: 支持 `extra="allow"`，可添加自定义参数传递给模型 API

**示例**:
```python
from openjiuwen.core.foundation.llm import ModelRequestConfig

model_config = ModelRequestConfig(
    model="qwen-plus",
    temperature=0.7,
    top_p=0.9,
    max_tokens=2000,
)
```

### 4.3 参数优先级规则

调用时传入的参数 **优先于** `ModelRequestConfig` 中的配置：

```python
# ModelRequestConfig 配置
model_config = ModelRequestConfig(
    model="qwen-plus",
    temperature=0.7,
    max_tokens=2000,
)

# 调用时覆盖
response = await model.invoke(
    messages=messages,
    temperature=0.3,  # 覆盖为 0.3
    max_tokens=500    # 覆盖为 500
    # top_p 使用配置中的值
)
```

---

## 5. 消息类型规范

### 5.1 消息类型概览

**位置**: `schema/message.py`

| 类 | role | 描述 |
|----|----|------|
| `SystemMessage` | `system` | 系统提示词 |
| `UserMessage` | `user` | 用户消息 |
| `AssistantMessage` | `assistant` | 助手回复（模型输出） |
| `ToolMessage` | `tool` | 工具返回结果 |

### 5.2 BaseMessage 基类

```python
class BaseMessage(BaseModel):
    role: str
    content: Union[str, List[Union[str, dict]]] = ""
    name: Optional[str] = None
```

### 5.3 AssistantMessage 详解

```python
class AssistantMessage(BaseMessage):
    role: str = "assistant"
    tool_calls: Optional[List[ToolCall]] = None     # 工具调用列表
    usage_metadata: Optional[UsageMetadata] = None  # 使用统计
    finish_reason: str = "null"                     # 结束原因
    parser_content: Optional[Any] = None            # 解析后的内容
    reasoning_content: Optional[str] = None         # 推理内容（DeepSeek 等）
```

### 5.4 UsageMetadata 统计信息

```python
class UsageMetadata(BaseModel):
    code: int = 0                    # 错误码
    err_msg: str = ""                # 错误信息
    prompt: str = ""                 # 提示词
    task_id: str = ""                # 任务 ID
    model_name: str = ""             # 模型名称
    total_latency: float = 0.        # 总延迟
    first_token_time: str = ""       # 首 token 时间
    request_start_time: str = ""     # 请求开始时间
    input_tokens: int = 0            # 输入 token 数
    output_tokens: int = 0           # 输出 token 数
    total_tokens: int = 0            # 总 token 数
    cache_tokens: int = 0            # 缓存 token 数
```

### 5.5 消息格式使用

**方式一：使用消息类**
```python
from openjiuwen.core.foundation.llm import SystemMessage, UserMessage

messages = [
    SystemMessage(content="你是一个AI助手"),
    UserMessage(content="你好")
]
```

**方式二：使用字典格式**
```python
messages = [
    {"role": "system", "content": "你是一个AI助手"},
    {"role": "user", "content": "你好"}
]
```

**方式三：直接使用字符串（自动转为 user 消息）**
```python
response = await model.invoke("你好")
# 等价于 [{"role": "user", "content": "你好"}]
```

---

## 6. 调用方式规范

### 6.1 完整调用示例

```python
import asyncio
from openjiuwen.core.foundation.llm import (
    Model, ModelClientConfig, ModelRequestConfig,
    SystemMessage, UserMessage
)

# 1. 配置客户端
model_client_config = ModelClientConfig(
    client_provider="OpenAI",
    api_key="sk-xxx",
    api_base="https://dashscope.aliyuncs.com/compatible-mode/v1",
    verify_ssl=False
)

# 2. 配置模型参数
model_config = ModelRequestConfig(
    model="qwen-plus",
    temperature=0.7,
    top_p=0.9,
    max_tokens=2000,
)

# 3. 创建 Model 实例
model = Model(
    model_client_config=model_client_config,
    model_config=model_config
)

# 4. 构造消息
messages = [
    SystemMessage(content="你是一个AI助手").model_dump(exclude_none=True),
    UserMessage(content="帮我写一首诗").model_dump(exclude_none=True)
]

# 5. 调用
async def main():
    response = await model.invoke(messages=messages)
    print(response.content)

asyncio.run(main())
```

### 6.2 invoke() 方法参数

```python
async def invoke(
    messages: Union[str, List[BaseMessage], List[dict]],  # 消息列表
    *,
    tools: Union[List[ToolInfo], List[dict], None] = None,  # 工具列表
    temperature: Optional[float] = None,  # 温度
    top_p: Optional[float] = None,        # Top-p
    max_tokens: Optional[int] = None,     # 最大 token
    stop: Union[Optional[str], None] = None,  # 停止词
    model: str = None,                    # 模型名称
    output_parser: Optional[BaseOutputParser] = None,  # 输出解析器
    timeout: float = None,                # 超时时间
    **kwargs                              # 额外参数
) -> AssistantMessage
```

### 6.3 stream() 方法参数

参数与 `invoke()` 相同，返回类型为 `AsyncIterator[AssistantMessageChunk]`

---

## 7. 输出解析器规范

### 7.1 BaseOutputParser 基类

**位置**: `output_parsers/output_parser.py`

```python
class BaseOutputParser(ABC):
    @abstractmethod
    async def parse(self, inputs) -> Any:
        """解析 LLM 输出"""
        pass
    
    @abstractmethod
    async def stream_parse(self, streaming_inputs: AsyncIterator) -> AsyncIterator[Any]:
        """解析流式 LLM 输出"""
        pass
```

### 7.2 内置解析器

#### JsonOutputParser

**功能**: 从 LLM 输出中提取 JSON 内容

**特性**:
- 支持 ` ```json ... ``` ` 格式
- 支持裸 JSON 字符串
- 自动处理解析错误

**使用示例**:
```python
from openjiuwen.core.foundation.llm import JsonOutputParser

parser = JsonOutputParser()
response = await model.invoke(
    messages=messages,
    output_parser=parser
)
# response.parser_content 将包含解析后的 dict/list
```



---

## 8. Tool 调用规范

### 8.1 ToolInfo 定义

**位置**: `openjiuwen/core/foundation/tool/schema.py`

```python
class ToolInfo(BaseModel):
    type: str = "function"
    name: str = ""
    description: str = ""
    parameters: Union[Dict[str, Any], Type[BaseModel]] = {}
```

### 8.2 ToolCall 响应结构

```python
class ToolCall(BaseModel):
    id: Optional[str]      # 工具调用 ID
    type: str              # 类型，固定为 "function"
    name: str              # 工具名称
    arguments: str         # 工具参数（JSON 字符串）
    index: Optional[int]   # 调用索引
```

### 8.3 Tool 调用示例

```python
# 定义工具
tools = [{
    'type': 'function',
    'function': {
        'name': 'get_weather',
        'description': '获取天气预报',
        'parameters': {
            'type': 'object',
            'properties': {
                'location': {
                    'type': 'string',
                    'description': '城市名称',
                }
            },
            'required': ['location']
        }
    }
}]

# 调用
response = await model.invoke(
    messages=messages,
    tools=tools
)

# 检查是否有工具调用
if response.tool_calls:
    for tool_call in response.tool_calls:
        print(f"Tool: {tool_call.name}")
        print(f"Arguments: {tool_call.arguments}")
```

### 8.4 工具调用流程

```
1. 用户请求 + tools 定义
       │
       ▼
2. 模型返回 AssistantMessage (finish_reason="tool_calls")
       │
       ▼
3. 解析 tool_calls，执行对应工具
       │
       ▼
4. 构造 ToolMessage 返回工具结果
       │
       ▼
5. 继续对话，包含工具结果
```

### 8.5 ToolMessage 使用

```python
from openjiuwen.core.foundation.llm import ToolMessage

# 工具执行后构造返回消息
tool_result = ToolMessage(
    content='{"temperature": 25, "weather": "晴天"}',
    tool_call_id=response.tool_calls[0].id
)
```

---

## 9. 流式输出规范

### 9.1 AssistantMessageChunk 结构

```python
class AssistantMessageChunk(AssistantMessage, BaseMessageChunk):
    # 继承自 AssistantMessage，新增流式合并能力
    # 支持 __add__ 操作符进行 chunk 合并
```

### 9.2 流式调用示例

```python
async def stream_example():
    full_content = ""
    async for chunk in model.stream(messages=messages):
        # 增量内容
        if chunk.content:
            full_content += chunk.content
            print(chunk.content, end="", flush=True)
        
        # 推理内容（如 DeepSeek）
        if chunk.reasoning_content:
            print(f"[思考] {chunk.reasoning_content}")
        
        # 工具调用片段
        if chunk.tool_calls:
            for tc in chunk.tool_calls:
                print(f"Tool call delta: {tc.name} - {tc.arguments}")
        
        # 使用统计（通常在最后一个 chunk）
        if chunk.usage_metadata:
            print(f"\nTokens: {chunk.usage_metadata.total_tokens}")
        
        # 解析后的内容（使用 output_parser 时）
        if chunk.parser_content:
            print(f"Parsed: {chunk.parser_content}")
```

### 9.3 Chunk 合并

```python
# 手动合并 chunks
accumulated = None
async for chunk in model.stream(messages=messages):
    if accumulated is None:
        accumulated = chunk
    else:
        accumulated = accumulated + chunk

# accumulated 现在包含完整内容
print(accumulated.content)
```

---

## 10. 扩展开发规范

### 10.1 新增 ModelClient

1. 继承 `BaseModelClient`
2. 实现 `invoke()` 和 `stream()` 方法
3. 可选：重写 `_get_client_name()` 和 `_validate_config()`

```python
from openjiuwen.core.foundation.llm.model_clients.base_model_client import BaseModelClient

class CustomModelClient(BaseModelClient):
    def _get_client_name(self) -> str:
        return "Custom Client"
    
    async def invoke(self, messages, *, tools=None, ...) -> AssistantMessage:
        # 使用父类方法构建参数
        params = self._build_request_params(
            messages=messages,
            tools=tools,
            stream=False,
            ...
        )
        # 调用自定义 API
        response = await self._call_api(params)
        # 返回 AssistantMessage
        return self._parse_response(response)
    
    async def stream(self, messages, *, tools=None, ...) -> AsyncIterator[AssistantMessageChunk]:
        # 流式实现
        pass
```

4. 在 `model.py` 中注册

```python
_CLIENT_TYPE_REGISTRY: Dict[str, Type[BaseModelClient]] = {
    "OpenAI": OpenAIModelClient,
    "SiliconFlow": SiliconFlowModelClient,
    "CustomProvider": CustomModelClient,  # 新增
}
```

### 10.2 新增 OutputParser

1. 继承 `BaseOutputParser`
2. 实现 `parse()` 和 `stream_parse()` 方法

```python
from openjiuwen.core.foundation.llm.output_parsers.output_parser import BaseOutputParser

class XmlOutputParser(BaseOutputParser):
    async def parse(self, inputs) -> Any:
        # 解析 XML 格式
        pass
    
    async def stream_parse(self, streaming_inputs) -> AsyncIterator[Any]:
        # 流式解析
        pass
```

---

## 11. 最佳实践

### 11.1 配置管理

```python
# ✅ 推荐：使用环境变量管理敏感信息
import os

model_client_config = ModelClientConfig(
    client_provider="OpenAI",
    api_key=os.getenv("OPENAI_API_KEY"),
    api_base=os.getenv("OPENAI_API_BASE"),
)
```

### 11.2 错误处理

```python
from openjiuwen.core.common.exception.exception import JiuWenBaseException

try:
    response = await model.invoke(messages=messages)
except JiuWenBaseException as e:
    print(f"Error code: {e.error_code}")
    print(f"Message: {e.message}")
```

### 11.3 超时控制

```python
# 方式一：全局配置
model_client_config = ModelClientConfig(
    ...,
    timeout=120.0  # 120 秒超时
)

# 方式二：单次调用覆盖
response = await model.invoke(
    messages=messages,
    timeout=30.0  # 本次调用 30 秒超时
)
```

### 11.4 消息格式统一

```python
# ✅ 推荐：使用 model_dump() 转换为字典
messages = [
    SystemMessage(content="你是助手").model_dump(exclude_none=True),
    UserMessage(content="你好").model_dump(exclude_none=True)
]

# ✅ 也可以：直接使用字典
messages = [
    {"role": "system", "content": "你是助手"},
    {"role": "user", "content": "你好"}
]
```

### 11.5 流式处理最佳实践

```python
async def robust_stream():
    try:
        async for chunk in model.stream(messages=messages):
            if chunk.content:
                # 实时处理内容
                yield chunk.content
    except Exception as e:
        # 处理流中断
        logger.error(f"Stream error: {e}")
        raise
```

---

## 12. 常见问题与注意事项

### 12.1 配置验证

| 错误场景 | 错误信息 |
|---------|---------|
| 缺少 `client_provider` | `model client config client_provider is none` |
| 缺少 `client_id` | `model client config client_id is none` |
| 缺少 `api_key` | `api_key is required for {client_name}` |
| 缺少 `api_base` | `api_base is required for {client_name}` |
| `verify_ssl=True` 但无 `ssl_cert` | `ssl_cert is required when verify_ssl is True` |

### 12.2 消息验证

- 消息列表不能为空
- 必须指定模型名称（通过 `ModelRequestConfig` 或调用时传入）

### 12.3 SSL 配置

```python
# 生产环境：启用 SSL 验证
model_client_config = ModelClientConfig(
    ...,
    verify_ssl=True,
    ssl_cert="/path/to/cert.pem"
)

# 开发/测试环境：可禁用
model_client_config = ModelClientConfig(
    ...,
    verify_ssl=False
)
```

### 12.4 代理配置

系统自动从 `UrlUtils.get_global_proxy_url()` 获取代理配置。

### 12.5 日志与调试

LLM 模块使用统一的 `llm_logger` 进行日志记录，支持以下事件类型：
- `LogEventType.LLM_CALL_START` - 调用开始
- `LogEventType.LLM_CALL_END` - 调用结束
- `LogEventType.LLM_CALL_ERROR` - 调用错误

---

## 附录：公开 API 列表

```python
from openjiuwen.core.foundation.llm import (
    # 核心类
    Model,
    BaseModelClient,
    BaseOutputParser,
    
    # 配置类
    ModelRequestConfig,
    ModelClientConfig,
    BaseModelInfo,
    ModelConfig,
    
    # 消息类
    BaseMessage,
    AssistantMessage,
    UserMessage,
    SystemMessage,
    ToolMessage,
    UsageMetadata,
    
    # 流式消息
    AssistantMessageChunk,
    
    # 工具相关
    ToolCall,
    
    # 内置实现
    OpenAIModelClient,
    JsonOutputParser,
    MarkdownOutputParser,
)
```

---



