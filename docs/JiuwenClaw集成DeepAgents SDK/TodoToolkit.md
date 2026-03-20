# TodoToolkit 替换为 TaskPlanningRail 说明

## 背景

JiuwenClaw 原本自维护一套 `TodoToolkit`（6 个工具，Markdown 文件存储），需要迁移至
DeepAgents SDK 提供的 `TaskPlanningRail`（3 个工具，JSON 文件存储）。本文从**修改影响**
角度说明各关键点，便于后续维护或二次调整时快速定位。

---

## 一、功能对齐：DeepAgents Todo 是 JiuwenClaw Todo 的超集

迁移后的 3 个工具（`todo_write / todo_read / todo_modify`）在功能上完全覆盖了原有 6 个工具，
能力更强。**唯一缺失的是 `canceled` 状态**。

### 工具映射

| 原工具 | 新工具 | 说明 |
|---|---|---|
| `todo_create` | `todo_write` | 批量创建，首条自动设为 `IN_PROGRESS` |
| `todo_list` | `todo_read` | 按状态分组格式化展示 |
| `todo_complete` | `todo_modify(action=update, status=completed)` | 功能等价 |
| `todo_insert` | `todo_modify(action=append/insert_after/insert_before)` | 功能等价，且更灵活 |
| `todo_remove` | `todo_modify(action=delete)` | 功能等价 |
| 无 | `todo_modify(action=update, status=in_progress)` | 新增能力 |

### 缺失的 `canceled` 状态

SDK 的 `TodoStatus` 枚举只有 `PENDING / IN_PROGRESS / COMPLETED`，没有 `CANCELED`。
当前实现在任务中断时选择直接删除未完成任务项，而非标记为取消状态。

若业务上需要保留取消记录，有两个方向可选：

- **绕过 SDK**：在 JiuwenClaw 侧直接操作 JSON 文件，将 `status` 字段写为自定义
  字符串 `"canceled"`，同步扩展前端 `types/todo.ts` 的 `TodoStatus` 类型定义。
- **扩展 SDK（推荐）**：向 `openjiuwen/` 提交变更，在 `TodoStatus` 新增 `CANCELED` 枚举值。
  影响范围更大，需评估 SDK 其他使用方。

---

## 二、三个关键调用点

迁移后 JiuwenClaw 有三处直接操作 Todo 数据的逻辑，均**绕过工具调用、直接读写**
`workspace/session/{session_id}.json`。修改时需关注各自的职责边界。

### 2.1 刷新任务列表

**位置**：`jiuwenclaw/agentserver/rails/stream_event_rail.py`
→ `after_tool_call` → `_emit_todo_updated()`

**触发时机**：每次 Agent 调用 `todo_write / todo_read / todo_modify` 之后，由 Rail 钩子
自动触发，向 session 写入最新任务列表推送给前端。

**修改时注意**：

- 触发条件由模块级常量 `_TODO_TOOL_NAMES` 控制，若工具名变更需同步更新，否则刷新会
  静默失效。
- 读取文件后通过 `TodoItem.from_dict()` 反序列化，所需的全部前端字段均由 `TodoItem`
  直接提供，无需额外映射。若 SDK 的 `TodoItem` 模型字段发生变化，只需在此处调整字段
  组装部分。

### 2.2 任务中断（cancel）

**位置**：`jiuwenclaw/agentserver/interface.py`
→ `process_interrupt()` → cancel 分支 → `_remove_pending_todos()`

**触发时机**：用户主动取消当前任务时，清理残留的未完成任务项。

**修改时注意**：

- 当前实现**直接删除**非 `completed` 的任务项，而非标记为 `canceled`，原因是 SDK
  层不支持该状态。若后续需引入 `canceled` 语义，需在此方法内修改逻辑，改为更新
  `status` 字段而非删除行。
- 该方法只操作文件，**不触发** `todo.updated` 事件，前端不会收到取消后的状态更新。
  若需要前端同步展示取消状态，需在 cancel 分支额外手动 emit 一次 `todo.updated`。

### 2.3 任务续传（supplement）

**位置**：`jiuwenclaw/agentserver/interface.py`
→ `process_message_stream()` → supplement 分支

**触发时机**：检测到当前 session 存在未完成任务时，将待执行任务拼入 prompt，引导 Agent
继续执行。

**修改时注意**：

- 过滤条件为 `status != TodoStatus.COMPLETED.value`，即 `pending` 和 `in_progress`
  均视为未完成并纳入续传 prompt。
- 若未来引入 `canceled` 状态，**必须在此处明确排除**，否则已取消的任务会被错误地
  引导 Agent 重新执行。

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
  Agent 调用新工具（todo_write / todo_read / todo_modify）
    → TaskPlanningRail 读写 {session_id}.json
    → stream_event_rail.after_tool_call → _emit_todo_updated()
        → 读 JSON → TodoItem.from_dict() → 直接组装，无字段映射
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
