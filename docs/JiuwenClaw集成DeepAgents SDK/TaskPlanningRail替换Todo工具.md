# 使用 TaskPlanningRail 替换直接 Todo 工具调用

## 背景

当前代码 (`jiuwenclaw/agentserver/deep_agent/interface_deep.py`) 中，`_register_runtime_tools()` 方法采用"双轨制"管理 Todo 工具：

1. **手动注册/清理**：直接调用 `create_todos_tool()` 创建和注册工具
2. **TaskPlanningRail**：同时注册 Rail，由 Rail 的 `init()`/`uninit()` 再次管理工具

这导致工具被重复管理，且手动逻辑与 Rail 的自动化能力重叠。

### 当前现状（line 680-738）

```python
async def _register_runtime_tools(self, session_id: str | None, mode="plan") -> None:
    # 1. 手动清理旧 todo 工具
    for tool in tool_list:
        if tool.name.startswith("todo_"):
            self._instance.ability_manager.remove(tool.name)

    if mode == "plan":
        # 2. 手动创建和注册 todo 工具（与 Rail 重复）
        todo_tools = create_todos_tool(...)
        for tool in todo_tools:
            Runner.resource_mgr.add_tool(tool)
            self._instance.ability_manager.add(tool.card)

        # 3. 注册 TaskPlanningRail（内部也会注册工具）
        if self._task_planning_rail is None:
            self._task_planning_rail = self._build_task_planning_rail()
            await self._instance.register_rail(self._task_planning_rail)
    else:
        # 4. agent 模式：再次手动清理
        for tool in tool_list:
            if tool.name.startswith("todo_"):
                self._instance.ability_manager.remove(tool.name)

        # 5. 注销 TaskPlanningRail（内部也会清理工具）
        if self._task_planning_rail is not None:
            await self._instance.unregister_rail(self._task_planning_rail)
            self._task_planning_rail = None
```

### TaskPlanningRail 的能力

根据 `agent-core` 中的实现，TaskPlanningRail 已完整支持：

- **`init(agent)`** (line 71-102): 自动调用 `create_todos_tool()` 并注册到 `ability_manager` 和 `resource_mgr`
- **`uninit(agent)`** (line 104-118): 自动从 `ability_manager` 和 `resource_mgr` 清理 todo 工具
- **`before_model_call()`** (line 121-132): 自动注入 task planning prompt section

因此，**手动工具管理逻辑可以完全移除**，交由 Rail 统一处理。

---

## 修改目标

- **移除双轨制**：删除手动 `create_todos_tool()` 调用和手动清理逻辑
- **纯 Rail 管理**：仅通过 TaskPlanningRail 的 register/unregister 管理工具和 prompt
- **热更新适配**：TaskPlanningRail 作为运行时动态 Rail，不应在热更新时自动重建

---

## 涉及文件

| 文件 | 修改类型 |
|------|----------|
| `jiuwenclaw/agentserver/deep_agent/interface_deep.py` | 核心修改：简化 `_register_runtime_tools()`，移除手动工具管理 |

---

## 具体变更

### 1. 简化 `_register_runtime_tools()`（line 680-738）

**当前实现**：

```python
async def _register_runtime_tools(
        self,
        session_id: str | None,
        mode="plan",
) -> None:
    """Register per-request tools for current agent execution."""
    if self._instance is None:
        raise RuntimeError("JiuWenClawDeepAdapter 未初始化，请先调用 create_instance()")

    if self._tool_prompt_rail is not None:
        self._tool_prompt_rail.set_mode(mode)

    # 清理旧 todo 工具（手动）
    tool_list = self._instance.ability_manager.list()
    for tool in tool_list:
        if isinstance(tool, ToolCard):
            if tool.name.startswith("todo_"):
                self._instance.ability_manager.remove(tool.name)

    effective_session_id = session_id or "default"

    if mode == "plan":
        self._instance.react_agent.config.prompt_template = \
            [{"role": "system", "content": build_identity_prompt(
                mode="plan",
                language=self._resolve_prompt_language(),
                channel=self._resolve_prompt_channel(session_id),
            )}]
        # 手动创建和注册 todo 工具（待删除）
        todo_tools = create_todos_tool(
            operation=self._instance._deep_config.sys_operation,
            workspace=str(getattr(self._instance._deep_config.workspace, 'workspace_root', '')),
            language=self._resolve_runtime_language()
        )
        for tool in todo_tools:
            Runner.resource_mgr.add_tool(tool)
            self._instance.ability_manager.add(tool.card)
        self._todo_tool_sessions_registered.add(effective_session_id)

        # 注册 TaskPlanningRail（保留）
        if self._task_planning_rail is None:
            self._task_planning_rail = self._build_task_planning_rail()
            if self._task_planning_rail is not None:
                await self._instance.register_rail(self._task_planning_rail)
                logger.info("[JiuWenClawDeepAdapter] TaskPlanningRail registered for plan mode")
    else:
        self._instance.react_agent.config.prompt_template = \
            [{"role": "system", "content": build_identity_prompt(
                mode="agent",
                language=self._resolve_prompt_language(),
                channel=self._resolve_prompt_channel(session_id),
            )}]
        # 手动清理 todo 工具（待删除）
        tool_list = self._instance.ability_manager.list()
        for tool in tool_list:
            if isinstance(tool, ToolCard):
                if tool.name.startswith("todo_"):
                    self._instance.ability_manager.remove(tool.name)

        # 注销 TaskPlanningRail（保留）
        if self._task_planning_rail is not None:
            await self._instance.unregister_rail(self._task_planning_rail)
            self._task_planning_rail = None
            logger.info("[JiuWenClawDeepAdapter] TaskPlanningRail unregistered for agent mode")

    # 内存工具、Web 工具...（保持不变）
```

**目标实现**：

```python
async def _register_runtime_tools(
        self,
        session_id: str | None,
        mode="plan",
) -> None:
    """Register per-request tools for current agent execution."""
    if self._instance is None:
        raise RuntimeError("JiuWenClawDeepAdapter 未初始化，请先调用 create_instance()")

    if self._tool_prompt_rail is not None:
        self._tool_prompt_rail.set_mode(mode)

    effective_session_id = session_id or "default"

    if mode == "plan":
        self._instance.react_agent.config.prompt_template = \
            [{"role": "system", "content": build_identity_prompt(
                mode="plan",
                language=self._resolve_prompt_language(),
                channel=self._resolve_prompt_channel(session_id),
            )}]

        # TaskPlanningRail 自动处理：
        # 1. 注册 todo 工具 (init())
        # 2. 注入 task planning prompt (before_model_call())
        if self._task_planning_rail is None:
            self._task_planning_rail = self._build_task_planning_rail()
            if self._task_planning_rail is not None:
                await self._instance.register_rail(self._task_planning_rail)
                logger.info("[JiuWenClawDeepAdapter] TaskPlanningRail registered for plan mode")

        self._todo_tool_sessions_registered.add(effective_session_id)
    else:
        self._instance.react_agent.config.prompt_template = \
            [{"role": "system", "content": build_identity_prompt(
                mode="agent",
                language=self._resolve_prompt_language(),
                channel=self._resolve_prompt_channel(session_id),
            )}]

        # TaskPlanningRail 自动清理：
        # 1. 注销 todo 工具 (uninit())
        # 2. 移除 task planning prompt
        if self._task_planning_rail is not None:
            await self._instance.unregister_rail(self._task_planning_rail)
            self._task_planning_rail = None
            logger.info("[JiuWenClawDeepAdapter] TaskPlanningRail unregistered for agent mode")

    # 内存工具、Web 工具...（保持不变）
    if not self._memory_tools_registered:
        await init_memory_manager_async(...)
        ...
```

**关键变更点**：

1. **删除手动清理逻辑**（原 line 693-696）：`uninit()` 会自动处理
2. **删除手动创建/注册工具**（原 line 707-714）：`init()` 会自动处理
3. **删除 agent 模式下的手动清理**（原 line 729-733）：`uninit()` 会自动处理

### 2. 热更新处理（已正确实现）

当前代码已正确处理 TaskPlanningRail 的热更新（line 608-650）：

```python
async def reload_agent_config(self) -> None:
    # ...
    old_rails = self._rails_snapshot_for_unregister()  # 不包含 TaskPlanningRail
    for rail in old_rails:
        await self._instance.unregister_rail(rail)

    rails_list = self._get_current_agent_rails(config)  # 不包含 TaskPlanningRail
    # ...
```

TaskPlanningRail 被设计为**运行时动态 Rail**，不包含在：
- `_build_agent_rails()` (line 338) - 冷启动 Rail 列表
- `_rails_snapshot_for_unregister()` (line 365) - 热更新注销列表
- `_get_current_agent_rails()` (line 411) - 热更新注册列表

这是正确的：热更新后，下次请求会根据 `mode` 参数重新决定是否注册 TaskPlanningRail。

---

## 能力对照

| 能力项 | 当前双轨制 | 目标纯 Rail 模式 |
|--------|-----------|-----------------|
| Todo 工具注册 | 手动 `create_todos_tool()` + Rail `init()` | 仅 Rail `init()` |
| Prompt 注入 | 手动设置 `prompt_template` | 仅 Rail `before_model_call()` |
| 工具清理 | 手动 `ability_manager.remove()` + Rail `uninit()` | 仅 Rail `uninit()` |
| 代码复杂度 | 高（重复逻辑） | 低（单一职责） |
| 维护性 | 差（两处修改） | 好（Rail 内部处理） |

---

## 验证建议

1. **Plan 模式验证**：
   - 发送 `mode="plan"` 请求
   - 确认 TaskPlanningRail 被注册（日志输出）
   - 确认 todo 工具可用（由 Rail 自动注册）

2. **Agent 模式验证**：
   - 发送 `mode="agent"` 请求
   - 确认 TaskPlanningRail 被注销（日志输出）
   - 确认 todo 工具不可用（由 Rail 自动清理）

3. **模式切换验证**：
   - Plan -> Agent -> Plan 切换
   - 确认工具注册/注销逻辑正确，无重复或泄漏

4. **热更新验证**：
   - 执行热更新
   - 确认 TaskPlanningRail 状态被重置
   - 后续请求能正常根据 mode 注册 Rail

---

## 注意事项

1. **Session 隔离**：TaskPlanningRail 内部通过 `session_id` 隔离不同会话的 todo 数据，无需外部维护 `_todo_tool_sessions_registered`

2. **重复注册安全**：TaskPlanningRail 的 `init()` 方法在 Rail 实例生命周期内只执行一次，重复 `register_rail()` 不会导致重复注册工具

3. **Prompt 冲突**：迁移后，`prompt_template` 中不再包含 task planning 相关内容，由 Rail 的 `before_model_call()` 动态注入。确保 `build_identity_prompt(mode="plan")` 和 `build_identity_prompt(mode="agent")` 的区别仅限于 identity 部分，不包含 tools 或 todo 相关内容。
