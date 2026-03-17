# ModelRequestConfig 参数传递机制说明

## 概述

`ModelRequestConfig` 中定义的所有参数都会在实际调用模型时被正确传递。系统采用**两层参数配置机制**：

1. **配置层**：在 `ModelRequestConfig` 中预设默认参数
2. **调用层**：在 `invoke()` 或 `stream()` 方法中可以覆盖配置参数

## 支持的参数

在 `ModelRequestConfig` 中可以配置以下参数：

| 参数名 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `model` (model_name) | str | "" | 模型名称，如 "qwen-plus", "gpt-4" |
| `temperature` | float | 0.95 | 控制输出的随机性 (0.0-2.0) |
| `top_p` | float | 0.1 | Nucleus采样参数 (0.0-1.0) |
| `max_tokens` | int | None | 最大生成token数 |
| `stop` | str | None | 停止序列 |

## 参数传递原理

参数传递采用**优先级机制**，在 `BaseModelClient._build_request_params()` 方法中实现：

```python
# 优先使用调用时传入的参数，如果为None则使用配置中的参数
final_temperature = temperature if temperature is not None else self.model_config.temperature
if final_temperature is not None:
    params["temperature"] = final_temperature
```

### 优先级顺序

```
调用时传入的参数 > ModelRequestConfig中的配置 > 默认值
```

## 使用示例

### 示例1：使用配置的默认参数

```python
# 创建配置
model_config = ModelRequestConfig(
    model="qwen-plus",
    temperature=0.7,
    top_p=0.9,
    max_tokens=2000
)

model = Model(
    model_config=model_config,
    model_client_config=model_client_config
)

# 调用时不传参数，使用配置中的值
# 实际使用: temperature=0.7, top_p=0.9, max_tokens=2000
response = await model.invoke(messages=messages)
```

### 示例2：在调用时覆盖配置参数

```python
# 使用相同的model_config配置

# 调用时覆盖部分参数
# 实际使用: temperature=0.3(覆盖), top_p=0.9(配置), max_tokens=500(覆盖)
response = await model.invoke(
    messages=messages,
    temperature=0.3,      # 覆盖配置中的temperature
    max_tokens=500        # 覆盖配置中的max_tokens
    # top_p使用配置中的0.9
)
```

### 示例3：不同场景使用不同参数

```python
# 同一个model实例，不同调用场景使用不同参数

# 创意性任务：使用较高的temperature
creative_response = await model.invoke(
    messages=creative_messages,
    temperature=1.2,
    top_p=0.95
)

# 分析性任务：使用较低的temperature
analytical_response = await model.invoke(
    messages=analytical_messages,
    temperature=0.2,
    top_p=0.5
)
```

## 代码实现位置

### 1. 参数定义
- **文件**: `openjiuwen/core/foundation/llm/schema/config.py`
- **类**: `ModelRequestConfig`
- **行数**: 23-31

### 2. 参数合并逻辑
- **文件**: `openjiuwen/core/foundation/llm/model_clients/base_model_client.py`
- **方法**: `_build_request_params()`
- **行数**: 160-234
- **关键逻辑**: 195-213行

### 3. 参数传递
- **文件**: `openjiuwen/core/foundation/llm/model.py`
- **方法**: `invoke()` 和 `stream()`
- **行数**: 95-183

## 工作流程

```
用户调用 model.invoke(temperature=0.3)
    ↓
Model.invoke() 接收参数
    ↓
传递给 ModelClient.invoke()
    ↓
ModelClient._build_request_params() 合并参数
    ↓
优先级: 调用参数(0.3) > 配置参数 > 默认值
    ↓
构建最终请求参数
    ↓
发送给LLM API
```

## 注意事项

1. **参数为None vs 未传递**：只有显式传递 `None` 或不传递参数时，才会使用配置中的值
2. **额外参数支持**：`ModelRequestConfig` 使用 `model_config = {"extra": "allow"}`，支持传递额外的未定义参数
3. **timeout参数**：`timeout` 参数是 `ModelClientConfig` 的一部分，不在 `ModelRequestConfig` 中
4. **参数验证**：参数验证在 `BaseModelClient._validate_config()` 中进行

## 扩展自定义参数

如需添加新的请求参数：

1. 在 `ModelRequestConfig` 类中添加字段定义
2. 在 `BaseModelClient._build_request_params()` 中添加参数合并逻辑
3. 在 `Model.invoke()` 和 `Model.stream()` 的方法签名中添加参数
4. 在 `BaseModelClient` 的抽象方法签名中添加参数

## 总结

✅ **参数已正确实现传递机制**

所有在 `ModelRequestConfig` 中配置的参数都会在调用模型时被使用。系统提供了灵活的两层配置方式，既可以预设默认值，也可以在调用时动态覆盖。












