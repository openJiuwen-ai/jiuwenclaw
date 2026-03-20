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

---

## 四、SDK Todo 工具升级后的适配清单

SDK 对 Todo 工具进行了以下变更，JiuwenClaw 侧需对应调整。

### 变更概览

| 变更项 | 旧版 | 新版 |
|---|---|---|
| `todo_write` 工具名 | `todo_write` | `todo_create` |
| `todo_read` 工具名 | `todo_read` | `todo_list` |
| `todo_modify` 工具名 | `todo_modify` | `todo_modify`（不变） |
| `TodoStatus.CANCELLED` | 不存在 | 新增 `"cancelled"` |
| `todo_modify` cancel action | 不支持 | 新增 `action=cancel` |
| `TodoItem` 模型字段 | 不变 | 不变 |

---

### 4.1 工具名变更 → 刷新触发失效

**影响位置**：`jiuwenclaw/agentserver/rails/stream_event_rail.py`

`_TODO_TOOL_NAMES` 控制 `after_tool_call` 是否触发 `todo.updated` 推送。工具名更新后
若不同步，前端 TodoList 将完全停止实时刷新，且**不会有任何报错**，极难排查。

```python
# 需要更新为
_TODO_TOOL_NAMES = frozenset(["todo_create", "todo_list", "todo_modify"])
```

**同样需要更新**：`interface.py` 中 `TODO_PROMPT` 里向 Agent 介绍工具名称的文本，以及
`_register_runtime_tools()` 中按工具名过滤注销逻辑（若有硬编码工具名）。

---

### 4.2 `CANCELLED` 状态与中断行为适配

#### 背景：`cancelled` 状态的历史情况

旧版 `TodoToolkit`（Markdown 存储）中，`TaskStatus` 枚举含有 `CANCELLED = "cancelled"`，
中断时后端将未完成任务**标记为 `cancelled` 并保留在文件中**（不删除）。

**前端从未完整支持该状态**，但这并非遗漏——而是有意为之的设计决策：

**1. `cancelled` 是纯后端"软删除"标记，LLM 无法主动触发**

旧版 4 个公开工具（`todo_create / todo_complete / todo_insert / todo_remove`）均不含
写入 `cancelled` 的逻辑，LLM 无法通过工具调用触发此状态。`cancelled` 唯一的写入路径
是 `interface.py` 的 cancel interrupt 分支，属于用户主动取消任务时的后置清理操作。

**2. `cancelled` 写入后不发送 `todo.updated` 事件**

cancel 流程结束时，任务的 `asyncio` 协程已被取消，session 的流式输出通道随之关闭，
`session.write_stream()` 不可用。因此 `cancelled` 的写入是**静默落盘**，前端永远
收不到这次状态变更通知。

**3. `_emit_todo_updated` 有意将 `cancelled` 映射回 `pending`**

旧版 `react_agent._emit_todo_updated()` 中的 `status_mapping` 写死了以下映射：

```python
status_mapping = {
    "waiting":   "pending",
    "running":   "in_progress",
    "completed": "completed",
    "cancelled": "pending",   # 有意降级为 pending
}
```

这说明前端不感知 `cancelled` 是**设计上的选择**，而非实现缺口：`cancelled` 只是
后端用于区分"已完成"与"被放弃但保留记录"的内部语义标记，不打算暴露给用户。

**4. supplement 过滤 `cancelled` 任务会被纳入续传**

旧版 supplement 续传的过滤条件是 `status != "completed"`，**`cancelled` 任务会被
纳入续传 prompt**，LLM 看到后会重新执行已被用户取消的任务。需要确认 cancel 的本意是否是
取消这些任务，supplement 并未遵守这一约定。这是可能是旧版代码里的一个既有 bug，
同时因为 `cancelled` 写入后不触发 `todo.updated`，前端无法感知，用户难以察觉。

---

综上，**`cancelled` 是一个"半途而废"的实现**：后端写了但前端不展示（有意），
`_emit_todo_updated` 主动抹平了差异，supplement 则因此留下了一个静默 bug。

SDK 升级正式补齐了后端侧的支持（`TodoStatus.CANCELLED`），这是同步修复前端展示
和 supplement 过滤这两个历史问题的合适时机。

#### 4.2.1 任务中断：恢复旧版标记行为

**影响位置**：`jiuwenclaw/agentserver/interface.py` → `_remove_pending_todos()`

当前迁移后的实现**直接删除**未完成任务项，与旧版行为不一致：旧版是标记为
`cancelled` 并保留记录。SDK 升级后 `TodoStatus.CANCELLED` 正式可用，应改回标记语义。

改为标记后，需要额外**向前端 emit 一次 `todo.updated`**。该方法目前只操作文件，不
触发任何推送——用户取消后前端 TodoList 不会刷新，仍显示旧的进行中状态。

```python
# 恢复旧版语义：标记 cancelled 并保留，而非删除
# 1. 读取 JSON，将 pending/in_progress 的 status 改为 "cancelled"，更新 updatedAt
# 2. 写回文件（保留全部记录）
# 3. caller 需在此之后手动调用 _emit_todo_updated(session, session_id) 通知前端
```

#### 4.2.2 任务续传：排除 `cancelled`

**影响位置**：`jiuwenclaw/agentserver/interface.py` → `process_message_stream()` supplement 分支

中断行为改为标记 `cancelled` 后，续传的过滤条件必须同步更新，否则已取消的任务会被
Agent 重新执行：

```python
# 当前（只排除 completed，存在漏洞）
pending_todos = [d for d in data if d.get("status") != TodoStatus.COMPLETED.value]

# 修改后（同时排除 cancelled）
_DONE_STATUSES = {TodoStatus.COMPLETED.value, TodoStatus.CANCELLED.value}
pending_todos = [d for d in data if d.get("status") not in _DONE_STATUSES]
```

**注意**：即使中断行为仍保持删除语义（不改 4.2.1），这里也应预防性地加上排除
`cancelled` 的逻辑，避免将来行为变更后引发静默 Bug。

#### 4.2.3 前端：补齐既有缺口

**影响位置**：`jiuwenclaw/web/src/types/todo.ts`、`TodoList/index.tsx`、`TodoItem.tsx`

这是修复前后端既有缺口，与 SDK 升级无直接因果关系，但 SDK 升级后后端会正式写入
`"cancelled"` 值，前端若仍不处理，会在 TypeScript 编译时报类型错误。

```typescript
// types/todo.ts — 补充枚举值
export type TodoStatus = 'pending' | 'in_progress' | 'completed' | 'cancelled';
```

`todoStore.ts` 无需修改，类型联动后自动生效。

`TodoList/index.tsx` 参照 `completed` 分组增加 `cancelled` 分组渲染；`TodoItem.tsx`
的 `getStatusIcon()` 补充 `cancelled` 分支，提供对应图标样式。

---

### 4.3 `_emit_todo_updated` 无需改动

`TodoItem.from_dict()` 内部通过 `TodoStatus(data["status"])` 反序列化状态值。SDK 升级后
`TodoStatus` 新增 `CANCELLED`，`from_dict()` 会自动正确解析 `"cancelled"` 字符串，字段
组装代码无需任何修改。

---

### 变更适配汇总

| 位置 | 变更内容 | 不改的后果 |
|---|---|---|
| `stream_event_rail.py` `_TODO_TOOL_NAMES` | 更新工具名 | 前端 TodoList 停止实时刷新 |
| `interface.py` `TODO_PROMPT` | 更新工具名描述 | Agent 调用不存在的工具名，报错 |
| `interface.py` supplement 过滤条件 | 排除 `cancelled` | 已取消任务被 Agent 重新执行 |
| `web/src/types/todo.ts` + `TodoItem.tsx` | 补充 `cancelled` 类型和渲染 | TS 类型报错，取消状态对用户隐藏 |
| `interface.py` `_remove_pending_todos()` | 改为标记取消 + emit | 与旧版行为不一致，中断后前端不刷新 |
| `TodoList/index.tsx` | 新增 cancelled 分组 | 取消状态任务不分组展示（但有图标） |
