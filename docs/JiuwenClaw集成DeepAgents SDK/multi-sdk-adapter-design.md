# JiuWenClaw 多 SDK 适配架构设计

## 概述

本文档描述了 JiuWenClaw 的多 SDK 适配架构改造，实现了 SDK 可插拔、职责分离、动态 Rail 管理等关键特性。

## 设计目标

1. **SDK 可插拔**：支持通过环境变量切换不同的 SDK（DeepAgents、ReAct、Pi）
2. **职责分离**：Facade 层负责公共编排，Adapter 层负责 SDK 专属逻辑
3. **动态 Rail 管理**：根据运行时模式动态注册/去注册 Rail
4. **向后兼容**：对外 API 保持 100% 向后兼容

## 架构设计

### 整体架构图

```
┌─────────────────────────────────────────────────────────┐
│                    interface.py (Facade)                │
│  - 统一入口 API                                          │
│  - SDK 工厂路由                                          │
│  - skill_manager (创建并管理)                            │
│  - session_manager (创建并管理)                          │
│  - 公共编排（Skills 路由、heartbeat、流式包装）           │
└───────────────────────┬─────────────────────────────────┘
                        │
          ┌─────────────┼─────────────┐
          │             │             │
          ▼             ▼             ▼
┌───────────────┐ ┌───────────┐ ┌─────────────────────┐
│session_manager│ │skill_mgr  │ │     Adapters        │
│ - session队列 │ │ - skills  │ ├─────────────────────┤
│ - 任务提交    │ │ - hooks   │ │ interface_react.py  │
│ - 任务取消    │ │           │ │ interface_deep.py   │
└───────────────┘ └───────────┘ └─────────────────────┘
```

### 层次说明

| 层次 | 文件 | 职责 |
|------|------|------|
| **Facade 层** | `interface.py` | 统一入口、SDK 工厂路由、公共编排 |
| **Manager 层** | `session_manager.py`<br>`skill_manager.py` | Session 管理、Skills 管理 |
| **Adapter 层** | `interface_react.py`<br>`interface_deep.py` | SDK 专属逻辑实现 |
| **Protocol 层** | `agent_adapters.py` | 定义适配器接口协议 |

## 核心组件

### 1. AgentAdapter Protocol

定义所有 SDK 适配器必须实现的接口：

```python
@runtime_checkable
class AgentAdapter(Protocol):
    async def create_instance(self, config: dict[str, Any] | None = None) -> None: ...
    async def reload_agent_config(self) -> None: ...
    async def process_message_impl(self, request: AgentRequest, inputs: dict[str, Any]) -> AgentResponse: ...
    async def process_message_stream_impl(self, request: AgentRequest, inputs: dict[str, Any]) -> AsyncIterator[AgentResponseChunk]: ...
    async def process_interrupt(self, request: AgentRequest) -> AgentResponse: ...
    async def handle_user_answer(self, request: AgentRequest) -> AgentResponse: ...
```

### 2. SDK 工厂路由

通过环境变量选择 SDK：

```python
# 环境变量: JIUWENCLAW_AGENT_SDK
# 可选值: deepagents (默认), react, pi (保留)

def resolve_sdk_choice() -> str:
    raw = os.getenv("JIUWENCLAW_AGENT_SDK", "").strip().lower()
    if not raw:
        return "deepagents"  # 默认
    if raw in {"deepagents", "react", "pi"}:
        return raw
    return "deepagents"  # 未知值回退

def create_adapter(sdk: str | None = None) -> AgentAdapter:
    sdk_name = sdk or resolve_sdk_choice()
    if sdk_name == "deepagents":
        return JiuWenClawDeepAdapter()
    if sdk_name == "react":
        return JiuWenClawReactAdapter()
    ...
```

### 3. SessionManager

封装 Session 任务队列管理：

```python
class SessionManager:
    def __init__(self):
        self._session_tasks: dict[str, asyncio.Task] = {}
        self._session_priorities: dict[str, int] = {}
        self._session_queues: dict[str, asyncio.PriorityQueue] = {}
        self._session_processors: dict[str, asyncio.Task] = {}

    async def submit_task(self, session_id: str, task_func: Callable[[], Awaitable[Any]]) -> None: ...
    async def submit_and_wait(self, session_id: str, task_func: Callable[[], Awaitable[Any]]) -> Any: ...
    async def cancel_session_task(self, session_id: str, log_msg_prefix: str = "") -> None: ...
```

### 4. Facade 层 (JiuWenClaw)

统一入口，协调各组件：

```python
class JiuWenClaw:
    def __init__(self):
        self._adapter: AgentAdapter | None = None
        self._sdk_name: str | None = None
        self._skill_manager = SkillManager()
        self._session_manager = SessionManager()

    async def create_instance(self, config: dict[str, Any] | None = None) -> None: ...
    async def reload_agent_config(self) -> None: ...
    async def process_message(self, request: AgentRequest) -> AgentResponse: ...
    async def process_message_stream(self, request: AgentRequest) -> AsyncIterator[AgentResponseChunk]: ...
```

## 动态 Rail 管理

### TaskPlanningRail 动态注册

根据运行时模式动态管理 `TaskPlanningRail`：

```python
async def _register_runtime_tools(self, session_id: str | None, mode="plan") -> None:
    if mode == "plan":
        # plan 模式：创建并注册（不重复注册）
        if self._task_planning_rail is None:
            self._task_planning_rail = self._build_task_planning_rail()
            if self._task_planning_rail is not None:
                await self._instance.register_rail(self._task_planning_rail)
    else:
        # agent 模式：去注册并清理
        if self._task_planning_rail is not None:
            await self._instance.unregister_rail(self._task_planning_rail)
            self._task_planning_rail = None
```

### 静态 Rails vs 动态 Rails

| 类型 | Rails | 管理方式 |
|------|-------|----------|
| **静态 Rails** | `FileSystemRail`<br>`SkillUseRail`<br>`JiuClawStreamEventRail`<br>`ToolPromptRail` | 在 `create_instance` 时注册，热更新时重新注册 |
| **动态 Rails** | `TaskPlanningRail` | 根据运行时模式动态注册/去注册 |

## 公共编排逻辑

### 1. Session 队列管理

- 多 session 并发执行
- 同 session 内任务按先进后出顺序执行
- 新任务优先级更高

### 2. Skills 路由

```python
_SKILL_ROUTES: dict[ReqMethod, str] = {
    ReqMethod.SKILLS_LIST: "handle_skills_list",
    ReqMethod.SKILLS_INSTALLED: "handle_skills_installed",
    ReqMethod.SKILLS_GET: "handle_skills_get",
    ReqMethod.SKILLS_INSTALL: "handle_skills_install",
    ReqMethod.SKILLS_UNINSTALL: "handle_skills_uninstall",
    ...
}
```

### 3. Heartbeat 处理

检查 `HEARTBEAT.md` 文件，执行遗留任务。

### 4. 流式响应包装

统一处理流式响应，记录历史记录。

## 使用方式

### 默认使用 DeepAgents SDK

```python
from jiuwenclaw.agentserver.interface import JiuWenClaw

agent = JiuWenClaw()
await agent.create_instance()
response = await agent.process_message(request)
```

### 切换到 ReAct SDK

```bash
# 设置环境变量
export JIUWENCLAW_AGENT_SDK=react
```

```python
from jiuwenclaw.agentserver.interface import JiuWenClaw

agent = JiuWenClaw()  # 自动使用 ReAct SDK
await agent.create_instance()
response = await agent.process_message(request)
```

### 切换到 DeepAgents SDK

```bash
export JIUWENCLAW_AGENT_SDK=deepagents
```

## 文件结构

```
jiuwenclaw/agentserver/
├── interface.py                 # Facade 层 - 统一入口
├── agent_adapters.py            # Protocol 层 - 适配器协议
├── session_manager.py           # Session 管理器
├── skill_manager.py             # Skills 管理器
├── interface_react.py           # ReAct 适配器
└── deep_agent/
    └── interface_deep.py        # DeepAgents 适配器
```

## 关键改进

### 1. 职责分离清晰

- **Facade**：公共编排、路由、管理
- **Adapter**：SDK 专属逻辑、Rail 管理、工具注册

### 2. SDK 可插拔

- 通过环境变量切换 SDK
- 工厂模式创建适配器
- Protocol 定义统一接口

### 3. 避免重复注册

- TaskPlanningRail 动态管理
- 已注册的 Rail 不重复注册
- 去注册后清理引用

### 4. 正确清理状态

- 去注册后将引用设为 None
- 下次切换模式时重新创建
- 避免状态残留

### 5. 代码更简洁

- 移除冗余的向后兼容别名
- 统一入口，避免混淆
- 清晰的层次结构

## 向后兼容性

### API 兼容

所有对外 API 保持 100% 向后兼容：

```python
# 旧代码仍然可以工作
from jiuwenclaw.agentserver.interface import JiuWenClaw

agent = JiuWenClaw()
await agent.create_instance()
await agent.reload_agent_config()
response = await agent.process_message(request)
```

### 移除的别名

以下向后兼容别名已移除（不应直接使用）：

```python
# 已移除，不应使用
# from jiuwenclaw.agentserver.interface_react import JiuWenClaw
# from jiuwenclaw.agentserver.deep_agent.interface_deep import JiuWenClaw
```

## 测试建议

### 1. SDK 切换测试

```python
# 测试 DeepAgents SDK
os.environ["JIUWENCLAW_AGENT_SDK"] = "deepagents"
agent = JiuWenClaw()
await agent.create_instance()

# 测试 ReAct SDK
os.environ["JIUWENCLAW_AGENT_SDK"] = "react"
agent = JiuWenClaw()
await agent.create_instance()
```

### 2. 动态 Rail 测试

```python
# 测试 plan 模式注册
await adapter._register_runtime_tools(session_id, mode="plan")
assert adapter._task_planning_rail is not None

# 测试 agent 模式去注册
await adapter._register_runtime_tools(session_id, mode="agent")
assert adapter._task_planning_rail is None
```

### 3. Session 管理测试

```python
# 测试任务提交
result = await session_manager.submit_and_wait(session_id, task_func)

# 测试任务取消
await session_manager.cancel_session_task(session_id)
```

## 未来扩展

### 1. 新增 SDK 支持

1. 创建新的适配器文件 `interface_pi.py`
2. 实现 `AgentAdapter` 协议
3. 在 `create_adapter` 工厂函数中添加分支
4. 设置环境变量 `JIUWENCLAW_AGENT_SDK=pi`

### 2. 新增动态 Rail

1. 在 `__init__` 中添加 Rail 属性
2. 在 `_register_runtime_tools` 中根据条件注册/去注册
3. 去注册后设置为 None

### 3. 新增公共编排逻辑

在 `interface.py` 中添加新的处理方法，所有 SDK 都能复用。

## 总结

本次改造实现了：

1. ✅ SDK 可插拔架构
2. ✅ 职责分离清晰
3. ✅ 动态 Rail 管理
4. ✅ 向后兼容
5. ✅ 代码更简洁

架构设计遵循了单一职责原则、开闭原则、依赖倒置原则，为未来的扩展和维护奠定了良好的基础。