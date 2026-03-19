# SkillUtil 到 SkillRail 迁移说明

## 背景

`jiuwenclaw` 早期通过 `BaseAgent._skill_util` 管理技能。这套机制更适合直接运行单个 `ReActAgent`，但当前主链路已经切到 `DeepAgent` + `create_deep_agent()`，原方案会出现两个问题：

- 技能注册点和实际执行 agent 不一致。
- `EvolutionService` 的挂载目标也跟着变化，不能再默认挂到外层实例上。

`openjiuwen.deepagents.rails.SkillRail` 是为 `DeepAgent` 设计的能力注入方式，负责：

- 扫描 `skills_dir`
- 注册 `read_file` / `code` / `bash` / `list_skill` 等工具
- 在 `before_model_call` 阶段注入技能提示

本次调整的目标，是把技能能力迁移到 `SkillRail`，同时补齐与 `DeepAgent` 运行时结构匹配的 evolution 绑定方式。

## 当前实现状态

### 已生效

- `interface.py` 已通过 `rails=[skill_rail]` 把 `SkillRail` 接入 `DeepAgent`
- 创建 `SkillRail` 时已补齐必填的 `operation` 参数
- `reload_agent_config()` 会重新创建并重新挂载 `SkillRail`
- `EvolutionService` 已重新挂载到真实运行的内部 agent
- `chat.user_answer` 的审批回传已改为路由到正确的 evolution 目标对象

### 预留但未接线

- `react_agent.py` 中与 `SkillRail` 相关的 `_skill_rail` / `set_skill_rail()` / `_get_skill_messages()` 目前仍保留
- 这部分代码当前不会进入主运行链路，不影响现有功能
- 如果后续需要切回自定义 `JiuClawReActAgent` 主导的链路，可以继续复用

## 涉及文件

| 文件 | 说明 |
| --- | --- |
| `jiuwenclaw/agentserver/interface.py` | 主链路接入 `SkillRail`，并修复 evolution 挂载 |
| `jiuwenclaw/agentserver/react_agent.py` | 保留预留接口，当前未接线 |
| `openjiuwen/deepagents/rails/skill_rail.py` | `SkillRail` 的框架实现 |

## 关键变更

### 1. `SkillRail` 创建方式修正

`SkillRail` 的构造函数不是只收 `skills_dir`，还要求传入 `operation: SysOperation`。

当前代码在 `create_instance()` 和 `reload_agent_config()` 中都按下面的方式创建：

```python
sys_operation = (
    Runner.resource_mgr.get_sys_operation(self._sysop_card_id)
    if self._sysop_card_id is not None
    else None
)
if sys_operation is None:
    raise RuntimeError("sys_operation is not available")

skill_rail = SkillRail(
    skills_dir=str(_SKILLS_DIR),
    operation=sys_operation,
    skill_mode=...,
)
```

这样做的原因：

- `SkillRail` 需要借助 `SysOperation` 提供的文件读写和命令执行能力
- 只传 `skills_dir` 会在构造时直接抛 `TypeError`
- 现在即使获取不到 `sys_operation`，也会明确降级并记录 warning，而不是隐式失败

### 2. `DeepAgent` 通过 rails 接收技能能力

技能不再通过旧的 `register_skill()` 挂到 `_skill_util` 上，而是统一通过工厂参数注入：

```python
self._instance = create_deep_agent(
    ...,
    rails=[skill_rail] if skill_rail is not None else None,
)
```

这样可以保证：

- 技能工具注册和 prompt 注入都跟随 `DeepAgent` 生命周期
- `reload_agent_config()` 重建实例时不会丢失技能注入能力
- 技能逻辑与 `DeepAgent` 的 rail 机制保持一致

### 3. `EvolutionService` 挂载目标修正

以前的逻辑默认把 evolution service 挂到 `self._instance`。这在“实例本身就是自定义 `JiuClawReActAgent`”时是合理的，但当前 `self._instance` 已经变成外层 `DeepAgent`，真实处理 evolve 的对象在内部 `react_agent` 上。

现在 `interface.py` 里新增了两个 helper：

```python
def _get_evolution_target(self) -> Any | None:
    ...

def _bind_evolution_service(self, evo_service: EvolutionService) -> bool:
    ...
```

绑定规则是：

- 优先取 `self._instance.react_agent`
- 取不到再退回 `self._instance`

这样做的好处：

- 兼容当前 `DeepAgent` 主链路
- 也兼容未来如果切回直接实例化自定义 agent 的场景

### 4. 热更新时保留 evolution service

`reload_agent_config()` 现在不再尝试从新建的 `self._instance` 上读取 `_evolution_service`，而是使用外层 `JiuWenClaw` 自己持有的 `self._evolution_service`，然后重新绑定到新实例。

这样可避免：

- 新实例刚创建时读不到 evolution service
- 模型热更新后 evolution 能力丢失

### 5. `skill_mode` 支持配置

当前 `SkillRail` 支持通过 `react.skill_mode` 配置技能暴露模式，合法值只有：

- `auto_list`
- `all`

配置示例：

```yaml
react:
  skill_mode: auto_list
```

当前代码在 `create_instance()` 和 `reload_agent_config()` 中都会读取该配置，并传给 `SkillRail`。

行为说明：

- `auto_list`：注册 `list_skill` 工具，让模型按需发现技能，默认使用这个模式
- `all`：将所有技能描述直接注入 system prompt

### 6. `skill_mode` 非法值回退策略

为了避免配置写错导致整套技能能力失效，`interface.py` 中新增了 `JiuWenClaw._resolve_skill_mode()` 进行校验：

- 如果配置值合法，则按配置生效
- 如果配置值非法、为空、或类型不正确，则记录 warning，并自动回退到 `auto_list`

这样即使工作区里的 `config.yaml` 被手工改坏，也不会因为 `SkillRail` 构造抛出 `ValueError` 而退化成 `skill_rail = None`。

## 配置说明

模板配置 `jiuwenclaw/resources/config.yaml` 已新增：

```yaml
react:
  skill_mode: auto_list
```

补充说明：

- 这是模板默认值
- 现有用户工作区中的 `config/config.yaml` 不会因为模板更新而自动补入该字段
- 对于未显式配置 `skill_mode` 的已有环境，运行时仍会走默认值 `auto_list`

## 架构说明

当前主链路如下：

```text
JiuWenClaw
  -> create_deep_agent(...)
  -> DeepAgent
       -> inner react_agent
```

在这条链路里：

- 技能提示由 `SkillRail` 的 rail 回调自动注入
- evolution 能力挂在真实运行的内部 agent 上
- `react_agent.py` 里的 skill prompt 组装逻辑当前不是主路径

## `react_agent.py` 当前定位

`jiuwenclaw/agentserver/react_agent.py` 中以下改动仍然保留：

- `self._skill_rail: Optional[SkillRail] = None`
- `set_skill_rail()`
- `_get_skill_messages()` 读取 `skills_meta`

它们当前的定位是“预留实现”，不是“在线生效逻辑”。保留这部分代码的原因是：

- 便于后续恢复或切换到自定义 `JiuClawReActAgent`
- 方便对比 `SkillUtil` 和 `SkillRail` 两套 prompt 组装方式
- 不影响当前 `DeepAgent` + `SkillRail` 主链路

## 验证建议

1. 启动服务，确认 `create_instance()` 不再因 `SkillRail` 参数缺失报错。
2. 发起一次普通对话，确认 agent 可以正常工作且不影响现有工具链。
3. 观察日志，确认 `SkillRail` 成功创建，或在缺少 `sys_operation` 时输出明确 warning。
4. 分别验证 `skill_mode=auto_list` 和 `skill_mode=all` 的行为是否符合预期。
5. 故意把 `skill_mode` 改成非法值，确认系统记录 warning 并回退到 `auto_list`。
6. 启用 evolution 配置后，验证 `/evolve`、自动演进或审批回传链路能正确命中内部 agent。
7. 调用 `reload_agent_config()`，确认模型热更新后技能和 evolution 都没有丢失。

## 总结

当前迁移已经完成到“主链路可用”状态：

- 技能侧：主链路已切到 `SkillRail`
- 配置侧：`skill_mode` 已支持配置，且非法值会安全回退
- evolution 侧：已修正为面向真实运行目标对象绑定
- 自定义 `react_agent.py`：相关 skill 改动保留，但目前仅作为预留实现
