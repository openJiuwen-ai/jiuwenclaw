# VibeSkill Channel 架构与接口说明

> 本文基于 `jiuwenclaw/channel/vibeskill_channel.py`、`vibeskill_session.py`、`vibeskill_file_utils.py`、`gateway/channel_manager.py` 与 `gateway/message_handler.py` 梳理，用于测试串讲 VibeSkill Channel 的架构、协议入口、消息转换和关键验证点。

## 1. 定位

VibeSkill Channel 是 JiuwenClaw 为 VibeSkill 前端提供的专用接入层，频道标识为 `vibeskill`。它继承 `BaseChannel`，但没有复用 `GatewayServer` 的通用 WebSocket 路由，而是自己启动独立的 HTTP Server 与 WebSocket Server。

默认端口：

| 服务 | 默认地址 | 用途 |
|------|----------|------|
| HTTP | `http://{local_ip}:19002/api/v1` | 会话、文件、版本、导出、注册 Skill 等 REST 接口 |
| WebSocket | `ws://{local_ip}:19003/api/v1/messages` | 前端实时对话、SkillDev 流式事件、确认问题回填 |

核心职责：

- 接收 VibeSkill 前端的 REST 与 WebSocket 请求。
- 将前端协议转换成 JiuwenClaw 内部统一 `Message`。
- 通过 `ChannelManager` / `MessageHandler` 转发到 AgentServer。
- 将 AgentServer 的 `chat.*` 与 `skilldev.*` 事件转换回 VibeSkill 前端事件。
- 维护 VibeSkill session 状态、内外 session ID 映射、WebSocket 连接绑定与待确认请求。

## 2. 总体架构

```mermaid
flowchart LR
    FE["VibeSkill 前端"]
    HTTP["VibeSkillChannel HTTP Server<br/>19002 /api/v1"]
    WS["VibeSkillChannel WebSocket Server<br/>19003 /api/v1/messages"]
    STORE["VibeSkillSessionStore<br/>状态与内外 ID 映射"]
    CM["ChannelManager"]
    MH["MessageHandler"]
    AS["AgentServer<br/>chat / skilldev"]

    FE -->|REST| HTTP
    FE <-->|WebSocket| WS
    HTTP --> STORE
    WS --> STORE
    HTTP -->|send_request / create_agent_session / register_skill| CM
    WS -->|deliver_to_message_handler(Message)| CM
    CM --> MH
    MH <-->|E2A| AS
    MH -->|robot_messages| CM
    CM -->|Channel.send| WS
```

启动链路：

1. `app_gateway.py` 创建 `VibeSkillChannel(VibeSkillConfig(channel_id="vibeskill"))`。
2. `ChannelManager.register_channel(vibeskill_channel)` 注册频道，出站响应会通过 `channel.send(msg)` 回到该频道。
3. `vibeskill_channel.start()` 启动独立 HTTP 与 WebSocket 服务。
4. WebSocket 入站消息通过 `self.bus.deliver_to_message_handler(msg)` 送入 `MessageHandler`。
5. AgentServer 响应经 `MessageHandler.consume_robot_messages()` 被 `ChannelManager` 派发回 `VibeSkillChannel.send()`。

## 3. 核心对象

### 3.1 `VibeSkillConfig`

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `enabled` | `True` | 是否启用频道 |
| `channel_id` | `vibeskill` | 注册到 `ChannelManager` 的频道 ID |
| `default_session_id` | `vibeskill_session` | 默认会话 ID，当前主流程主要使用动态 session |
| `http_port` | `19002` | 独立 HTTP 服务端口 |
| `ws_port` | `19003` | 独立 WebSocket 服务端口 |

### 3.2 `VibeSkillSessionStore`

Session Store 由每个 `VibeSkillChannel` 实例独立持有，不是全局单例。

维护两类 ID：

| ID | 来源 | 用途 |
|----|------|------|
| `external_id` | 前端传入的 `sessionID` | 前端可见的会话 ID |
| `internal_id` | Channel 创建或 MessageHandler 创建 | JiuwenClaw / AgentServer 内部处理用 ID |

正常 VibeSkill 建会话流程里需要特别注意：

1. `POST /api/v1/session` 创建本地 session 时，Channel 调用 `get_or_create(external_id=None)`，因此刚创建出的 session 只有 `internal_id`，`external_id` 为空。
2. HTTP 响应给前端的 `sessionID` 使用这个 `internal_id`。
3. 前端后续用该 `sessionID` 建立 WebSocket 并发送 `message.send`。
4. Channel 在处理 `message.send` 时发现入站 `external_session_id` 有值、但 session 里还没有 `external_id`，会调用 `bind_external(session.internal_id, external_session_id)`。
5. 因为正常流程中前端传回来的 `sessionID` 就是第 2 步返回的 `internal_id`，所以最终会形成 `external_id == internal_id` 的绑定。

也就是说，代码结构支持“前端外部 ID”和“内部 ID”两套映射；但 VibeSkill 的正常创建流程不是先由前端提供外部 ID，而是先由 Channel 生成内部 ID，再把它返回给前端，后续再把这个同一个值绑定为 `external_id`。只有前端主动带入已有或自定义 `sessionID` 时，`external_id` 与 `internal_id` 才可能不同。

会话状态：

| 状态 | 含义 | 典型触发 |
|------|------|----------|
| `idle` | 空闲 | 新建、取消、错误恢复、Standard chat final/cancel |
| `busy` | 正在处理 | 收到 `message.send` |
| `completed` | SkillDev 任务完成 | 收到 `skilldev.completed` |
| `retry` | 可重试状态 | 设计上预留 |

会话模式：

| 模式 | 说明 | 入站 `message.send` 转换 |
|------|------|--------------------------|
| `SkillCreate` | 默认模式，用于 Skill 创建/解析/评审/打包 | `ReqMethod.SKILLDEV_CHAT` |
| `Standard` | 标准 JiuwenClaw 对话模式 | `ReqMethod.CHAT_SEND` |

## 4. WebSocket 接口

### 4.1 连接

地址：

```text
ws://127.0.0.1:19003/api/v1/messages?sessionID={sessionID}
```

连接规则：

- 只接受路径 `/api/v1/messages`。
- 可通过 query 参数 `sessionID` 绑定已有会话。
- 建连成功后服务端发送：

```json
{
  "type": "server.connected",
  "properties": {}
}
```

- 服务端每 10 秒发送心跳：

```json
{
  "type": "server.heartbeat",
  "properties": {
    "timestamp": 1710000000000
  }
}
```

### 4.2 入站消息

| 前端消息类型 | 主要字段 | 内部转换 | 说明 |
|--------------|----------|----------|------|
| `message.send` | `sessionID`, `parts`, `model`, `agent`, `agent_id`/`agentId`, `system` | `SKILLDEV_CHAT` 或 `CHAT_SEND` | 核心发送入口，按 session mode 分流；SkillCreate 每轮会先清空 `_message_ctx` |
| `skill.parse` | `sessionID`, `url`, `filename`（可包在 `properties` 内） | `SKILLDEV_PARSE_SKILL` | 导入 skill 压缩包到任务工作区，仅 SkillCreate 模式 |
| `question.replied` | `sessionID`, `requestID`, `answers`（可包在 `properties` 内） | `SKILLDEV_USER_ANSWER` | 回答结构化提问（`skilldev.ask_user_question`） |
| `review.replied` | `sessionID`, `id`, `accept`, `feedback` | `SKILLDEV_CHAT` | 审阅结果仅写入 `params.query`（通过/反馈文案） |
| `desc_optimize.replied` | `sessionID`, `id`, `accept` | `SKILLDEV_RESPOND` | 描述优化确认（`action=skip` 或 `optimize`） |
| `test.replied` | `sessionID`, `id`, `accept` | `SKILLDEV_RESPOND` | 是否进入测试设计（`action=test_design` 或 `skip_tests`） |

`message.send` 的 `parts` 支持：

| part 类型 | 字段 | 转换结果 |
|-----------|------|----------|
| `text` | `text` | 拼接为 `params.query` |
| `file` | `filename`, `url`, `mime`, `resourceType` | 普通文件进入 `params.files`；`resourceType=skill` 进入 `params.skill_packages` |
| `toolDefinition` | `pluginId`, `pluginType`, `toolType`, `toolName`, `description`, `arguments`, `protocol` | 进入 `params.tool_spec_files` |
| `agentDefinition` | `agentId`, `name`, `description`, `parameters` | 进入 `params.agent_definitions` |

SkillCreate 模式 `message.send` 示例：

```json
{
  "type": "message.send",
  "sessionID": "vibeskill_xxx",
  "parts": [
    {"type": "text", "text": "创建一个能计算大数乘法的 skill"}
  ],
  "model": {"providerID": "llm_OpenAI", "modelID": "deepseek-v3-250324"},
  "agent": "coder"
}
```

转换后的内部请求要点：

```text
channel_id = vibeskill
req_method = skilldev.chat
session_id = internal_id
params.task_id = internal_id
params.query = parts 中所有 text 拼接
params.files / skill_packages / tool_spec_files / agent_definitions = 按 part 类型填充
params.agent_id = 可选，来自 message 顶层 agent_id 或 agentId
metadata.vibeskill_original_session_id = external sessionID
is_stream = true
```

Standard 模式 `message.send` 转换要点：

```text
req_method = chat.send
params.query = parts 中所有 text 拼接
params.service_id = external sessionID 或 internal_id
```

`service_id` 用于按 session 做租户隔离，避免 Standard 模式落到共享默认工作区。

### 4.3 出站事件

AgentServer 返回的 `Message(type="event")` 会在 `outbound_intercept()` 中转换成 VibeSkill 前端事件。

SkillDev 事件映射：

| AgentServer 事件 | 前端事件 | 说明 |
|------------------|----------|------|
| `skilldev.skill_name_ready` | `session.updated` | 更新会话标题 |
| `skilldev.agent_thinking` | `message.updated` + `message.part.updated/delta` | reasoning 流式输出（payload 仅 `delta`） |
| `skilldev.agent_output` | `message.updated` + `message.part.updated/delta` | assistant 文本流式输出（payload 仅 `delta`） |
| `skilldev.tool_call` | `message.part.updated` | 工具调用开始，part 类型为 `tool` |
| `skilldev.tool_result` | `message.part.updated` | 工具调用结束，写入 `state.output`（即使 part 已存在也会推送） |
| `skilldev.todos_update` | `todo.updated` | Todo 列表更新 |
| `skilldev.ask_user_question` | `question.asked` | 结构化澄清提问（`questions` 列表） |
| `skilldev.confirm_request` | `review.asked` / `desc_optimize.asked` / `test.asked` | 按 `confirm_type` 分流：`review` / `desc_optimize_confirm` / `skip_tests_confirm` |
| `skilldev.error` | `message.*` + `task.error` + `session.status` | 输出错误文本 part，状态置为 idle |
| `skilldev.completed` | `task.completed` + `session.status` | 状态置为 completed |

通用 chat 事件映射：

| AgentServer 事件 | 前端事件 | 说明 |
|------------------|----------|------|
| `chat.delta` | `message.part.delta` | 标准对话流式增量 |
| `chat.final` | `message.updated` + `task.completed` | 标准对话结束，Standard session 置为 idle |
| `chat.cancel` | 状态置为 idle | 用于取消后的状态恢复 |

消息聚合规则：

- Channel 为每个 session 维护 `_message_ctx`。
- 每次 SkillCreate 模式的 `message.send` 会先调用 `_clear_message_context_for_session`，为新一轮执行分配新的 assistant `message_id`。
- 一个 assistant message 有一个 `message_id` 和多个 `parts`；文本、reasoning、tool 调用分别作为不同 part。
- `skilldev.agent_thinking` / `skilldev.agent_output` 仅携带 `delta`：若与上一条流式种类相同则追加到当前 part 并发送 `message.part.delta`，否则新建 part 并发送 `message.part.updated`。
- 首次需要展示 part 前，会先补发 `message.updated`，后续增量用 `message.part.delta` 或 `message.part.updated`。

## 5. HTTP REST 接口

### 5.1 会话接口

| 方法 | 路径 | 状态 | 说明 |
|------|------|------|------|
| `POST` | `/api/v1/session` | 已实现 | 创建会话，body 可传 `{"mode":"SkillCreate"}` 或 `{"mode":"Standard"}` |
| `GET` | `/api/v1/session/{sessionID}` | 已实现 | 查询 session 状态 |
| `DELETE` | `/api/v1/session/{sessionID}` | 已实现 | 删除本地 session 记录 |
| `POST` | `/api/v1/session/{sessionID}/abort` | 已实现 | 要求北向 WS 仍连接；SkillCreate 经 MessageHandler 派发 `skilldev.cancel`，Standard 派发 `chat.interrupt`（`CHAT_CANCEL`）；无 WS 时返回 400 `websocket_not_connected` |
| `GET` | `/api/v1/session/{sessionID}/messages` | 已实现 | 调用 `skilldev.restore`，将 `timeline_items` 反转为前端消息列表 |
| `POST` | `/api/v1/session/{sessionID}/summarize` | 占位 | 当前返回 `202 {"triggered":true}` |

创建 SkillCreate 会话：

```bash
curl -X POST http://127.0.0.1:19002/api/v1/session \
  -H 'Content-Type: application/json' \
  -d '{"mode":"SkillCreate"}'
```

响应：

```json
{
  "sessionID": "vibeskill_abc123def456",
  "time": {
    "created": 1710000000000,
    "updated": 1710000000000
  },
  "status": {
    "sessionStatus": "idle",
    "sandboxStatus": "none"
  }
}
```

创建 Standard 会话：

```bash
curl -X POST http://127.0.0.1:19002/api/v1/session \
  -H 'Content-Type: application/json' \
  -d '{"mode":"Standard"}'
```

Standard 模式会额外调用 `ChannelManager.create_agent_session()`，再由 `MessageHandler` 发送 `session.create` 到 AgentServer。

### 5.2 文件接口

| 方法 | 路径 | 状态 | 说明 |
|------|------|------|------|
| `GET` | `/api/v1/session/{sessionID}/file` | 已实现 | 调用 `skilldev.file.list`，返回前端 `FileTreeNode[]` |
| `GET` | `/api/v1/session/{sessionID}/file/content?path={path}` | 已实现 | 调用 `skilldev.file.read`，返回文本内容 |
| `GET` | `/api/v1/session/{sessionID}/file/status` | 占位 | 当前返回空数组 |

文件树转换规则：

- 后端 `dir` 转成前端 `type: "directory"`。
- 后端 `file` 转成前端 `type: "file"`。
- `absolute` 统一拼成 `/vibeskill/{task_id}/skill/{relative_path}`。
- `ignored` 固定为 `false`。

文件内容响应：

```json
{
  "type": "text",
  "content": "...",
  "encoding": "utf8",
  "mimeType": "text/plain"
}
```

### 5.3 搜索、VCS 与版本接口

| 方法 | 路径 | 状态 | 当前行为 |
|------|------|------|----------|
| `GET` | `/api/v1/session/{sessionID}/find` | 占位 | 返回空数组 |
| `GET` | `/api/v1/session/{sessionID}/find/file` | 占位 | 返回空数组 |
| `GET` | `/api/v1/session/{sessionID}/vcs` | 占位 | 返回 `{"branch":"main"}` |
| `POST` | `/api/v1/session/{sessionID}/version` | 占位 | 返回随机 `commitHash` 与空文件列表 |
| `GET` | `/api/v1/session/{sessionID}/version` | 占位 | 返回空 commit 列表 |
| `POST` | `/api/v1/session/{sessionID}/version/{commitHash}/rollback` | 占位 | 返回 `{"rolledBack":true}` |
| `GET` | `/api/v1/session/{sessionID}/version/{commitHash}/diff` | 占位 | 返回空 diff stat |

### 5.4 导出与 Skill 注册

| 方法 | 路径 | 状态 | 说明 |
|------|------|------|------|
| `POST` | `/api/v1/session/{sessionID}/export` | 已实现 | 调用 `skilldev.download`，返回导出包信息 |
| `POST` | `/api/v1/session/{sessionID}/register-skill` | 已实现 | 仅 Standard mode 支持，调用 `skills.import_local` 注册远程 skill 包 |

导出响应要求：

```json
{
  "exportId": "xxx",
  "url": "https://...",
  "mimeType": "application/zip",
  "exportedAt": 1710000000000
}
```

注册 Skill 请求：

```json
{
  "skills": [
    {"url": "https://example.com/skill.zip"}
  ]
}
```

注册时 `MessageHandler.register_skill()` 会发送：

```text
req_method = skills.import_local
params.path = skill url
params.force = true
params.service_id = sessionID
```

## 6. 两条主流程

### 6.1 SkillCreate 创建 Skill 流程

```mermaid
sequenceDiagram
    participant FE as VibeSkill 前端
    participant VC as VibeSkillChannel
    participant MH as MessageHandler
    participant AS as AgentServer SkillDev

    FE->>VC: POST /api/v1/session {"mode":"SkillCreate"}
    VC-->>FE: sessionID=vibeskill_xxx
    FE->>VC: WS /api/v1/messages?sessionID=vibeskill_xxx
    VC-->>FE: server.connected
    FE->>VC: message.send(parts/model/agent)
    VC->>MH: Message(req_method=skilldev.chat)
    MH->>AS: E2A skilldev.chat
    AS-->>MH: skilldev.agent_output / tool_call / confirm_request / ...
    MH-->>VC: Message(type=event)
    VC-->>FE: message.* / todo.updated / question.asked / review.asked
    FE->>VC: question.replied / review.replied / test.replied
    VC->>MH: Message(req_method=skilldev.user_answer 或 skilldev.chat/respond)
    AS-->>MH: skilldev.completed
    VC-->>FE: task.completed + session.status completed
```

测试关注点：

- `message.send` 后是否先收到 `session.status busy`。
- 流式输出是否包含 `message.updated` 和后续 part 事件；多轮 `message.send` 是否使用不同的 `messageID`。
- `question.asked`（来自 `skilldev.ask_user_question`）回填 `question.replied` 是否走 `skilldev.user_answer`；`review.replied` 是否走 `skilldev.chat`（审阅结果写入 `query`）；其它确认是否仍走 `skilldev.respond`。
- `skilldev.completed` 是否转换为 `task.completed` 与 `session.status completed`。
- WebSocket 断开时，busy session 是否会恢复 idle，并发送 `skilldev.cancel`。

### 6.2 Standard 标准对话流程

```mermaid
sequenceDiagram
    participant FE as VibeSkill 前端
    participant VC as VibeSkillChannel
    participant CM as ChannelManager
    participant MH as MessageHandler
    participant AS as AgentServer Chat

    FE->>VC: POST /api/v1/session {"mode":"Standard"}
    VC->>CM: create_agent_session(sess_xxx)
    CM->>MH: session.create
    MH->>AS: E2A session.create
    AS-->>MH: session_id
    VC-->>FE: sessionID
    FE->>VC: WS + message.send
    VC->>MH: Message(req_method=chat.send, service_id=sessionID)
    AS-->>MH: chat.delta / chat.final
    VC-->>FE: message.part.delta / message.updated / task.completed
```

测试关注点：

- `POST /api/v1/session {"mode":"Standard"}` 是否返回可用于 WS 连接的 `sessionID`。
- `message.send` 是否走 `chat.send` 而不是 `skilldev.chat`。
- `service_id` 是否按 session 隔离。
- `chat.final` 后 session 是否回到 `idle`，前端是否收到 `task.completed`。
- Standard session 调 `/register-skill` 应成功；SkillCreate session 调用应返回 400。

## 7. 鉴权与错误处理

鉴权开关：

```text
JIUWEN_CLAW_AUTH_ENABLED=true
```

开启后：

- HTTP 入口调用 `check_http_auth()`。
- WebSocket 入口调用 `check_ws_auth()`。
- 当前 `gateway/auth.py` 是 stub 实现，默认直接通过；测试鉴权失败需要替换或扩展该实现。

错误处理：

- WebSocket 收到非法 JSON，会返回：

```json
{"type":"res","id":"","ok":false,"error":"invalid json"}
```

- 未识别 WebSocket 消息类型，会返回：

```json
{"type":"res","id":"","ok":false,"error":"unhandled"}
```

- REST 未匹配路由返回 404：

```json
{"error":"Not found"}
```

- AgentServer 同步请求失败时，文件/导出等接口会返回 4xx 或 502，并带 `error` 字段。

## 8. 清理与取消

WebSocket 断开时，`cleanup(ws)` 会执行：

1. 停止该连接的 heartbeat task。
2. 移除 `ws -> session` 与 `session -> ws` 绑定。
3. 清理该 session 的 `_message_ctx`。
4. 如果 session 仍是 `busy`，本地置为 `idle`。
5. 按 session `mode` 向 MessageHandler 派发取消：SkillCreate → `SKILLDEV_CANCEL`（`task_id` + `session_id`）；Standard → `CHAT_CANCEL` / `chat.interrupt`（`intent=cancel`）。

`POST .../abort` 与断连共用上述逻辑，且要求北向 WS 仍连接。

这部分是测试长任务中断、浏览器刷新、网络断连时必须关注的链路。

## 9. 测试脚本与建议用例

仓库内已有可参考脚本：

| 脚本 | 用途 |
|------|------|
| `scripts/vibeskill/test_ws_whole_reviews.py` | SkillCreate 模式长流程：自动处理 question/review/desc/test 确认 |
| `scripts/vibeskill/test_ws_whole.py` | SkillCreate WebSocket 主流程参考 |
| `scripts/vibeskill/test_ws_multi_tenant_concurrent.py` | 多 session 并发隔离参考 |
| `scripts/vibeskill/test_ws_two_message_send_message_ids.py` | 多轮 `message.send` 的 `messageID` 不复用验证 |
| `scripts/vibeskill/test_http_session_messages_live.py` | `GET /session/{id}/messages` 历史恢复验证 |

建议串讲测试矩阵：

| 场景 | 操作 | 预期 |
|------|------|------|
| 创建默认会话 | `POST /api/v1/session` 空 body 或 `SkillCreate` | 返回 `vibeskill_` 前缀 session，状态 idle |
| 创建 Standard 会话 | `POST /api/v1/session {"mode":"Standard"}` | 返回 `sess_` 风格 session，AgentServer 创建成功 |
| WS 建连 | 连接 `/api/v1/messages?sessionID=...` | 收到 `server.connected` 与周期 heartbeat |
| SkillCreate 发送 | WS 发 `message.send` | 内部走 `skilldev.chat`，前端收到 busy、message/todo/confirm 事件 |
| Standard 发送 | Standard session 发 `message.send` | 内部走 `chat.send`，前端收到 chat 流式与 `task.completed` |
| 确认回填 | 收到 `question.asked` 后发 `question.replied` | `question` 走 `skilldev.user_answer`；`review` 走 `skilldev.chat`；desc/test 走 `skilldev.respond` |
| 历史消息 | `GET /session/{id}/messages` | 经 `skilldev.restore` 返回可回放的前端消息列表 |
| 文件树 | `GET /session/{id}/file` | 返回 `FileTreeNode[]`，目录和文件结构正确 |
| 文件内容 | `GET /session/{id}/file/content?path=...` | 返回 text content |
| 导出 | `POST /session/{id}/export` | 返回 `exportId/url/mimeType/exportedAt` |
| 断连/abort 取消 | busy 时关闭 WS 或 `POST .../abort`（需 WS 在线） | 本地 idle；SkillCreate 派发 `skilldev.cancel`，Standard 派发 `chat.interrupt` |
| 占位接口 | find/vcs/version/summarize | 返回当前占位结构，不应 500 |

## 10. 串讲时的关键结论

- VibeSkill Channel 是独立 HTTP + WebSocket 服务，不走 `GatewayServer` 的 `/tui` 或 `/acp` 路由。
- REST 主要处理会话、文件、导出等同步操作；WebSocket 处理对话和流式事件。
- `SkillCreate` 和 `Standard` 是两套不同后端路径（`skilldev.chat` vs `chat.send`），分流点在 session 的 `mode`。
- 结构化提问（`question.asked`）对应 `skilldev.user_answer`；评审确认（`review.replied`）对应 `skilldev.chat`；描述优化/测试确认仍走 `skilldev.respond`。
- 前端只感知 `sessionID`，Channel 与 MessageHandler 共同处理内外 ID 映射。
- 出站不是简单透传，Channel 会把 `skilldev.*` / `chat.*` 事件聚合成前端需要的 `message.*`、`todo.updated`、`*.asked`、`task.completed` 等事件。
- 当前部分 REST 是占位实现，测试时应区分“已接 AgentServer”的接口和“固定响应”的接口。
