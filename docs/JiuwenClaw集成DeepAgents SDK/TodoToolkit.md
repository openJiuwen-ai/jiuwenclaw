# TodoToolkit 替换为 DeepAgents Todo 工具完成说明

## 背景

JiuwenClaw 原本自维护一套 `TodoToolkit`（6 个工具，Markdown 文件存储），现已迁移至
DeepAgents SDK 提供的 Todo 工具（3 个工具，JSON 文件存储）。本文从**已实现**
角度说明各关键点，便于后续维护或二次调整时快速定位。

**注意**：当前实现是**直接使用** DeepAgents 的 Todo 工具（`create_todos_tool`），
尚未完全替换为 `TaskPlanningRail`。后续如需进一步迁移至 Rail 模式，需额外适配工作。

---

## 一、功能对齐：DeepAgents Todo 是 JiuwenClaw Todo 的超集

迁移后的 3 个工具（`todo_create / todo_list / todo_modify`）在功能上完全覆盖了原有 6 个工具，
能力更强。

### 工具映射

| 原工具 | 新工具 | 说明 |
|---|---|---|
| `todo_create` | `todo_create` | 批量创建，首条自动设为 `IN_PROGRESS` |
| `todo_list` | `todo_list` | 按状态分组格式化展示 |
| `todo_complete` | `todo_modify(action=update, status=completed)` | 功能等价 |
| `todo_insert` | `todo_modify(action=append/insert_after/insert_before)` | 功能等价，且更灵活 |
| `todo_remove` | `todo_modify(action=delete)` | 功能等价 |
| 无 | `todo_modify(action=update, status=in_progress)` | 新增能力 |

### 状态处理

SDK 的 `TodoStatus` 枚举支持 `PENDING / IN_PROGRESS / COMPLETED / CANCELLED`。当前实现：

- 任务中断时：将未完成的 `pending` 和 `in_progress` 任务标记为 `CANCELLED`
- 前端展示时：`CANCELLED` 状态降级映射为 `pending`（与旧版行为一致）

---

## 二、三个关键调用点实现

迁移后 JiuwenClaw 有三处直接操作 Todo 数据的逻辑，均**绕过工具调用、直接读写**
`workspace/session/{session_id}.json`。以下说明各调用点的实现方式和维护注意事项。

### 2.1 刷新任务列表

**位置**：`jiuwenclaw/agentserver/rails/stream_event_rail.py`
→ `after_tool_call` → `_emit_todo_updated()`

**触发时机**：每次 Agent 调用 `todo_create / todo_list / todo_modify` 之后，由 Rail 钩子
自动触发，向 session 写入最新任务列表推送给前端。

**实现方式**（已上线）：

```python
# 按需创建 TodoListTool 实例
todo_tool = TodoListTool(
    operation=self.sys_operation,
    workspace=str(getattr(self.workspace, "workspace_root", "")),
    language=resolve_language(),
)
todo_tool.set_file(session_id)
todos_data = await todo_tool.load_todos()

# 状态映射保留 cancelled → pending 的降级策略
status_mapping = {
    TodoStatus.PENDING: "pending",
    TodoStatus.IN_PROGRESS: "in_progress",
    TodoStatus.COMPLETED: "completed",
    TodoStatus.CANCELLED: "pending",  # 降级为 pending
}
```

**维护注意**：

- 触发条件由模块级常量 `_TODO_TOOL_NAMES` 控制：
  ```python
  _TODO_TOOL_NAMES = frozenset(["todo_create", "todo_list", "todo_modify"])
  ```
- 若 SDK 工具名变更需同步更新，否则刷新会静默失效。
- 使用 `TodoListTool.load_todos()` 加载任务列表，确保工具层的封装性。
- 若 SDK 的 `TodoItem` 模型字段发生变化，只需在此处调整字段组装部分。

### 2.2 任务中断（cancel）

**位置**：`jiuwenclaw/agentserver/interface.py`
→ `process_interrupt()` → `_cancel_pending_todos()`

**触发时机**：用户主动取消当前任务时，将残留的未完成任务标记为 `CANCELLED`。

**实现方式**（已上线）：

```python
async def _cancel_pending_todos(self, session_id: str) -> None:
    """将未完成的 todo 项标记为 cancelled."""
    modify_tool = TodoModifyTool(
        operation=deep_config.sys_operation,
        workspace=str(getattr(deep_config.workspace, "workspace_root", "")),
        language=resolve_language(),
    )
    modify_tool.set_file(session_id)

    todos = await modify_tool.load_todos()
    ids_to_cancel = [
        todo.id for todo in todos
        if todo.status not in {TodoStatus.COMPLETED, TodoStatus.CANCELLED}
    ]
    if ids_to_cancel:
        await modify_tool._cancel_todos(ids_to_cancel, todos)
```

**维护注意**：

- 该方法将非 `completed`/`cancelled` 的任务项标记为 `cancelled`，保留在文件中。
- 与旧版行为一致，任务记录得以保留而非删除。
- 该方法只操作文件，**不触发** `todo.updated` 事件，前端不会收到取消后的状态更新。
  若需要前端同步展示取消状态，需在 cancel 分支额外手动 emit 一次 `todo.updated`。

### 2.3 任务续传（supplement）

**位置**：`jiuwenclaw/agentserver/interface.py`
→ `process_message_stream()` → `_get_pending_todos()`

**触发时机**：检测到当前 session 存在未完成任务时，将待执行任务拼入 prompt，引导 Agent
继续执行。

**实现方式**（已上线）：

```python
async def _get_pending_todos(self, session_id: str) -> list[dict[str, Any]]:
    """获取指定 session 的未完成 todo 列表."""
    todo_tool = TodoListTool(...)
    todo_tool.set_file(session_id)
    todos = await todo_tool.load_todos()

    # 过滤掉已完成的任务
    pending = [
        todo.to_dict() for todo in todos
        if todo.status != TodoStatus.COMPLETED
    ]
    return pending
```

**维护注意**：

- 过滤条件为 `status != TodoStatus.COMPLETED`，即 `pending`、`in_progress` 和 `cancelled`
  均视为未完成并纳入续传 prompt。
- 与旧版行为一致，`cancelled` 任务会被纳入续传（旧版即如此设计）。
- 若未来需要排除 `cancelled` 任务，需在此处修改过滤条件。

---

## 三、前端数据链路与适配层

### 适配层的位置

适配层**唯一在** `stream_event_rail._emit_todo_updated()` 内部，职责是将磁盘 JSON 转换为
前端期望的 `TodoItem[]` 数组后写入 `todo.updated` 事件。

**整个链路传输的始终是结构化 JSON，从未涉及 Markdown 文本**。旧链路同样是发送 JSON，
区别仅在于数据来源从解析 Markdown 变为直接读取 JSON 文件。

### 链路对比

```
旧链路：
  Agent 调用旧工具
    → TodoToolkit 读写 todo.md
    → react_agent._emit_todo_updated()
        → 解析 Markdown → 手动 status_mapping 字段映射
        → OutputSchema(type="todo.updated", payload={"todos": [...]})
    → 前端渲染

新链路：
  Agent 调用新工具（todo_create / todo_list / todo_modify）
    → DeepAgents Todo 工具读写 {session_id}.json
    → stream_event_rail.after_tool_call → _emit_todo_updated()
        → 读 JSON → TodoItem 组装 → 状态映射
        → OutputSchema(type="todo.updated", payload={"todos": [...]})
    → interface._parse_stream_chunk() → 透传（无需改动）
    → web_channel → 前端 useWebSocket.ts → setTodos()（无需改动）
    → TodoList 组件渲染（无需改动）
```

### 字段天然对齐，无需映射

SDK `TodoItem` 的字段与前端 `types/todo.ts` 的 `TodoItem` 接口**完全一致**：

| 后端 `TodoItem` | 前端 `TodoItem` | 说明 |
|---|---|---|
| `id: str` | `id: string` | UUID |
| `content: str` | `content: string` | 任务描述 |
| `activeForm: str` | `activeForm: string` | 进行中时前端展示的描述 |
| `status: TodoStatus` | `status: TodoStatus` | `pending/in_progress/completed` |
| `createdAt: str` | `createdAt: string` | ISO 8601 |
| `updatedAt: str` | `updatedAt: string` | ISO 8601 |

若 SDK 升级导致 `TodoItem` 字段变更，或前端新增展示字段，**只需修改
`_emit_todo_updated()` 内的字段组装部分**，链路中其他所有层均不需要改动。

---

## 四、已实现变更总结

以下是本次迁移涉及的关键代码变更，供后续维护参考。

### 4.1 工具名更新

**位置**：`jiuwenclaw/agentserver/rails/stream_event_rail.py`

`_TODO_TOOL_NAMES` 常量控制 `after_tool_call` 是否触发 `todo.updated` 推送：

```python
_TODO_TOOL_NAMES = frozenset(["todo_create", "todo_list", "todo_modify"])
```

**同步更新**：`interface.py` 中 `TODO_PROMPT` 里向 Agent 介绍工具名称的文本已同步调整。

### 4.2 `CANCELLED` 状态处理

SDK `TodoStatus` 已支持 `CANCELLED` 状态。当前实现：

- **中断时**：将 `pending`/`in_progress` 任务标记为 `CANCELLED`（保留记录）
- **前端展示**：`CANCELLED` 降级映射为 `pending`（与旧版行为一致）
- **续传过滤**：仅排除 `COMPLETED` 任务，`CANCELLED` 任务仍纳入续传（与旧版一致）

### 4.3 关键方法变更

| 方法 | 原实现 | 新实现 |
|---|---|---|
| `_emit_todo_updated()` | 使用旧 `TodoToolkit` 解析 Markdown | 使用 `TodoListTool` 读取 JSON |
| `_cancel_pending_todos()` | 直接删除未完成任务 | 使用 `TodoModifyTool` 标记为 `CANCELLED` |
| `_get_pending_todos()` | 使用旧 `TodoToolkit` 加载任务 | 使用 `TodoListTool` 加载任务 |

---