# 使用 HeartbeatRail 替换 AgentServer 层心跳处理

## 背景

当前 JiuwenClaw 的心跳处理逻辑分散在 Gateway 层和 AgentServer 层：

1. **Gateway 层** (`jiuwenclaw/gateway/heartbeat.py`): 定时触发心跳，构造 `AgentRequest(params={"heartbeat": HEARTBEAT_PROMPT})`
2. **AgentServer 层** (`jiuwenclaw/agentserver/interface.py`): 识别心跳请求，读取 HEARTBEAT.md，拼接 query，短路返回

这种设计导致：
- 心跳语义识别在 Gateway 和 AgentServer 两层分散处理
- HEARTBEAT.md 读取和 prompt 拼接逻辑与业务代码耦合
- 无法利用 DeepAgents SDK 的 Rail 扩展机制

随着 DeepAgents SDK 引入 `HeartbeatRail`，心跳能力可以收编到 Rail 中统一处理。

### 当前现状

**Gateway 层心跳请求构造** (heartbeat.py:180-185):
```python
request = AgentRequest(
    request_id=request_id,
    channel_id=self._config.channel_id,  # "__heartbeat__"
    session_id=session_id,
    params={"heartbeat": HEARTBEAT_PROMPT},  # 旧格式
)
```

**AgentServer 层心跳处理** (interface.py:1029-1077):
```python
if "heartbeat" in request.params:
    heartbeat_md = USER_WORKSPACE_DIR / "workspace" / "HEARTBEAT.md"

    # 1. 检查文件是否存在，不存在则短路返回 HEARTBEAT_OK
    if not os.path.isfile(heartbeat_md):
        return AgentResponse(..., payload={"heartbeat": "HEARTBEAT_OK"})

    # 2. 读取任务列表
    task_list = []
    with open(heartbeat_md, "r", encoding="utf-8") as f:
        ...

    # 3. 无任务则短路返回
    if not task_list:
        return AgentResponse(..., payload={"heartbeat": "HEARTBEAT_OK"})

    # 4. 构造执行提示词，走正常 chat 流程
    query = f"请检查下面用户遗留给你的任务项...\n{task_list}"
    request.params["query"] = query
    # 继续执行后续 chat 处理...
```

### HeartbeatRail 的能力

根据 `openjiuwen` 中的实现，`HeartbeatRail` 已完整支持：

- **`init(agent)`**: 初始化时获取 `system_prompt_builder` 和 `workspace` 路径
- **`before_model_call()`**: 检测 `run_kind == HEARTBEAT`，读取 HEARTBEAT.md，注入 heartbeat prompt section
- **`uninit(agent)`**: 清理 heartbeat prompt section

因此，**AgentServer 层的心跳处理逻辑可以完全移除**，交由 HeartbeatRail 统一处理。

---

## 修改目标

- **标准化请求格式**: Gateway 使用 `params["run"]` 替代 `params["heartbeat"]` 传递心跳语义
- **Rail 化处理**: 通过 HeartbeatRail 的 `before_model_call()` 处理 HEARTBEAT.md 读取和 prompt 注入
- **简化 AgentServer**: 移除 interface.py 中的心跳特殊处理分支，所有请求统一走 chat 流程
- **向后兼容**: 过渡期间支持新旧两种请求格式

---

## 涉及文件

| 文件 | 修改类型 |
|------|----------|
| `jiuwenclaw/gateway/heartbeat.py` | 修改 `_tick()`，使用新 `params["run"]` 格式 |
| `jiuwenclaw/agentserver/deep_agent/interface_deep.py` | 新增 HeartbeatRail 注册 |
| `jiuwenclaw/agentserver/interface.py` | 移除心跳特殊处理逻辑（可选，过渡期内保留向后兼容） |

---

## 具体变更

### 1. Gateway 层请求格式更新 (heartbeat.py:166-185)

**当前实现**:
```python
async def _tick(self) -> None:
    from jiuwenclaw.schema.agent import AgentRequest

    # ...
    request = AgentRequest(
        request_id=request_id,
        channel_id=self._config.channel_id,
        session_id=session_id,
        params={"heartbeat": HEARTBEAT_PROMPT},  # 旧格式
    )
```

**目标实现**:
```python
async def _tick(self) -> None:
    from jiuwenclaw.schema.agent import AgentRequest

    # ...
    request = AgentRequest(
        request_id=request_id,
        channel_id=self._config.channel_id,
        session_id=session_id,
        params={
            "run": {
                "kind": "heartbeat",
                "context": {
                    "reason": "interval",
                    "session_id": session_id,
                }
            }
        },  # 新格式
    )
```

**关键变更**:
- 移除 `HEARTBEAT_PROMPT` 常量引用
- 使用 `params["run"]` 嵌套结构传递心跳语义
- `kind` 标识运行类型为 `"heartbeat"`
- `context` 可携带额外上下文信息

### 2. HeartbeatRail 注册 (interface_deep.py)

**新增导入** (line 32):
```python
from openjiuwen.deepagents.rails import (
    SkillUseRail,
    TaskPlanningRail,
    HeartbeatRail,  # 新增
)
```

**新增成员变量** (line 137-142):
```python
class JiuWenClawDeepAdapter:
    def __init__(self) -> None:
        # ... 现有代码 ...
        self._filesystem_rail: FileSystemRail | None = None
        self._skill_rail: SkillUseRail | None = None
        self._stream_event_rail: JiuClawStreamEventRail | None = None
        self._task_planning_rail: TaskPlanningRail | None = None
        self._tool_prompt_rail: ToolPromptRail | None = None
        self._heartbeat_rail: HeartbeatRail | None = None  # 新增
```

**新增构建方法**:
```python
def _build_heartbeat_rail(self) -> HeartbeatRail | None:
    """Build HeartbeatRail."""
    try:
        heartbeat_rail = HeartbeatRail(
            language=self._resolve_runtime_language(),
        )
        logger.info(
            "[JiuWenClawDeepAdapter] HeartbeatRail create success"
        )
    except Exception as exc:
        logger.warning(
            "[JiuWenClawDeepAdapter] HeartbeatRail create failed: %s",
            exc
        )
        heartbeat_rail = None
    return heartbeat_rail
```

**修改 `_build_agent_rails()`** (line 337-363):
```python
def _build_agent_rails(self, config: dict[str, Any]) -> list[Any]:
    """Build DeepAgent rails consistently for cold start and hot reload."""
    rail_infos = [
        _RailBuildInfo("_filesystem_rail", self._build_filesystem_rail),
        _RailBuildInfo("_skill_rail", self._build_skill_rail, {...}),
        _RailBuildInfo("_stream_event_rail", self._build_stream_event_rail),
        _RailBuildInfo("_tool_prompt_rail", self._build_tool_prompt_rail),
        _RailBuildInfo("_heartbeat_rail", self._build_heartbeat_rail),  # 新增
    ]
    # ...
```

**修改 `_rails_snapshot_for_unregister()`** (line 365-372):
```python
def _rails_snapshot_for_unregister(self) -> list[Any]:
    """与 _build_agent_rails 顺序一致，用于热更新前 unregister."""
    rails = []
    for attr in (
        "_filesystem_rail",
        "_skill_rail",
        "_stream_event_rail",
        "_tool_prompt_rail",
        "_heartbeat_rail",  # 新增
    ):
        r = getattr(self, attr, None)
        if r is not None:
            rails.append(r)
    return rails
```

### 3. openjiuwen 层导出状态

**当前状态** (`openjiuwen/deepagents/rails/__init__.py`):
```python
__all__ = [
    "DeepAgentRail",
    "TaskPlanningRail",
    "SkillUseRail",
    # ... HeartbeatRail 尚未导出
]
```

由于 HeartbeatRail 尚未在 `__init__.py` 中导出，JiuwenClaw 中需要直接从子模块导入：

```python
from openjiuwen.deepagents.rails.heartbeat_rail import HeartbeatRail
```

---

## 能力对照

| 能力项 | 旧 AgentServer 处理 | 新 HeartbeatRail 模式 |
|--------|---------------------|----------------------|
| 心跳语义识别 | `params["heartbeat"]` 字段 | `params["run"]["kind"] == "heartbeat"` |
| HEARTBEAT.md 读取 | AgentServer 层手动读取 | HeartbeatRail 自动读取 |
| Prompt 拼接 | AgentServer 层手动拼接 | HeartbeatRail 注入 prompt section |
| 短路返回 | AgentServer 层直接返回 | 依赖 LLM 返回 HEARTBEAT_OK |
| 代码耦合度 | 高（侵入 interface.py） | 低（Rail 内部处理） |
| 可扩展性 | 差 | 好（Rail 生命周期管理） |

---

## 架构说明

### 旧路径

```text
GatewayHeartbeatService._tick()
    ├── 构造 AgentRequest(params={"heartbeat": HEARTBEAT_PROMPT})
    ├── AgentServerClient.send_request(request)
    │       └── JiuWenClaw.handle_request(request)
    │               ├── if "heartbeat" in request.params:  # 识别
    │               ├── 读取 HEARTBEAT.md
    │               ├── 拼接 query
    │               └── request.params["query"] = query
    │               └── 走正常 chat 流程
    └── 处理响应
```

特点：
- 心跳识别和处理逻辑分散在 Gateway 和 AgentServer 两层
- AgentServer 层需要特殊分支处理心跳请求
- HEARTBEAT.md 读取和 prompt 拼接与业务代码耦合

### 新路径

```text
GatewayHeartbeatService._tick()
    ├── 构造 AgentRequest(params={"run": {"kind": "heartbeat"}})
    ├── AgentServerClient.send_request(request)
    │       └── JiuWenClaw.handle_request(request)
    │               └── 所有请求统一处理，无特殊分支
    │                   ├── DeepAgent.invoke(inputs={..., "run": {...}})
    │                   │       ├── _normalize_inputs() 提取 run_kind
    │                   │       ├── HeartbeatRail.before_model_call()
    │                   │       │       └── 读取 HEARTBEAT.md
    │                   │       │       └── 注入 heartbeat prompt
    │                   │       └── ReActAgent.invoke()
    │                   └── 返回结果
    └── 处理响应
```

特点：
- Gateway 只负责触发，所有心跳处理逻辑收编到 HeartbeatRail
- AgentServer 层无特殊分支，所有请求统一处理
- HEARTBEAT.md 读取和 prompt 注入由 Rail 生命周期管理

---

## 数据流说明

### Run 信息传递流程

```python
# 1. Gateway 构造请求
request = AgentRequest(
    params={
        "run": {
            "kind": "heartbeat",  # RunKind.HEARTBEAT
            "context": {...}
        }
    }
)

# 2. DeepAgent._normalize_inputs() 解析
inputs = {"run": {"kind": "heartbeat", "context": {...}}}
run_kind = RunKind("heartbeat")  # RunKind.HEARTBEAT
run_context = RunContext(**context_data)
invoke_inputs = InvokeInputs(..., run_kind=run_kind, run_context=run_context)

# 3. DeepAgent._to_effective_inputs() 透传
effective_inputs = {
    "query": ...,
    "run_kind": RunKind.HEARTBEAT,
    "run_context": RunContext(...)
}

# 4. ReActAgent.invoke() 放入 ctx.extra
ctx.extra["run_kind"] = RunKind.HEARTBEAT
ctx.extra["run_context"] = RunContext(...)

# 5. HeartbeatRail.before_model_call() 检测
if ctx.extra.get("run_kind") == RunKind.HEARTBEAT:
    # 注入 heartbeat prompt
```

---

## 验证建议

### 1. HeartbeatRail 注册验证
- 启动服务，确认日志输出 `[JiuWenClawDeepAdapter] HeartbeatRail create success`
- 确认 HeartbeatRail 在 `_build_agent_rails()` 中被正确构建

### 2. 心跳请求处理验证
- 触发心跳请求（或等待定时触发）
- 确认 Gateway 使用新 `params["run"]` 格式
- 确认 HeartbeatRail `before_model_call()` 被调用
- 查看日志确认 HEARTBEAT.md 读取状态

### 3. Prompt 注入验证
- HEARTBEAT.md 存在且有内容时，确认 heartbeat prompt 被注入
- HEARTBEAT.md 不存在或为空时，确认返回 HEARTBEAT_OK

### 4. 向后兼容验证
- 旧格式 `params["heartbeat"]` 请求仍能正确处理（过渡期内）
- 普通 chat 请求不受 HeartbeatRail 影响

### 5. 热更新验证
- 执行热更新
- 确认 HeartbeatRail 被正确注销和重新注册
- 后续心跳请求处理正常

---

## 注意事项

### 1. openjiuwen 库导出更新

HeartbeatRail 需要先被导出才能在 JiuwenClaw 中导入使用。

**临时方案**（等待 openjiuwen 更新）：
```python
# 直接从子模块导入
from openjiuwen.deepagents.rails.heartbeat_rail import HeartbeatRail
```

### 2. 短路返回机制变化

旧方案：AgentServer 层直接返回 AgentResponse，不调用 LLM
新方案：依赖 LLM 返回 HEARTBEAT_OK

影响：
- 无任务时也会有一次 LLM 调用（返回 HEARTBEAT_OK）
- 如果希望保持短路行为，可在 HeartbeatRail 中使用 `ctx.request_force_finish()`

### 3. 多会话并发

多个 heartbeat session 同时读取同一文件，当前设计天然支持并发。

---

## 相关代码索引

| 文件 | 职责 |
|------|------|
| `jiuwenclaw/gateway/heartbeat.py` | Gateway 心跳服务，构造 AgentRequest |
| `jiuwenclaw/agentserver/deep_agent/interface_deep.py` | DeepAgents 适配器，注册 HeartbeatRail |
| `jiuwenclaw/agentserver/interface.py` | AgentServer 统一入口（移除心跳特殊处理） |
| `openjiuwen/deepagents/rails/heartbeat_rail.py` | HeartbeatRail 实现 |
| `openjiuwen/deepagents/rails/__init__.py` | Rail 导出列表 |
| `openjiuwen/core/single_agent/rail/base.py` | RunKind / RunContext / InvokeInputs 定义 |
| `openjiuwen/deepagents/deep_agent.py` | DeepAgent 输入处理逻辑 |
