# 使用 ContextEngineeringRail 替换 Workspace/Context 提示词和 Context Processors

## 背景

当前 JiuwenClaw 采用**手动配置**方式管理：

1. **Context 提示词**：`_context_prompt()` 生成简单的上下文压缩提示（仅说明 offload 机制）
2. **Workspace 提示词**：`_workspace_prompt()` 生成静态的工作区路径（已注释）
3. **Context Processors**：`_proc_memory_compression_config()` 中注释的处理器配置逻辑

`ContextEngineeringRail` 提供了**完整的自动化方案**：

| 能力项 | 当前手动方式 | ContextEngineeringRail |
|--------|-------------|------------------------|
| Context 提示词 | 静态 offload 说明 | 动态读取 `memory/daily_memory/` + 配置文件 + 工具列表 |
| Workspace 提示词 | 静态路径 | 动态扫描真实目录结构（含描述） |
| Context Processors | 注释掉的配置逻辑 | 自动配置 preset processors，支持用户覆盖 |

---

## ContextEngineeringRail 的能力

根据 `agent-core` 中的实现，`ContextEngineeringRail` 提供以下能力：

### 1. init(agent)

自动配置 context processors 到 `agent.react_agent._config.context_processors`：

- **Preset Processors**（`preset=True` 时启用）：
  - `DialogueCompressor`：对话压缩（阈值：40条消息 / 100000 tokens）
  - `MessageOffloader`：消息卸载（阈值：40条消息 / 5000 tokens）
- **用户自定义 Processors**：通过 `processors` 参数传入，相同 key 会覆盖 preset

### 2. before_model_call(ctx)

自动注入 **workspace** 和 **context** prompt sections：

- **workspace_section**：动态扫描工作区目录结构（深度2层），包含目录描述
- **context_section**：读取以下文件内容：
  - `Agent.md`、`Config.md`、`Rules.md` 等配置文件
  - `memory/daily_memory/YYYY-MM-DD.md`（当日记忆）
  - 可用工具列表（从 ability_manager 获取）

### 3. uninit(agent)

自动从 `system_prompt_builder` 中移除 workspace 和 context sections。

---

## 修改目标

1. **移除静态 context/workspace 提示词**：从 `build_identity_prompt()` 和 `build_system_prompt_sections()` 中移除手动添加
2. **配置 ContextEngineeringRail**：通过 Rail 统一管理动态提示词和 processors
3. **保持兼容性**：确保原有功能（offload、压缩）继续工作

---

## 涉及文件

| 文件 | 修改类型 |
|------|----------|
| `jiuwenclaw/agentserver/deep_agent/interface_deep.py` | 配置 ContextEngineeringRail 参数 |
| `jiuwenclaw/agentserver/deep_agent/prompt_builder.py` | 移除静态 context/workspace 提示词 |

---

## 具体变更

### 1. ContextEngineeringRail 配置（interface_deep.py）

**当前实现**（line 304-316）：

```python
def _build_context_engineering_rail(self) -> ContextEngineeringRail | None:
    """Build ContextEngineeringRail."""
    try:
        context_rail = ContextEngineeringRail(
            processors=None,  # 使用预置配置
            language=self._resolve_runtime_language(),
            preset=True,
        )
        logger.info("[JiuWenClawDeepAdapter] ContextEngineeringRail create success")
        return context_rail
    except Exception as exc:
        logger.warning("[JiuWenClawDeepAdapter] ContextEngineeringRail create failed: %s", exc)
        return None
```

**问题**：`processors=None` 使用纯 preset，用户无法自定义 processors。

**建议增强**：支持从配置读取 processors：

```python
def _build_context_engineering_rail(self) -> ContextEngineeringRail | None:
    """Build ContextEngineeringRail."""
    try:
        # 从配置读取 processors（可选）
        processors = self._get_context_processors_from_config()

        context_rail = ContextEngineeringRail(
            processors=processors,
            language=self._resolve_runtime_language(),
            preset=True,
        )
        logger.info("[JiuWenClawDeepAdapter] ContextEngineeringRail create success")
        return context_rail
    except Exception as exc:
        logger.warning("[JiuWenClawDeepAdapter] ContextEngineeringRail create failed: %s", exc)
        return None

def _get_context_processors_from_config(self) -> list | None:
    """从配置读取 context processors（可选）。"""
    # TODO: 实现从 config 读取 processors 的逻辑
    # 例如：
    # memory_config = get_config().get('memory_compression', {})
    # if not memory_config.get('enabled'):
    #     return None
    # return self._build_processors_from_config(memory_config)
    return None
```

### 2. 移除静态 context/workspace 提示词（prompt_builder.py）

**当前 `build_identity_prompt()`**（line 957-982）：

```python
def build_identity_prompt(mode: str, language: str, channel: str) -> str:
    # ...
    builder.add_section(_start_prompt(resolved_language))
    builder.add_section(_context_prompt(resolved_language))      # 待移除
    # builder.add_section(_workspace_prompt(resolved_language))  # 已注释

    is_cron = (channel == "cron")
    builder.add_section(_memory_prompt(resolved_language, is_cron))

    builder.add_section(_principle_prompt(resolved_language))
    builder.add_section(_tone_prompt(resolved_language))
    builder.add_section(_safety_prompt(language))
    builder.add_section(_response_prompt(language))

    return builder.build()
```

**目标实现**：

```python
def build_identity_prompt(mode: str, language: str, channel: str) -> str:
    # ...
    builder.add_section(_start_prompt(resolved_language))
    # NOTE:  _workspace_prompt 现由 ContextEngineeringRail
    #       在 before_model_call() 中动态注入，此处不再手动添加

    is_cron = (channel == "cron")
    builder.add_section(_memory_prompt(resolved_language, is_cron))

    builder.add_section(_principle_prompt(resolved_language))
    builder.add_section(_tone_prompt(resolved_language))
    builder.add_section(_safety_prompt(language))
    builder.add_section(_response_prompt(language))

    return builder.build()
```

**当前 `build_system_prompt_sections()`**（line 900-929）：

```python
def build_system_prompt_sections(mode: str, channel: str, language: str) -> SystemPromptBuilder:
    # ...
    builder.add_section(_start_prompt(language))
    builder.add_section(_time_prompt(language))
    builder.add_section(_context_prompt(language))     
    builder.add_section(_workspace_prompt(language))   
    # ...
```

**目标实现**：

```python
def build_system_prompt_sections(mode: str, channel: str, language: str) -> SystemPromptBuilder:
    # ...
    builder.add_section(_start_prompt(language))
    builder.add_section(_time_prompt(language))
    builder.add_section(_context_prompt(language))   

```

### 3. 清理已注释的 processors 配置（interface_deep.py）

**可移除**（line 444-478）：`_proc_memory_compression_config()` 注释代码

**可简化**（line 516-524）：`_proc_context_compaction()` 中的注释说明

```python
async def _proc_context_compaction(self):
    """Process context compaction config."""
    # Context processors are now configured via ContextEngineeringRail.init()
    # (注释可保留或简化)
```

---

## ContextEngineeringRail 配置选项

### 构造参数

```python
ContextEngineeringRail(
    processors: Union[Tuple[str, BaseModel], List[Tuple[str, BaseModel]], None] = None,
    language: str = "cn",
    preset: bool = True,
)
```

| 参数 | 类型 | 说明 |
|------|------|------|
| `processors` | `List[Tuple[str, BaseModel]]` | 用户自定义 processors，格式为 `[(key, config), ...]` |
| `language` | `str` | 提示词语言，`'cn'` 或 `'en'` |
| `preset` | `bool` | 是否启用预置 processors（默认 `True`） |

### 预置 Processors

| Processor | 配置类 | 默认阈值 |
|-----------|--------|----------|
| `DialogueCompressor` | `DialogueCompressorConfig` | messages_threshold=40, tokens_threshold=100000 |
| `MessageOffloader` | `MessageOffloaderConfig` | messages_threshold=40, tokens_threshold=5000 |

### 自定义 Processors 示例

```python
from openjiuwen.core.context_engine import MessageOffloaderConfig, DialogueCompressorConfig
from openjiuwen.core.foundation.llm import ModelRequestConfig

processors = [
    (
        "DialogueCompressor",
        DialogueCompressorConfig(
            messages_threshold=50,
            tokens_threshold=80000,
            keep_last_round=True,
            model=ModelRequestConfig(model="gpt-4"),
        ),
    ),
    (
        "MessageOffloader",
        MessageOffloaderConfig(
            messages_threshold=30,
            tokens_threshold=10000,
            offload_message_type=["tool", "user"],
        ),
    ),
]

context_rail = ContextEngineeringRail(processors=processors, preset=True)
```

---

## 动态提示词内容对比

### Context 提示词

| 方面 | 当前静态提示词 | ContextEngineeringRail 动态内容 |
|------|----------------|--------------------------------|
| 内容 | 通用 offload 说明 | 读取 `Agent.md`、`Config.md`、`Rules.md` 等配置文件 |
| 记忆 | 无 | 读取 `memory/daily_memory/YYYY-MM-DD.md` |
| 工具 | 无 | 列出当前 ability_manager 中的所有工具 |

### Workspace 提示词

| 方面 | 当前静态提示词 | ContextEngineeringRail 动态内容 |
|------|----------------|--------------------------------|
| 内容 | 固定路径 `WORKSPACE_DIR` | 动态扫描真实目录结构（深度2层） |
| 描述 | 无 | 包含目录/文件描述（如 `memory/` = 记忆目录） |

---

## 能力对照

| 能力项 | 当前手动方式 | 目标 ContextEngineeringRail |
|--------|-------------|----------------------------|
| Context 提示词 | 静态 `_context_prompt()` | 动态读取配置文件 + 每日记忆 + 工具列表 |
| Workspace 提示词 | 静态 `_workspace_prompt()` | 动态扫描目录结构 + 描述 |
| DialogueCompressor | 注释代码 | Rail `init()` 自动配置 |
| MessageOffloader | 注释代码 | Rail `init()` 自动配置 |
| Prompt 注入时机 | `build_identity_prompt()` 时 | `before_model_call()` 每次调用前 |

---

## 验证建议

### 1. Context 提示词验证

- 检查 LLM 请求中是否包含：
  - `Agent.md` 等配置文件内容（如存在）
  - 当日 `memory/daily_memory/YYYY-MM-DD.md` 内容（如存在）
  - 可用工具列表

### 2. Workspace 提示词验证

- 检查 LLM 请求中是否包含：
  - 工作区目录树结构（如 `memory/`、`config/` 等）
  - 各目录的描述信息

### 3. Context Processors 验证

- 确认 `agent.react_agent._config.context_processors` 包含：
  - `DialogueCompressor`
  - `MessageOffloader`
- 长对话场景下验证压缩/卸载是否正常工作

### 4. 热更新验证

- 执行配置热更新
- 确认 ContextEngineeringRail 状态正确
- 验证新的 processors 配置生效（如有）

---

## 注意事项

1. **Prompt 冲突**：移除静态 `_context_prompt()` 后，不要在 `build_identity_prompt()` 中重新添加，否则会与 Rail 动态注入的内容冲突

2. **语言一致性**：`ContextEngineeringRail` 使用 `self.language` 渲染提示词，需与 `build_identity_prompt()` 的语言参数保持一致

3. **processors 覆盖逻辑**：用户配置的 processors 会**替换** preset 中相同 key 的配置，而非合并

4. **Workspace 为空场景**：`before_model_call()` 中当 workspace 为 None 时会移除相关 sections

5. **向后兼容**：如果外部代码直接调用 `build_identity_prompt()` 获取 prompt 字符串，需要注意内容变化
