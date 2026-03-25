# PromptBuilder 与 Rails 提示词分层说明

## 背景

JiuwenClaw 接入 DeepAgents 后，`DeepAgentConfig.system_prompt` 不再只是简单的字符串透传。
在 `DeepAgent` 初始化阶段，这段字符串会被包装为一个名为 `identity` 的 `PromptSection`，然后再与各类
rail 动态追加的 section 共同组成最终 system prompt。

如果继续把 JiuwenClaw 本地“完整 system prompt”直接塞进 `system_prompt`，会带来两个问题：

- `identity` 和动态 rail 注入的职责边界不清晰
- `skills`、`todo` 等内容可能与 rail 自己追加的 section 重叠

因此本次调整的目标是：

- 保留 JiuwenClaw 自己的 identity-like baseline
- 将 `skills`、`todo` 这类动态能力提示交还给 DeepAgents rails
- 让 prompt 的语言选择与运行时语言保持一致

---

## 当前实现状态

### 已生效

- `interface.py` 不再直接调用完整 `build_system_prompt()` 作为 `system_prompt`
- 新增 `build_identity_prompt(mode, language, channel)` 作为 DeepAgent 的 baseline prompt 入口
- `build_identity_prompt()` 只保留 identity-like sections
- `build_system_prompt_sections(mode, channel, language)` 现在要求显式传入 `language`
- `build_system_prompt_sections()` 中已移除本地 `skills` 和 `todo` section
- `SkillRail` 与 `TaskPlanningRail` 统一通过运行时语言初始化
- `interface.py` 中 `_build_managed_system_prompt()` 已删除，调用点直接使用 `build_identity_prompt(...)`
- 本地 `PromptSection.priority` 已统一收口为 `PromptPriority(IntEnum)`，不再散落裸数字

### 当前边界

- 本地 PromptBuilder 负责：
  - `start`
  - `context`
  - `tools`
  - `workspace`
  - `memory`
  - `principle`
  - `tone`
- DeepAgents rails 负责：
  - `skills`
  - `todo`
  - `task_tool`
  - `tool_navigation`
  - `progressive_tool_rules`

---

## 涉及文件

| 文件 | 说明 |
| --- | --- |
| `jiuwenclaw/agentserver/prompt_builder.py` | 本地 prompt section 定义与 identity baseline 组装入口 |
| `jiuwenclaw/agentserver/interface.py` | DeepAgent 创建、rails 初始化、运行时 prompt 切换 |
| `openjiuwen/deepagents/deep_agent.py` | `system_prompt` 包装为 `identity` section 的入口 |
| `openjiuwen/deepagents/rails/skill_rail.py` | 动态注入 `skills` section |
| `openjiuwen/deepagents/rails/task_planning_rail.py` | 动态注入 `todo` section |
| `openjiuwen/deepagents/rails/subagent_rail.py` | 动态注入 `task_tool` section |
| `openjiuwen/deepagents/rails/progressive_tool_rail.py` | 动态注入工具导航与 progressive tool rules |

---

## 关键变更

### 1. `system_prompt` 改为使用 identity baseline

当前 `interface.py` 中创建 `DeepAgent` 和运行时切换 prompt 时，统一调用：

```python
build_identity_prompt(
    mode=...,
    language=self._resolve_prompt_language(),
    channel=self._resolve_prompt_channel(session_id),
)
```

这样做的目的不是“生成完整 prompt”，而是只生成应该放进 `identity` 的基线内容。

这部分基线会在 `DeepAgent` 初始化时被包装成：

```python
PromptSection(
    name="identity",
    content={"cn": cfg.system_prompt, "en": cfg.system_prompt},
)
```

因此这里传入什么，最终就会整体作为 `identity` section 的内容。

### 2. 本地 `skills` / `todo` 从 PromptBuilder 中移除

当前 `build_system_prompt_sections()` 已不再追加：

- `_skills_prompt(language)`
- `_todo_prompt(language)`

原因是这两块已经由 rails 在 `before_model_call` 阶段动态注入：

- `SkillRail` 注入 `skills`
- `TaskPlanningRail` 注入 `todo`

如果本地 PromptBuilder 再放一份，就会出现主题重叠，甚至规则冲突。

### 3. `build_identity_prompt()` 只保留 identity-like sections

当前 `build_identity_prompt()` 只组装以下 section：

- `start`
- `context`
- `tools`
- `workspace`
- `memory`
- `principle`
- `tone`

不再包含：

- `skills`
- `todo`
- `time`
- `response`
- `safety`

这样拆分的依据有两个：

- JiuwenClaw 本地这几块更接近“长期稳定的助手基线”
- DeepAgents 的 `SystemPromptBuilder` 已将 `identity`、`safety`、`skills`、`tools`、`task_tool`、`runtime` 视为不同 section 类型

### 3.1 `build_system_prompt_sections()` 语言参数改为必传

当前 `build_system_prompt_sections()` 的签名已经收紧为：

```python
build_system_prompt_sections(mode: str, channel: str, language: str)
```

不再保留 `language="cn"` 这一层默认值。这样做的原因是：

- 上层调用已经都会显式传语言
- `build_system_prompt()` / `build_identity_prompt()` 内部已经负责语言归一化
- 避免后续误以为该函数可以在未明确语言来源时被直接调用

### 3.2 section priority 收口为 `PromptPriority`

当前本地 `prompt_builder.py` 中各 section 的 priority 不再使用裸数字，而是统一通过：

```python
class PromptPriority(IntEnum):
    ...
```

来表达 section 顺序，例如：

```python
priority=PromptPriority.START
priority=PromptPriority.MEMORY
priority=PromptPriority.TONE
```

这样做的主要目的有两个：

- 避免 magic number 分散在各个 section 定义里
- 降低后续调整 section 顺序时被随手改坏的概率

### 4. `SkillRail` / `TaskPlanningRail` 统一使用运行时语言

本次没有直接修改 rail 模块默认值，而是在 `interface.py` 的创建调用点补齐语言参数：

```python
SkillRail(
    ...,
    language=self._resolve_runtime_language(),
)

TaskPlanningRail(
    language=self._resolve_runtime_language(),
)
```

这样可以保证：

- 本地 identity baseline 的语言
- rail 注入的 `skills` / `todo` section 语言
- `create_todos_tool(...)` 的工具语言

三者保持一致。

### 5. 删除 `_build_managed_system_prompt()` 中间包装

此前 `interface.py` 中存在一层 `_build_managed_system_prompt()` helper。
当前这层已经删除，原因是它只做简单透传，无法提供额外抽象价值，反而会隐藏调用点真实依赖的是：

- prompt 语言
- prompt channel

删除后，调用点直接写 `build_identity_prompt(...)`，更利于理解系统 prompt 的来源。

---

## `_resolve_prompt_language()` 与 `_resolve_runtime_language()` 的分工

当前 `interface.py` 中保留了两层语言解析：

### `_resolve_prompt_language()`

职责是读取配置原值：

- 从 `get_config()` 中读取 `preferred_language`
- 默认值为 `"zh"`
- 返回配置态值，例如 `"zh"` 或 `"en"`

### `_resolve_runtime_language()`

职责是把配置原值转换为运行时代码真正使用的标准值：

- 内部调用 `resolve_language(...)`
- 将 `"zh"` 规范化为 `"cn"`
- 返回 `"cn"` 或 `"en"`

当前使用规则是：

- Prompt builder 入口使用 `_resolve_prompt_language()`
- rails、todo 工具、`DeepAgentConfig.language` 使用 `_resolve_runtime_language()`

这样可以同时兼容配置侧输入格式和运行时内部标准语言值。

---

## 当前 prompt 注入链路

### 冷启动

```text
interface.py
  -> build_identity_prompt(language, channel)
  -> create_deep_agent(system_prompt=...)
  -> DeepAgent
       -> 将 system_prompt 包装为 identity section
       -> rails 在 before_model_call 继续追加 skills / todo / task_tool ...
```

### 运行时切换

```text
interface.py::_register_runtime_tools(session_id, mode)
  -> 重新设置 react_agent.config.prompt_template
  -> 使用当前 session_id 解析 channel
  -> 使用 build_identity_prompt(language, channel)
```

保留运行时重设 `prompt_template` 的原因是：

- 同一 `DeepAgent` 实例会复用
- 不同 session/channel 的 prompt baseline 可能不同
- 因此不能只依赖创建实例时的初始值

---

## rails 当前会额外注入哪些 prompt

### `SkillRail`

- 注入 section：`skills`
- 时机：`before_model_call`

### `TaskPlanningRail`

- 注入 section：`todo`
- 时机：`before_model_call`
- 额外行为：在 `after_tool_call` 中追加一条 progress reminder user message

### `SubAgentRail`

- 注入 section：`task_tool`
- 时机：`before_model_call`

### `ProgressiveToolRail`

- 注入 section：
  - `tool_navigation`
  - `progressive_tool_rules`
- 时机：`before_model_call`

---

## 当前设计结论

本次调整后，JiuwenClaw 与 DeepAgents 的提示词职责边界如下：

- JiuwenClaw 本地维护“助手基线人格与运行环境说明”
- DeepAgents rails 维护“动态能力提示与执行期规则”

这个边界的直接好处是：

- identity baseline 更稳定
- `skills` / `todo` 不再双重维护
- rail 负责的内容可以继续独立演进
- prompt 语言在 baseline、rails、tools 三层保持一致

---

## 验证建议

1. 启动服务，确认 `interface.py` 与 `prompt_builder.py` 均可正常导入。
2. 分别用中文和英文配置启动，确认 `SkillRail` / `TaskPlanningRail` 注入内容语言正确。
3. 在 `plan` 模式下发起复杂任务，确认 `todo` 规则仅来自 rail，而非本地 PromptBuilder。
4. 在 `agent` 模式下发起技能相关请求，确认技能说明由 `SkillRail` 动态注入。
5. 切换不同 `session_id` 对话，确认运行时覆盖 `prompt_template` 后系统行为正常。

---

## 总结

当前 prompt 结构已经从“本地全量字符串 prompt”收敛为“两层注入”：

- 第一层：`build_identity_prompt()` 提供 identity baseline
- 第二层：DeepAgents rails 追加动态 section

这使得 JiuwenClaw 本地 prompt 组装逻辑与 DeepAgents 原生的 section/rail 机制更接近，也为后续继续拆分 `tools`、`runtime`、`safety` 等 section 留出了空间。
