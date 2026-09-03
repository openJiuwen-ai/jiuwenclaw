# Agent Server 入口、协议与 Handler 模块设计说明书

> 本文是正式归档分册。取证范围：`jiuwenswarm/server/*.py`、`gateway_push/**/*.py`、`handlers/**/*.py`、`hooks/**/*.py`、`sandbox/**/*.py`、`transports/**/*.py`、`utils/**/*.py`。共 43 个 Python 文件；`server/runtime/**` 不计入本报告覆盖面，仅在服务入口调用到它时作为边界依赖标注。行号均指当前工作树中的实际源文件。

## 1. 结论与边界

1. 进程入口是 `app_agentserver.main() -> None`（[`app_agentserver.py:450`](../../../../../jiuwenswarm/server/app_agentserver.py#L450)）。主服务必启 WebSocket；HTTP 由配置决定是否启用，HTTP 启动失败不会终止 WebSocket（[`app_agentserver.py:326`](../../../../../jiuwenswarm/server/app_agentserver.py#L326)）。关闭顺序为 teammate bootstrap → HTTP → WS → 性能/可观测性/历史落盘（[`app_agentserver.py:400`](../../../../../jiuwenswarm/server/app_agentserver.py#L400)）。
2. HTTP 和 WS 在解析以后汇合到同一条业务管线：`parse_inbound` → `RequestContext` → `dispatch_parsed_request` → `dispatch.HANDLERS` 或 `_default` → `ResponseSink`。业务 handler 只依赖 `ctx.sink`，不反向依赖具体传输。
3. HTTP 有 181 条声明式 `RouteSpec`，另有 7 个显式 FastAPI 路由；统一前缀为 `/api/v1`。WS 没有业务 path 路由，连接建立后每一帧都是 E2A/legacy 请求信封。
4. `dispatch.HANDLERS` 有 79 条静态注册，加 17 条权限动态注册，共 96 个显式操作。其余合法 `ReqMethod`（包括大量 skills/plugins/channels/updater/symphony 操作和 `chat.send/resume/answer`）并非“没有实现”，而是落入 [`_default.py`](../../../../../jiuwenswarm/server/handlers/_default.py)，交由 `JiuWenSwarm.process_message/process_message_stream` 的 runtime 边界处理。
5. 检查范围内没有统一的 HTTP/WS 身份认证或 handler 级 RBAC。可见保护是浏览器 Origin/CORS、企业版 skills 写操作白名单、sandbox 平台能力检查，以及下游工具权限 rail；这些不能等同于入口身份认证。

## 2. 启动、请求与清理调用链

### 2.1 进程生命周期

`app_agentserver.main`（[`app_agentserver.py:450`](../../../../../jiuwenswarm/server/app_agentserver.py#L450)）
→ 解析 `--port/--name/--dotenv` 与环境变量（`:450-496`）
→ `asyncio.run(_run(host, port))`（`:496`）
→ telemetry 生命周期包装 `_run_with_telemetry`（`:221-244`）
→ 扩展注册表、扩展管理器及可选 hooks/rails 初始化（`:248-321`）
→ `AgentWebSocketServer.get_instance(host, port)`（`:326-329`）
→ `AgentWebSocketServer.start()`（[`agent_ws_server.py:424`](../../../../../jiuwenswarm/server/agent_ws_server.py#L424)）
→ 可选 `AgentHTTPServer.start()`（[`app_agentserver.py:340`](../../../../../jiuwenswarm/server/app_agentserver.py#L340)）
→ 信号驱动 `stop_event`（`:375-399`）
→ `HTTP.stop()`、`WS.stop()` 及落盘（`:400-446`）。

WS 启动时先触发 `AGENT_SERVER_BEFORE_START`，再调用 `websockets.serve(..., _connection_handler, process_request=_process_request, ping_interval, ping_timeout, max_size)`（[`agent_ws_server.py:424`](../../../../../jiuwenswarm/server/agent_ws_server.py#L424)）；随后后台预热 deep interface/checkpointer、启动 JiuwenBox 和事件循环监视器（`:466-505`）。`stop()` 取消预热与监视任务、关闭 listener、清 KV 后台任务、停止自有 JiuwenBox（`:774-814`）。

### 2.2 WebSocket 请求链

连接：`_connection_handler(ws)`（[`agent_ws_server.py:818`](../../../../../jiuwenswarm/server/agent_ws_server.py#L818)）
→ 为连接创建唯一 `gateway-ws:<id>` subscriber 与 `_GatewayWSPushSink`，标记 `reverse_rpc_capable=True`（`:826-836`）
→ 保存 identity context（`:838-855`）
→ 发送唯一例外的裸事件 `connection.ack`（`:857-867`）
→ `async for raw in ws`，每帧创建独立 task，因此同一连接的业务请求可并发；发送由单一 `asyncio.Lock` 串行化（`:869-883`）
→ `_handle_message(ws, raw, send_lock)`（`:940`）
→ `parse_inbound(raw)`（[`wire_parse.py:158`](../../../../../jiuwenswarm/server/wire_parse.py#L158)）
→ `RequestContext(request, WSSink, str(id(ws)), AgentServerServices)`（[`agent_ws_server.py:963`](../../../../../jiuwenswarm/server/agent_ws_server.py#L963)）
→ `dispatch_parsed_request(ctx, request, peer=ws)`（`:969`；[`jiuwenswarm/server/pipeline.py:61`](../../../../../jiuwenswarm/server/pipeline.py#L61)）
→ 显式 handler 或 `_default`（[`dispatch.py:201`](../../../../../jiuwenswarm/server/dispatch.py#L201)）。

连接退出的 finally 会清 identity、仅注销本连接 subscriber、清 ACP capability、取消帧任务、取消该连接关联的 agent/team 工作、停止 scheduler、清 session stream task 表（[`agent_ws_server.py:901`](../../../../../jiuwenswarm/server/agent_ws_server.py#L901)）。解析错误直接发送 `ParseResult.error_wire`，不会进入管线（`:945-959`）。

### 2.3 HTTP 请求链

`AgentHTTPServer.start()`（[`agent_http_server.py:669`](../../../../../jiuwenswarm/server/agent_http_server.py#L669)）
→ `_find_bindable_port` 从配置端口起最多探测 10 次、每次 +1000（`:654-668`）
→ `build_fastapi_app(self)`（`:664`；[`agent_http_routes.py:537`](../../../../../jiuwenswarm/server/agent_http_routes.py#L537)）
→ 遍历 `ROUTES` 调 `app.add_api_route`（[`agent_http_routes.py:586`](../../../../../jiuwenswarm/server/agent_http_routes.py#L586)）并注册特殊路由（`:594-817`）
→ uvicorn 后台 task，最多 5 秒等 ready；失败返回 `False`（[`agent_http_server.py:669`](../../../../../jiuwenswarm/server/agent_http_server.py#L669)）。

声明式路由请求：路由闭包
→ `collect_params`：query → path → JSON body，后者覆盖前者；空或非法 JSON 被降为 `{}`（[`agent_http_routes.py:436`](../../../../../jiuwenswarm/server/agent_http_routes.py#L436)）
→ `request_context` 从 header/path 取得 request/channel/session/user/routing（`:465-508`）
→ `invoke_unary` 或 `iter_stream`（[`agent_http_server.py:486`](../../../../../jiuwenswarm/server/agent_http_server.py#L486) / [`agent_http_server.py:538`](../../../../../jiuwenswarm/server/agent_http_server.py#L538)）
→ `build_agent_request`（`:317`）
→ `_make_ctx` 使用 `UnaryHTTPSink`/`SSESink` 与固定 `HTTP_CONNECTION_ID="http"`（`:82,453-462`）
→ `_dispatch_request` → `dispatch_parsed_request`（`:479-484`）。

SSE 由 `_pump_sse` 把 sink 队列帧转成 `{id,event,data}`；handler 异常产生终止失败帧，客户端断开会取消 handler task，finally 总会 `finish()`（[`agent_http_server.py:595`](../../../../../jiuwenswarm/server/agent_http_server.py#L595)）。HTTP unary 把最后一帧转换为 `{ok,data|error,request_id}` 并映射状态码（`:358-424`）。

### 2.4 共享分派和输出协议

`pipeline.dispatch_parsed_request`（[`jiuwenswarm/server/pipeline.py:61`](../../../../../jiuwenswarm/server/pipeline.py#L61)）先注入 request extension context；非 initialize 请求注册 ACP capability；仅 `chat.send/resume/answer` 触发 `BEFORE_CHAT_REQUEST` hook（[`jiuwenswarm/server/pipeline.py:36`](../../../../../jiuwenswarm/server/pipeline.py#L36)）。随后 `dispatch_with_context` 查询表；未命中时仅对 `chat.send` 尝试自动 team binding，再选择 `_handle_stream` 或 `_handle_unary`（[`jiuwenswarm/server/pipeline.py:95`](../../../../../jiuwenswarm/server/pipeline.py#L95)）。异常被编码成失败 `AgentResponse`；`CancelledError` 重抛，WS closed 只记录，extension context 在 finally 复位（[`jiuwenswarm/server/pipeline.py:116`](../../../../../jiuwenswarm/server/pipeline.py#L116)）。

`WSSink`、`UnaryHTTPSink`、`SSESink` 都实现 `ResponseSink`（[`transports/sink.py:30`](../../../../../jiuwenswarm/server/transports/sink.py#L30)）。所有 wire 经 `enforce_send_budget`；超限时按 stream/event/unary 类型构造小型失败替代帧（[`ws_send.py:29`](../../../../../jiuwenswarm/server/ws_send.py#L29)）。SSE 队列容量 256，结束哨兵投递最多等 5 秒（[`transports/sink.py:229`](../../../../../jiuwenswarm/server/transports/sink.py#L229)）。

## 3. 外部协议矩阵

### 3.1 WebSocket 帧协议

| 阶段 | 实际协议/字段 | 证据 |
|---|---|---|
| 握手 | 无业务 path 分派；`_process_request(*args)` 仅做可配置 Origin 验证，拒绝返回 403 | [`agent_ws_server.py:740`](../../../../../jiuwenswarm/server/agent_ws_server.py#L740) |
| 首帧 | `{"type":"event","event":"connection.ack","payload":{"status":"ready"}}` | [`agent_ws_server.py:857`](../../../../../jiuwenswarm/server/agent_ws_server.py#L857) |
| 入站 | JSON；优先 `E2AEnvelope.from_dict` → `e2a_to_agent_request`，失败退回 legacy `_payload_to_request` | [`wire_parse.py:106`](../../../../../jiuwenswarm/server/wire_parse.py#L106) |
| legacy 字段 | 顶层读取 `request_id, channel_id(default web), session_id, chat_id, service_id, agent_id, workspace_key, req_method, params, is_stream, timestamp, metadata, app_id`；`app_id` 并入 metadata。`user_id/group_id/bot_id/gateway_id` 不属于该 legacy 顶层解析器字段，应通过 metadata/E2A 路由身份传递 | [`wire_parse.py:106`](../../../../../jiuwenswarm/server/wire_parse.py#L106) |
| unary 出站 | E2A/legacy `AgentResponse` wire；失败含 `ok=false` 与 payload `error/code` | [`transports/sink.py:70`](../../../../../jiuwenswarm/server/transports/sink.py#L70) |
| stream 出站 | `AgentResponseChunk`，sequence 单调递增；空闲每 10 秒 `event_type=keepalive, sequence=-1` | [`_default.py:616`](../../../../../jiuwenswarm/server/handlers/_default.py#L616) |
| 主动推送 | 普通消息 fanout；`ACP_OUTPUT_REQUEST` 只发给最近 reverse-RPC owner | [`agent_ws_server.py:1176`](../../../../../jiuwenswarm/server/agent_ws_server.py#L1176), [`transports/push_registry.py:189`](../../../../../jiuwenswarm/server/transports/push_registry.py#L189) |
| 上限 | UTF-8 JSON 发送预算 `AGENT_WS_SEND_BUDGET_BYTES`，超限替换为 RESPONSE_TOO_LARGE 语义帧 | [`ws_send.py:96`](../../../../../jiuwenswarm/server/ws_send.py#L96) |

## HTTP REST 路由矩阵

### 声明式路由（181 条）

以下均自动加 `/api/v1`。每项格式为 `VERB path → ReqMethod`，且确由 `ROUTES` 与 `add_api_route` 注册（[`agent_http_routes.py:82`](../../../../../jiuwenswarm/server/agent_http_routes.py#L82)）。

**会话、聊天与命令（[`agent_http_routes.py:84`](../../../../../jiuwenswarm/server/agent_http_routes.py#L84)）**

- `POST /initialize → INITIALIZE`；`GET /sessions → SESSION_LIST`；`POST /sessions → SESSION_CREATE`；`PATCH|DELETE /sessions/{session_id} → SESSION_RENAME|SESSION_DELETE`。
- `POST /sessions/{session_id}/actions/{switch|fork|rewind|rewind-restore|rewind-compact|rewind-context|restore-files} → SESSION_SWITCH|SESSION_FORK|SESSION_REWIND|SESSION_REWIND_AND_RESTORE|SESSION_REWIND_COMPACT|SESSION_REWIND_CONTEXT|SESSION_RESTORE_FILES`；`GET /sessions/{session_id}/{history|turns} → HISTORY_GET|HISTORY_LIST_TURNS`。
- `POST /chat/{session_id}/actions/{interrupt|answer} → CHAT_CANCEL|CHAT_ANSWER`。
- `POST /sessions/{session_id}/commands → COMMAND_SESSION`；后缀 `compact|compact-partial|model|mcp|sandbox|btw|add-dir|chrome|context|recap|diff|simplify|resume|workflows|goal` 分别映射同名 `COMMAND_*`；`GET .../commands/status → COMMAND_STATUS`。

**Agent 与 Team（[`agent_http_routes.py:152`](../../../../../jiuwenswarm/server/agent_http_routes.py#L152)）**

- `GET|POST /agents → AGENTS_LIST|AGENTS_CREATE`；`GET|PUT|DELETE /agents/{name} → AGENTS_GET|AGENTS_UPDATE|AGENTS_DELETE`；`POST .../actions/{enable|disable} → AGENTS_ENABLE|AGENTS_DISABLE`；`GET .../tools → AGENTS_TOOLS_LIST`。
- `GET /teams/templates → TEAM_TEMPLATES_LIST`；`GET|POST /teams/bindings → TEAM_BINDINGS_LIST|TEAM_BINDING_CREATE`；`POST /teams/bindings/actions/generate → TEAM_BINDING_GENERATE`；`POST /teams/{team_name}/sessions/{session_id}/bind → TEAM_SESSION_BIND`。
- `GET /sessions/{session_id}/team/{snapshot|members|history} → TEAM_SNAPSHOT|TEAM_MEMBERS_GET|TEAM_HISTORY_GET`；`POST /teams/mq/publish → TEAM_MQ_PUBLISH`；`DELETE /teams/{team_name} → TEAM_DELETE`。

**Skills（[`agent_http_routes.py:179`](../../../../../jiuwenswarm/server/agent_http_routes.py#L179)）**

- `GET /skills|/skills/installed|/skills/marketplace → SKILLS_LIST|SKILLS_INSTALLED|SKILLS_MARKETPLACE_LIST`；`POST /skills/marketplace → ...ADD`；`DELETE /skills/marketplace/{name} → ...REMOVE`；`POST .../{name}/actions/toggle → ...TOGGLE`。
- `POST /skills/install|/import-local|/online-search → SKILLS_INSTALL|SKILLS_IMPORT_LOCAL|SKILLS_ONLINE_SEARCH`。
- retrieval：`GET /skills/retrieval/status|search|tree → STATUS|SEARCH|TREE`；`POST .../index-build|index-cancel → INDEX_BUILD|INDEX_CANCEL`。
- evolution：`GET|PUT /skills/evolution → GET|SAVE`；`GET /skills/{name}/evolution/status → STATUS`；`GET /skills/evolution/archives → ARCHIVES`；`POST .../actions/rollback|rebuild → ROLLBACK|REBUILD`。
- clawhub：`GET|PUT /skills/clawhub/token → GET_TOKEN|SET_TOKEN`；`GET .../search → SEARCH`；`POST .../actions/download → DOWNLOAD`。
- skillnet：`GET .../search|install-status → SEARCH|INSTALL_STATUS`；`POST .../install|actions/evaluate → INSTALL|EVALUATE`。
- teamskillshub：`GET .../info|search → INFO|SEARCH`；`POST .../install|actions/init|validate|pack|publish|delete → INSTALL|INIT|VALIDATE|PACK|PUBLISH|DELETE`。
- sources/updates/enterprise：`GET /skills/sources|sources/search|updates|enterprise|enterprise/sources|enterprise/sources/search → SOURCE_PROVIDERS|SOURCE_SEARCH|UPDATES_CHECK|ENTERPRISE_LIST|ENTERPRISE_SOURCE_PROVIDERS|ENTERPRISE_SOURCE_SEARCH`；`POST /skills/sources/install|actions/update|enterprise/install|enterprise/actions/uninstall → SOURCE_INSTALL|UPDATE|ENTERPRISE_INSTALL|ENTERPRISE_UNINSTALL`。
- `GET|DELETE /skills/{name} → SKILLS_GET|SKILLS_UNINSTALL`；`POST /skills/{name}/actions/toggle → SKILLS_TOGGLE`。

**扩展、插件、调度与 harness（[`agent_http_routes.py:311`](../../../../../jiuwenswarm/server/agent_http_routes.py#L311)）**

- `GET|POST /extensions → EXTENSIONS_LIST|EXTENSIONS_IMPORT`；`DELETE /extensions/{name} → EXTENSIONS_DELETE`；`POST .../actions/toggle → EXTENSIONS_TOGGLE`；`GET /hooks → HOOKS_LIST`。
- `GET /plugins → PLUGINS_LIST`；`POST /plugins/install|actions/reload → PLUGINS_INSTALL|PLUGINS_RELOAD`；`DELETE /plugins/{name} → PLUGINS_UNINSTALL`；`POST .../actions/enable|disable → PLUGINS_ENABLE|PLUGINS_DISABLE`。
- `GET|PATCH /schedule/config → SCHEDULE_CHECK_CONFIG|SCHEDULE_UPDATE_CONFIG`；`GET|POST /schedule/tasks → SCHEDULE_LIST|SCHEDULE_CREATE`；`POST /schedule/tasks/actions/run → SCHEDULE_RUN`；`GET /schedule/tasks/{task_id}|.../logs → SCHEDULE_STATUS|SCHEDULE_LOGS`；`POST .../actions/cancel → SCHEDULE_CANCEL`；`DELETE .../{task_id} → SCHEDULE_DELETE`。
- `POST /issues/actions/watch-once|matrix → ISSUE_WATCH_ONCE|ISSUE_MATRIX`；`GET /issues/states → ISSUE_STATE_LIST`；`DELETE /issues/{issue_id} → ISSUE_DELETE`。
- `GET /harness/packages → HARNESS_PACKAGES_GET`；`POST .../scan → SCAN`；`POST .../{name}/actions/activate|deactivate → ACTIVATE|DEACTIVATE`；`DELETE .../{name} → DELETE`。

**权限与运维（[`agent_http_routes.py:353`](../../../../../jiuwenswarm/server/agent_http_routes.py#L353)）**

- permissions：`GET|PUT /permissions/enabled → ENABLED_GET|SET`；`GET /permissions/tools|tools/list → TOOLS_GET|LIST`；`PUT|PATCH /permissions/tools → TOOLS_SET|UPDATE`；`DELETE /permissions/tools/{rule_id} → TOOLS_DELETE`；`GET|POST /permissions/rules → RULES_GET|CREATE`；`PATCH|DELETE /permissions/rules/{rule_id} → RULES_UPDATE|DELETE`；`GET|DELETE /permissions/approval-overrides[/{override_id}] → APPROVAL_OVERRIDES_GET|DELETE`；`GET|PUT /permissions/file-guard/workspace → WORKSPACE_ENABLE_GET|SET`；`GET|PUT .../workspace/access → WORKSPACE_ACCESS_GET|SET`。
- `POST /config/actions/cache-clear|agent-reload|sync-agents|prewarm-sync → CONFIG_CACHE_CLEAR|AGENT_RELOAD_CONFIG|SYNC_AGENTS_CONFIGS|AGENT_PREWARM_SYNC`；`POST /runtime/browser/actions/restart → BROWSER_RUNTIME_RESTART`；`POST /proactive/actions/tick → PROACTIVE_TICK`；`POST /acp/tool-responses → ACP_TOOL_RESPONSE`。
- symphony：`POST /symphony/actions/plan|build-score|pause-build → SYMPHONY_PLAN|BUILD_SCORE|PAUSE_BUILD`；`GET /symphony/score-status|graph → SCORE_STATUS|GRAPH`。
- channel config：feishu、xiaoyi、telegram、slack、dingtalk、whatsapp、wechat 均有 `GET|PUT /channels/<name>/config → *_GET_CONF|*_SET_CONF`；另有 `GET /channels/wechat/login-ui → CHANNEL_WECHAT_GET_LOGIN_UI` 与 `POST .../actions/unbind → CHANNEL_WECHAT_UNBIND`。
- updater/heartbeat：`GET /updater/status → UPDATER_GET_STATUS`；`POST /updater/actions/check|download → UPDATER_CHECK|DOWNLOAD`；`GET|PUT /updater/config → UPDATER_GET_CONF|SET_CONF`；`GET|PUT /heartbeat/config → HEARTBEAT_GET_CONF|SET_CONF`。

### 特殊路由（7 条）

| 路由 | 行号 | 行为 |
|---|---:|---|
| `GET /api/v1/health` | [`agent_http_routes.py:602`](../../../../../jiuwenswarm/server/agent_http_routes.py#L602) | `{status:"ok"}`，不进入 handler。 |
| `GET /api/v1/events/stream` | `:610` | 主动推送 SSE；按 session/channel 过滤。生成器开始时注册 `http-sse:*`，结束时注销（`:625-677`）。header `x-jiuwen-push-consumer: gateway` 会赋予 reverse-RPC 能力。 |
| `POST /api/v1/chat/completions` | `:711` | `CHAT_SEND`；Accept SSE 或 `enable_streaming` 决定流式。 |
| `POST /api/v1/chat/resume` | `:716` | `CHAT_RESUME`；流式判断同上。 |
| `GET /api/v1/sessions/{session_id}/history/stream` | `:720` | 强制 `HISTORY_GET` 流式。 |
| `POST /api/v1/rpc/{method}` | `:738` | `is_valid_req_method` 校验任意 `ReqMethod`，按请求选择 unary/SSE。 |
| `POST /api/v1/e2a` | `:787` | 原始信封；此处非法 JSON 明确 400；信封 `is_stream` 或 Accept SSE 可流式。 |

### HTTP 上下文与错误

- 上下文 header：`x-request-id`、`x-channel-id`、`x-session-id`、`x-user-id`、`x-group-id`、`x-bot-id`、`x-gateway-id`、`x-service-id`、`x-agent-id`、`x-workspace-key`，加 path/query/body 合并后的 routing/tenant 信息（[`agent_http_routes.py:465`](../../../../../jiuwenswarm/server/agent_http_routes.py#L465)）。
- 所有 HTTP 请求共享逻辑连接 ID `http`，故 HTTP 内部的 session switch/ACP capability 可互锁；与 WS 不互锁（[`agent_http_server.py:82`](../../../../../jiuwenswarm/server/agent_http_server.py#L82)）。
- 主要状态码：BAD_REQUEST 400、VALIDATION 422、UNAUTHORIZED 401、FORBIDDEN 403、NOT_FOUND/UNKNOWN_METHOD 404、CONFLICT 409、RESPONSE_TOO_LARGE 413、SERVICE_UNAVAILABLE/SANDBOX_BAD_REQUEST 503、INTERNAL 500；泛化错误再按 message 关键字猜测（[`agent_http_server.py:89`](../../../../../jiuwenswarm/server/agent_http_server.py#L89)）。
- CORS 来源按环境 > 配置 > 本机 web 端口解析；`*` 时关闭 credentials（[`agent_http_server.py:140`](../../../../../jiuwenswarm/server/agent_http_server.py#L140)）。企业版 HTTP 对非白名单 `skills.*` 直接 403；WS 不走该 HTTP guard（`:31-58,501-520`）。

## 4. 显式 Handler 操作矩阵

所有下列函数都通过 `dispatch.HANDLERS` 注册（静态注册 [`dispatch.py:67`](../../../../../jiuwenswarm/server/dispatch.py#L67)，权限动态注册 `:177-186`）。“权限”列只陈述本层实际检查，不把 CORS/平台能力误称为用户授权。

### [`handlers/bootstrap.py`](../../../../../jiuwenswarm/server/handlers/bootstrap.py)

| 注册 op | 精确处理签名 | 主要输入 | 输出/事件与状态 | 权限与主要错误 |
|---|---|---|---|---|
| `INITIALIZE` | `async handle_initialize(ctx: RequestContext) -> None`（`:27`） | `clientCapabilities`, `protocolVersion` | 初始化 manager；ACP channel 保存 capability；返回 server capabilities | 无身份校验；异常失败 wire |
| `SESSION_CREATE` | `async handle_session_create(ctx: RequestContext) -> None`（`:86`） | `mode`, `previous_session_id`, project/work mode, `is_swarm`, `create_token` | 创建/预热会话，写 metadata、切换 owner，返回 session/project/workMode/prewarm，后台同步 KVC | 显式 `session_id`、缺 token、模式/创建失败；claim 失败会释放 |
| `SESSION_FORK` | `async handle_session_fork(ctx: RequestContext) -> None`（`:373`） | `source_session_id`, 可选 `target_session_id`, `title` | 复制会话目录、上下文与状态 | 无身份校验；BAD_REQUEST/NOT_FOUND/ALREADY_EXISTS |
| `ACP_TOOL_RESPONSE` | `async handle_acp_tool_response(ctx: RequestContext) -> None`（`:483`） | `jsonrpc_id`, `response` dict | 完成挂起 ACP 调用；未匹配也返回 `accepted:false, ignored:true` 的成功响应 | 无身份校验；字段错误/内部异常 |

### [`handlers/session.py`](../../../../../jiuwenswarm/server/handlers/session.py)

| 注册 op | 精确处理签名 | 主要输入 | 输出/事件与状态 | 权限与主要错误 |
|---|---|---|---|---|
| `SESSION_LIST` | `async handle_session_list(ctx: RequestContext) -> None`（`:212`） | `limit`, `offset` | 分页 session 列表；失败降级为空列表 | 无；读取失败不置失败 |
| `SESSION_RENAME` | `async handle_session_rename(ctx: RequestContext) -> None`（`:247`） | session id + rename params | 写会话标题/metadata | 无；下游校验错误 |
| `SESSION_SWITCH` | `async handle_session_switch(ctx: RequestContext) -> None`（`:279`） | `session_id`, `previous_session_id` | 每 connection/channel 锁；切换 owner，返回 mode，后台 KVC | 缺 id BAD_REQUEST、会话错误 |
| `SESSION_DELETE` | `async handle_session_delete(ctx: RequestContext) -> None`（`:367`） | `session_id` | 释放 runtime/team/checkpoint，安全删除 session dir、解除绑定 | 无；BAD_REQUEST/NOT_FOUND/DELETE_FAILED；有路径安全检查 |
| `SESSION_REWIND` / `...AND_RESTORE` / `...COMPACT` | `async handle_session_rewind_full(ctx: RequestContext, restore_files: bool = False, compact: bool = False) -> None`（`:499`） | session/turn；注册表注入两个布尔参数 | 截断 history/context；可恢复文件或写 compact 边界/摘要 | 无；BAD_REQUEST、历史/恢复失败 |
| `SESSION_REWIND_CONTEXT` | `async handle_session_rewind_context(ctx: RequestContext) -> None`（`:700`） | session/turn | 只回退上下文/历史，要求 live agent | 无；BAD_REQUEST、agent 不可用 |
| `HISTORY_GET` unary/stream | `async handle_history_get(ctx: RequestContext) -> None`（`:781`）；`async handle_history_get_stream(ctx: RequestContext) -> None`（`:806`） | `session_id`, `page_idx` | unary 返回分页；stream 逐条 `history.message`，末尾 done | 无；参数/读取错误，超限会终止发送 |

### [`handlers/chat.py`](../../../../../jiuwenswarm/server/handlers/chat.py)

| 注册 op | 精确处理签名 | 主要输入 | 输出/事件与状态 | 权限与主要错误 |
|---|---|---|---|---|
| `CHAT_CANCEL` | `async handle_chat_cancel_dispatch(ctx: RequestContext) -> None`（`:263`） | `intent` 默认 cancel；supplement/client-disconnect metadata | team 分支产生 `chat.interrupt_result` 并 pause/resume/cancel；普通分支取消 session stream task 或向现有 agent 发消息；disconnect 释放 runtime | 无；不自动创建普通 agent；unsupported intent/运行时失败 |

`_ensure_auto_team_binding_for_chat(ctx, request) -> Any | None`（`:332`）不是注册 op，只在未命中的 `CHAT_SEND` fallback 前执行：team mode 未绑定时在 session 弱锁内按 query 生成并绑定 team，失败回滚。

### [`handlers/team.py`](../../../../../jiuwenswarm/server/handlers/team.py)

| 注册 op | 精确处理签名 | 输入 | 输出/状态 | 权限/错误 |
|---|---|---|---|---|
| `TEAM_TEMPLATES_LIST` | `async handle_team_templates_list(ctx: RequestContext) -> None`（`:253`） | 无 | `{templates}` | 无；存储错误 |
| `TEAM_BINDINGS_LIST` | `async handle_team_bindings_list(ctx: RequestContext) -> None`（`:273`） | 无 | 合并实体/legacy session，`{teams}` | 无；读取错误 |
| `TEAM_BINDING_CREATE` | `async handle_team_binding_create(ctx: RequestContext) -> None`（`:352`） | `team_name`, `template_id` | 创建 team entity/binding，`{team}` | BAD_REQUEST/NOT_FOUND/冲突 |
| `TEAM_BINDING_GENERATE` | `async handle_team_binding_generate(ctx: RequestContext) -> None`（`:387`） | `description` 或 `prompt` | LLM 生成模板并持久化 binding | 缺描述、生成/持久化失败 |
| `TEAM_SESSION_BIND` | `async handle_team_session_bind(ctx: RequestContext) -> None`（`:435`） | `session_id`, `team_name`, `mode` | 锁内校验并写 session metadata/binding；失败回滚 | 无；BAD_REQUEST/NOT_FOUND/绑定失败 |
| `TEAM_DELETE` | `async handle_team_delete(ctx: RequestContext) -> None`（`:572`） | `team_name` 与 team mode | 停 runtime，删 core team/session dirs/entity/binding，返回受影响 session | 无；BAD_REQUEST/UNSUPPORTED_MODE/NOT_FOUND/DELETE_FAILED |
| `TEAM_SESSION_RESET` | `async handle_team_session_reset(ctx: RequestContext) -> None`（`:786`） | `team_name`, `session_id` | 清 runtime/task board/checkpoint，保留 roster/entity/binding/history | 无；参数/清理错误 |
| `TEAM_RUNTIME_DISSOLVE` | `async handle_team_runtime_dissolve(ctx: RequestContext) -> None`（`:880`） | `session_id`, 可选 `team_name` | 停止并清 runtime、reset/prune roster；无 runtime 时幂等成功 | 无；参数/清理错误 |
| `TEAM_SNAPSHOT` | `async handle_team_snapshot(ctx: RequestContext) -> None`（`:1042`） | `session_id`, 可选 `team_name` | live snapshot，失败回退 DB；空状态也成功 | 无；读取错误 |
| `TEAM_MQ_PUBLISH` | `async handle_team_mq_publish(ctx: RequestContext) -> None`（`:1148`） | request session + `payload`（期望 `team.external_event`） | 发布队列，返回 published/error | 无；类型/队列错误 |
| `TEAM_HISTORY_GET` | `async handle_team_history_get(ctx: RequestContext) -> None`（`:1176`） | session、member、cursor/offset/limit/max_bytes | 清洗、限额分页 records/cursor | 无；缺 session、游标/读取错误 |
| `TEAM_MEMBERS_GET` | `async handle_team_members_get(ctx: RequestContext) -> None`（`:1265`） | session/team | 解析 metadata 并返回 human agents | 无；空成员以 NOT_FOUND 失败 |

### [`handlers/commands.py`](../../../../../jiuwenswarm/server/handlers/commands.py)

| 注册 op | 精确处理签名（行） | 输入 → 输出/状态 | 权限/错误 |
|---|---|---|---|
| `COMMAND_WORKFLOWS` | `async handle_command_workflows(ctx: RequestContext) -> None`（`:148`） | `action=list|get|get_human_prompt`, workflow/run/agent/correlation id → 有预算的列表/详情/HITL prompt | 无；required/not found |
| `COMMAND_ADD_DIR` | `async handle_command_add_dir(ctx: RequestContext) -> None`（`:344`） | `path`, `remember` → 信任目录，可能持久化 CLI 配置 | 本层无授权；路径/持久化错误 |
| `COMMAND_CHROME` | `async handle_command_chrome(ctx: RequestContext) -> None`（`:382`） | 无 → `{}` | mock/no-op |
| `COMMAND_COMPACT` | `async handle_command_compact(ctx: RequestContext) -> None`（`:403`） | session/mode → 压缩上下文，写 compact history，可推 `context.compressed`/state | 无；agent/压缩/写入错误 |
| `COMMAND_COMPACT_PARTIAL` | `async handle_command_compact_partial(ctx: RequestContext) -> None`（`:511`） | `turn_index`, `direction`, mode → 部分压缩/失败状态 | 无；索引/压缩错误 |
| `COMMAND_CONTEXT` | `async handle_command_context(ctx: RequestContext) -> None`（`:561`） | 无 → context usage | 无；agent 错误 |
| `COMMAND_RECAP` | `async handle_command_recap(ctx: RequestContext) -> None`（`:600`） | 无 → recap | 无；LLM 错误 |
| `COMMAND_BTW` | `async handle_command_btw(ctx: RequestContext) -> None`（`:643`） | `question` → 一次性只读回答 | 缺 question 返回 ok 响应内 status failed；LLM 错误 |
| `COMMAND_DIFF` | `async handle_command_diff(ctx: RequestContext) -> None`（`:720`） | session/project → 并发读取 turn diff 与 git diff | 无；历史/git 失败 |
| `COMMAND_SIMPLIFY` | `async handle_command_simplify(ctx: RequestContext) -> None`（`:779`） | `target` → 简化提示 | 无；处理错误 |
| `COMMAND_MODEL` | `async handle_command_model(ctx: RequestContext) -> None`（`:812`） | `action=add_model|switch_model|default`, target/model/env_updates | 改 `os.environ`、清模型 cache、reload agents；拒绝 placeholder API base | 本层无授权；action/config/reload 错误 |
| `COMMAND_RESUME` | `async handle_command_resume(ctx: RequestContext) -> None`（`:913`） | query → mock resume payload | mock |
| `COMMAND_SESSION` | `async handle_command_session(ctx: RequestContext) -> None`（`:942`） | 无 → mock remote URL/QR | mock |
| `COMMAND_STATUS` | `async handle_command_status(ctx: RequestContext) -> None`（`:968`） | `action=usage|config|overview` → session/config/model/MCP/memory 诊断 | 无；读取错误 |

### [`handlers/mcp.py`](../../../../../jiuwenswarm/server/handlers/mcp.py)

| 注册 op | 精确处理签名 | 输入 | 输出/状态 | 权限/错误 |
|---|---|---|---|---|
| `COMMAND_MCP` | `async handle_command_mcp(ctx: RequestContext) -> None`（`:234`） | `action=list|show|add|update|enable|disable|remove|delete|list_tools`；name、enabled、transport、command/args/cwd/env 或 url/headers/timeout | 敏感值遮罩；add/update 先做 stdio 路径/PATH 或远端连接预检；写 MCP 配置并 reload；list_tools 探测工具 | 本层无授权；MCP_NOT_FOUND/BAD_REQUEST/INTERNAL |

### [`handlers/sandbox.py`](../../../../../jiuwenswarm/server/handlers/sandbox.py)

| 注册 op | 精确处理签名 | 输入 | 输出/状态 | 权限/错误 |
|---|---|---|---|---|
| `COMMAND_SANDBOX` | `async handle_command_sandbox(ctx: RequestContext) -> None`（`:736`） | `sub=status|enable|disable|exclude.add|exclude.remove|exclude.list|files.allow|files.deny|files.remove|files.list` 及 path/files policy | Linux-only；enable 启/检 JiuwenBox、持久化 endpoint/runtime、重建 agent；disable 反向处理；exclude/files 修改配置并 dry-run policy；附 effective files/Landlock 状态 | 平台/capability guard，不是用户授权；yuanrong 仅 status；SANDBOX_BAD_REQUEST/SANDBOX_INTERNAL |

### [`handlers/agents.py`](../../../../../jiuwenswarm/server/handlers/agents.py)

| 注册 op | 精确处理签名（行） | 输入 → 输出/状态 | 权限/错误 |
|---|---|---|---|
| `AGENTS_LIST` | `async handle_agents_list(ctx: RequestContext) -> None`（`:111`） | `workspace_dir` → `{agents}` | 无；读取错误 |
| `AGENTS_GET` | `async handle_agents_get(ctx: RequestContext) -> None`（`:139`） | name/workspace → `{agent}` | 无；not found |
| `AGENTS_CREATE` | `async handle_agents_create(ctx: RequestContext) -> None`（`:177`） | CreateAgentParams + `generate`/prompt/workspace → 写 agent 文件、upsert enable、reload，返回 generated/applied/reload_error | 本层无授权；校验/生成/文件错误 |
| `AGENTS_UPDATE` | `async handle_agents_update(ctx: RequestContext) -> None`（`:237`） | name + UpdateAgentParams，默认不 generate → 更新并 reload | 无；not found/校验错误 |
| `AGENTS_DELETE` | `async handle_agents_delete(ctx: RequestContext) -> None`（`:296`） | name/workspace → 删除文件/config、reload | 无；not found/删除错误 |
| `AGENTS_ENABLE/DISABLE` | `async handle_agents_set_enabled(ctx: RequestContext, enabled: bool) -> None`（`:335`） | name/workspace；注册表注入 True/False | 拒绝 builtin；写 enable 配置并 reload | 无；blank/not found/builtin |
| `AGENTS_TOOLS_LIST` | `async handle_agents_tools_list(ctx: RequestContext) -> None`（`:387`） | workspace → tools | 无；服务错误 |

### [`handlers/extensions.py`](../../../../../jiuwenswarm/server/handlers/extensions.py)

| 注册 op | 精确处理签名（行） | 输入 → 输出/状态 | 权限/错误 |
|---|---|---|---|
| `EXTENSIONS_LIST` | `async handle_extensions_list(ctx: RequestContext) -> None`（`:45`） | 无 → Rail extensions | 无；普通异常 |
| `EXTENSIONS_IMPORT` | `async handle_extensions_import(ctx: RequestContext) -> None`（`:71`） | `folder_path` → 导入扩展 | 本层无授权；缺失/目录不存在 |
| `EXTENSIONS_DELETE` | `async handle_extensions_delete(ctx: RequestContext) -> None`（`:107`） | `name` → 删除 Rail 扩展 | 无；缺 name/管理器错误 |
| `EXTENSIONS_TOGGLE` | `async handle_extensions_toggle(ctx: RequestContext) -> None`（`:139`） | name/enabled → 改配置并 hot reload rail | 无；缺字段/热更错误 |
| `HOOKS_LIST` | `async handle_hooks_list(ctx: RequestContext) -> None`（`:186`） | 无 → events/disable_all_hooks/source | 无；加载错误 |
| `HARNESS_PACKAGES_GET/SCAN` | `async handle_harness_packages_get(ctx: RequestContext) -> None`（`:217`）；`...scan...`（`:242`） | 无 → 包信息；scan 还保存扫描结果 | 无；普通异常 |
| `HARNESS_PACKAGES_ACTIVATE/DEACTIVATE/DELETE` | `async handle_harness_packages_activate...`（`:268`）；`...deactivate...`（`:338`）；`...delete...`（`:403`） | `package_id`（HTTP path `name` 由 defaults 映射）+ channel/mode → package 状态与 agent 热更新；delete 禁止 native | 本层无授权；BAD_REQUEST/NOT_FOUND/CONFLICT/INTERNAL_ERROR |

### [`handlers/schedule.py`](../../../../../jiuwenswarm/server/handlers/schedule.py)

统一签名 `async handle_schedule_request(ctx: RequestContext, action: str) -> None`（`:39`），注册表通过 `_schedule(action)` 注入 action（[`dispatch.py:62`](../../../../../jiuwenswarm/server/dispatch.py#L62)）。首次请求惰性创建 `AutoHarnessService` 并启动 scheduler；create/run/cancel/delete/issue_watch_once 会取得并 pin agent（[`schedule.py:47`](../../../../../jiuwenswarm/server/handlers/schedule.py#L47)）。

| op/action | 输入 → 输出/状态 | 权限/错误 |
|---|---|---|
| `SCHEDULE_CHECK_CONFIG/check_config` | 无 → config | 无 |
| `SCHEDULE_UPDATE_CONFIG/update_config` | `fields` → 写 schedule config | 无 |
| `SCHEDULE_CREATE/create` | `query`, `interval_hours=4`, `run_immediately=false`, `model_name`, `pipeline` → 创建并可立即运行 | 无 |
| `SCHEDULE_RUN/run` | query/model/pipeline → 一次执行 | 无 |
| `SCHEDULE_LIST/list` | 无 → `{tasks}` | 无 |
| `SCHEDULE_STATUS/status` | `task_id` → task 或 payload error | 无 |
| `SCHEDULE_LOGS/logs` | task_id/log_type/history_index/offset/limit → logs | 无 |
| `SCHEDULE_CANCEL/cancel`, `SCHEDULE_DELETE/delete` | task_id → 取消/删除 | 无 |
| `ISSUE_WATCH_ONCE/issue_watch_once` | 全 params + model → 抓取一次 | 无 |
| `ISSUE_STATE_LIST/issue_state_list` | 无 → states | 无 |
| `ISSUE_DELETE/issue_delete`, `ISSUE_MATRIX/issue_matrix` | 全 params → 删除/刷新矩阵 | 无 |

未知 action 仍构造 `ok=True` 且 payload 为 error；异常才 `ok=False`（[`schedule.py:145`](../../../../../jiuwenswarm/server/handlers/schedule.py#L145)）。

### [`handlers/ops.py`](../../../../../jiuwenswarm/server/handlers/ops.py)

| 注册 op | 精确处理签名（行） | 输入 → 输出/状态 | 权限/错误 |
|---|---|---|---|
| `PROACTIVE_TICK` | `async handle_proactive_tick(ctx: RequestContext) -> None`（`:37`） | `target_channel` → tick 状态 | 无；engine unavailable |
| `BROWSER_RUNTIME_RESTART` | `async handle_browser_runtime_restart(ctx: RequestContext) -> None`（`:83`） | 无 → reset 活跃 runtime 并 restart | 无；runtime 错误 |
| `CONFIG_CACHE_CLEAR` | `async handle_config_cache_clear(ctx: RequestContext) -> None`（`:114`） | 无 → clear cache | 无 |
| `AGENT_RELOAD_CONFIG` | `async handle_agent_reload_config(ctx: RequestContext) -> None`（`:143`） | config/env/target channel/session/reload_scopes → tenant/global reload + proactive reload | OfficeClaw tenant guard；其余无；reload 错误 |
| `SYNC_AGENTS_CONFIGS` | `async handle_sync_agents_configs(ctx: RequestContext) -> None`（`:230`） | 全 params → tenant pool sync result/event type，附 all_ok | 无；pool 错误 |
| `AGENT_PREWARM_SYNC` | `async handle_agent_prewarm_sync(ctx: RequestContext) -> None`（`:280`） | `enabled_channels` 必需 + config/env → stats | 无；参数/预热错误 |

### [`handlers/permissions.py`](../../../../../jiuwenswarm/server/handlers/permissions.py)

17 个 op 均注册为 `async handle_permissions_config(ctx: RequestContext) -> None`（`:37`）：`PERMISSIONS_ENABLED_GET/SET`、`TOOLS_GET/LIST/SET/UPDATE/DELETE`、`RULES_GET/CREATE/UPDATE/DELETE`、`APPROVAL_OVERRIDES_GET/DELETE`、`WORKSPACE_ENABLE_GET/SET`、`WORKSPACE_ACCESS_GET/SET`（[`dispatch.py:177`](../../../../../jiuwenswarm/server/dispatch.py#L177)）。

输入分别为 `enabled`、`tools`、`tool|name + level`、`rule`、`id + patch`、`id`、`rw_enabled`、`access`；调用 `common.permissions.permissions_config_rpc`，修改权限配置数据库/cache，成功 mutation 后 fire-and-forget reload agent config。输出是 RPC payload；错误码 BAD_REQUEST/NOT_FOUND/INTERNAL。这里管理“之后如何约束工具”，但本 handler 本身没有可见的管理员身份检查。

### [`_default.py`](../../../../../jiuwenswarm/server/handlers/_default.py)（合法但未显式注册的 ReqMethod）

`async _handle_unary(ctx, request)`（`:492`）与 `async _handle_stream(ctx, request)`（`:616`）由 pipeline 直接调用，不在 `HANDLERS` 表中。它们覆盖所有合法且未显式注册的操作：skills、plugins、symphony、channel/updater/heartbeat、goal、restore-files/turn list，以及 chat.send/resume/answer 等。stateless 前缀为 `skills.`、`skilldev.`、`plugins.`、`symphony.`（`:51-60`）；其余执行 mode/project/session 准备后进入 runtime agent。stream 保存当前 session task，发送 10 秒 keepalive，并在 finally 取消 heartbeat、清 task map、检测 plan mode 退出（`:616-755`）。这是内部 fallback，不是额外外部 op。

## 5. Push、Hook、Sandbox 与传输边界

### 5.1 主动推送

`AgentWebSocketServer.send_push(msg) -> int`（[`agent_ws_server.py:1176`](../../../../../jiuwenswarm/server/agent_ws_server.py#L1176)）调用 `build_server_push_wire`（[`gateway_push/wire.py:21`](../../../../../jiuwenswarm/server/gateway_push/wire.py#L21)）：有 `response_kind` 时构造 final E2AResponse；否则构造 AgentResponseChunk；两者都带 server-push marker/session。`PushRegistry.push` 对快照串行 fanout；SSE 或 `drop_on_stall=True` subscriber 在 5 秒超时/失败后注销，普通 WS 配置 `drop_on_stall=False` 因而不做该超时（[`transports/push_registry.py:220`](../../../../../jiuwenswarm/server/transports/push_registry.py#L220)）。ACP output request 走 `push_reverse_rpc`，只发给最近注册的 reverse-RPC owner（`:189-219`）。

HTTP push SSE subscriber 仅在生成器开始消费时注册，finally 注销；WS subscriber 随连接注册/注销。`WebSocketGatewayPushTransport.send_push`（[`gateway_push/transport.py:20`](../../../../../jiuwenswarm/server/gateway_push/transport.py#L20)）只是取得 singleton server 并委托，丢弃 delivery count。

### 5.2 Hooks

`HookExecutor.run_all(hook_configs, hook_input, session_id="") -> list[HookResult]`（[`hooks/executor.py:35`](../../../../../jiuwenswarm/server/hooks/executor.py#L35)）并发执行 command/prompt hooks。command hook 用配置的 shell 启子进程，把 JSON 放 stdin 并设置 `ARGUMENTS/TOOL_NAME`；exit 0 解析 JSON，2 为 blocking，其余 non-blocking，支持 timeout（`:60-171`）。prompt hook替换变量、调用模型、解析 block decision（`:173-289`）。因此 shell hook 配置本身是明确的信任边界。

`UserHookRail`（[`hooks/user_hook_rail.py:18`](../../../../../jiuwenswarm/server/hooks/user_hook_rail.py#L18)，priority 60）在 before tool 时可 block/改 tool name/args/追加上下文；after tool/exception/after invoke 可追加反馈（`:27-153`）。它不是 HTTP/WS handler，而是 agent 工具调用内部 rail。

### 5.3 Sandbox/JiuwenBox

`JiuwenBoxRunner`（[`sandbox/jiuwenbox_runner.py:70`](../../../../../jiuwenswarm/server/sandbox/jiuwenbox_runner.py#L70)）是进程内 singleton，持有子进程、锁、ownership、stdout/stderr pump task 与 policy。`ensure_running` 在锁内：external ownership 只 health check；internal ownership 在 host/port/policy 相同且健康时复用，否则停旧进程并启动 `python -m uvicorn jiuwenbox.server.app:app`，通过环境传 policy/PYTHONPATH，再等待 `/health`（`:161-346`）。`stop/_stop_no_lock` 优雅等待最长 60 秒后 kill（`:435-471`），atexit 同步终止最长 3 秒（`:390-434`）。

AgentServer 对外仍只有 `COMMAND_SANDBOX`；JiuwenBox HTTP 是它调用的内部执行边界，不应并入本报告的外部 AgentServer 路由表。

## 6. 逐文件覆盖清单（43/43）

下列“符号”只列模块顶层定义；类的关键协议方法随类说明列出。副作用、依赖、错误/并发均从实际调用点归纳。

### 顶层服务文件（14）

1. [`jiuwenswarm/server/__init__.py`](../../../../../jiuwenswarm/server/__init__.py)：runtime facade 的惰性导出。符号：`__getattr__(name: str) -> Any`（`:16`）；导出 `JiuWenSwarm/SkillManager`。副作用仅首次属性访问导入；未知名抛 `AttributeError`。
2. [`agent_http_routes.py`](../../../../../jiuwenswarm/server/agent_http_routes.py)：HTTP 路由声明/上下文组装。符号：`RequestContext` dataclass（`:47`）、`RouteSpec` dataclass（`:59`）、`_safe_json_body(request: Any) -> dict[str, Any]`（`:436`）、`collect_params(request: Any) -> dict[str, Any]`（`:454`）、`request_context(request: Any) -> RequestContext`（`:465`）、`_envelope_wants_stream(request: Any, envelope: dict[str, Any]) -> bool`（`:510`）、`wants_stream(request: Any, params: dict[str, Any]) -> bool`（`:524`）、`build_fastapi_app(server: AgentHTTPServer) -> Any`（`:537`）、`_register_special_routes(app: Any, server: AgentHTTPServer) -> None`（`:599`）、`_invoke_raw_envelope(server, envelope, request_id)` async（`:817`）。状态是 FastAPI route/CORS 注册；非法常规 JSON 被吞为 `{}`；SSE 生成器保证注销。
3. [`agent_http_server.py`](../../../../../jiuwenswarm/server/agent_http_server.py)：HTTP facade、SSE pump、错误映射。顶层函数签名：`_is_enterprise_skill_forbidden(method: str) -> bool`（`:48`）、`resolve_error_status(code: str, message: str) -> int`（`:124`）、`resolve_cors_origins() -> tuple[list[str], bool]`（`:140`）、`new_request_id() -> str`（`:198`）、`is_port_available(host: str, port: int) -> bool`（`:202`）、`_as_bool(value: Any, default: bool = False) -> bool`（`:220`）、`resolve_http_server_settings(agent_host: str) -> tuple[bool, str, int]`（`:234`）、`build_envelope_json(..., method, params, request_id, session_id, channel_id, user_id, is_stream, routing=None, tenant_ids=None) -> str`（`:281`）、`build_agent_request`（同参数）`-> Any`（`:317`）、`_frame_event_name(frame: dict[str, Any]) -> str`（`:358`）、`frame_to_http_envelope(frame, request_id) -> tuple[dict[str, Any], int]`（`:378`）、`AgentHTTPServer`（`:427`）、`is_valid_req_method(method: str) -> bool`（`:765`）。类关键方法：`__init__(self, ws_server, *, host, port)`（`:430`）、`invoke_unary(...)`（`:486`）、`iter_stream(...)`（`:538`）、`iter_raw_envelope(...)`（`:571`）、`start() -> bool`（`:669`）、`stop(*, timeout=10.0) -> None`（`:745`）。持 uvicorn task/server；断流取消 handler。
4. [`agent_ws_server.py`](../../../../../jiuwenswarm/server/agent_ws_server.py)：WS listener、共享服务容器、推送入口。顶层：`_import_interface_deep_blocking() -> None`（`:163`）、`_warm_interface_deep_module() -> None` async（`:168`）、`ensure_interface_deep_and_checkpointer() -> None` async（`:182`）、`_GatewayWSPushSink`（`:253`）、`AgentWebSocketServer`（`:292`）。关键签名：sink `__init__(ws, send_lock)`（`:268`）、`send_wire(dict) -> bool`（`:271`）、send_unary/chunk/error（`:280-286`）；server `__init__(host="127.0.0.1", port=18000, *, ping_interval=30.0, ping_timeout=300.0)`（`:306`）、`get_instance(...)`（`:387`）、`reset_instance()`（`:410`）、`start() -> None` async（`:424`）、`stop() -> None` async（`:774`）、`_connection_handler(ws)` async（`:818`）、`_handle_message(ws, raw, send_lock)` async（`:940`）、`send_push(msg) -> int` async（`:1176`）。singleton 与多组 task/cache 是主要状态；每帧并发、每连接发送锁。
5. [`app_agentserver.py`](../../../../../jiuwenswarm/server/app_agentserver.py)：CLI/进程编排。符号：`is_enterprise() -> bool`（`:29`）、`_set_exit_reason(reason: str) -> None`（`:202`）、`_atexit_log_exit_reason() -> None`（`:207`）、`_run(host: str, port: int) -> None` async（`:221`）、`_run_with_telemetry(host: str, port: int, telemetry_lifecycle) -> None` async（`:234`）、`main() -> None`（`:450`）。全局 exit reason、signals 与 shutdown 副作用；HTTP failure 隔离。
6. `context.py`：传输无关 context 和服务白名单。符号：`AgentServerServices`（`:58`），`__init__(server)`（`:67`）、`raw_server`（`:71`）、`__getattr__`（`:75`）、`__setattr__`（`:85`）；`RequestContext` frozen dataclass（`:93`），字段 request/sink/connection_id/services，properties params/request_id/channel_id/session_id（`:116-129`）。未知服务成员抛 `AttributeError`，避免 handler 绑定整类私有 API。
7. [`dispatch.py`](../../../../../jiuwenswarm/server/dispatch.py)：显式操作表。符号：`HandlerSpec` frozen dataclass（`:32`；`resolve_fn(request)` `:55`）、`_schedule(action: str) -> HandlerSpec`（`:62`）、`_register_permissions_methods() -> None`（`:177`）、`supported_methods() -> frozenset[ReqMethod]`（`:189`）、`dispatch_with_context(ctx, request) -> bool` async（`:194`）、`dispatch_to_handler(server, ws, request, send_lock, *, context_factory=None) -> bool` async（`:201`）、`_default_context(...) -> Any`（`:230`）。模块导入时动态注册 permissions；重复注册抛 RuntimeError。
8. [`event_loop_monitor.py`](../../../../../jiuwenswarm/server/event_loop_monitor.py)：可选内存/loop lag/线程栈诊断。符号：`_frame_signature(frame: Any) -> str`（`:72`）、`_all_thread_frames() -> dict[int, Any]`（`:87`）、`_format_full_stack(frame: Any) -> str`（`:93`）、`_mem_stats() -> tuple[float, float]`（`:98`）、`memory_trend_loop() -> None` async（`:133`）、`loop_heartbeat_loop() -> None` async（`:177`）、`StackSampler`（`:203`）、`ensure_event_loop_monitor() -> None` async（`:296`）、`stop_event_loop_monitor() -> None`（`:331`）。持全局 tasks/thread stop event；采样失败只诊断，不中断服务。
9. `pipeline.py`：共享分派管线。符号：`_should_trigger_before_chat_request_hook(request: AgentRequest) -> bool`（`:36`）、`_trigger_before_chat_request_hook(request: AgentRequest) -> None` async（`:44`）、`dispatch_parsed_request(ctx: RequestContext, request: AgentRequest, *, peer: Any = None) -> None` async（`:61`）。副作用是 hook/ACP capability/extension context；异常编码，取消重抛。
10. [`tool_concurrency.py`](../../../../../jiuwenswarm/server/tool_concurrency.py)：工具批处理并发策略桥。符号：`ToolConcurrencyRule`（`:17`）、`ConcurrencyPolicy`（`:22`，`as_log_text` `:26`）、`_normalize_tool_name`（`:36`）、`resolve_concurrency_policy(config=None, *, reload=False) -> ConcurrencyPolicy`（`:40`）、`_parse_limit`（`:48`）、`_load_policy_from_mapping`（`:72`）、`_load_policy_from_config`（`:102`）、`_to_core_policy`（`:121`）、`_get_controller()`（`:136`）、`register_tool_batch_concurrency() -> None`（`:150`）、`apply_tool_concurrency_limit() -> None`（`:175`）。模块 controller 缓存并向 AbilityManager 注册 hook；配置错误非致命降级。
11. [`wire_parse.py`](../../../../../jiuwenswarm/server/wire_parse.py)：E2A/legacy 入站解析与日志脱敏。符号：`_mask_text_for_log(value: str) -> str`（`:38`）、`_mask_system_prompt_for_log(system_prompt: str) -> str`（`:42`）、`_mask_query_for_log(data: dict[str, Any]) -> dict[str, Any]`（`:51`）、`_log_inbound_payload(raw, data) -> None`（`:74`）、`_payload_to_request(data) -> AgentRequest`（`:106`）、`ParseResult` frozen dataclass（`:142`，`ok` property `:153`）、`parse_inbound(raw: str | bytes) -> ParseResult`（`:158`）。无持久状态；解析错误返回 wire，不抛给连接循环。
12. [`wire_truncate.py`](../../../../../jiuwenswarm/server/wire_truncate.py)：历史/workflow wire 预算整形。顶层精确签名与行号：`_json_wire_size(value: Any) -> int` `:116`；`_coerce_int(value: Any, *, default: int, minimum: int, maximum: int) -> int` `:124`；`_truncate_string_by_bytes(value: str, max_bytes: int) -> str` `:133`；`_compact_wire_metadata_value(value: Any) -> Any` `:147`；`_sanitize_history_wire_value(value: Any, *, depth: int = 0) -> Any` `:156`；`_collapse_oversized_history_record(record: dict[str, Any]) -> dict[str, Any]` `:184`；`_minimal_history_record_for_wire` `:205`；`_sanitize_history_record_for_wire` `:217`；`_select_history_record_page(records, *, offset, limit, max_bytes)` `:229`；`_is_waiting_human_agent` `:292`；`_extract_waiting_human_prompts` `:296`；`_restore_waiting_human_prompts` `:320`；`_workflow_agent_for_collapse` `:341`；`_collapse_oversized_workflow_snapshot_item` `:385`；`_minimal_workflow_snapshot_item_for_wire` `:434`；`_minimal_workflow_detail_preserving_waiting_human` `:446`；`_sanitize_workflow_snapshot_item_for_wire` `:482`；`_fit_workflow_detail_to_budget(item, *, budget, preserved_prompts)` `:494`；`_workflow_list_summary_phase` `:532`；`_workflow_list_summary_item` `:544`；`_minimal_workflow_list_item` `:570`；`_fit_workflow_list_item_for_budget` `:582`；`_build_workflow_list_payload(workflows, *, session_id)` `:609`；`_build_workflow_detail_payload(workflow, *, session_id)` `:648`；`_find_workflow_agent(workflow, *, agent_id=None, correlation_id=None)` `:687`；`_build_workflow_human_prompt_payload(...)` `:713`；`_build_workflow_snapshot_payload(workflows, *, session_id)` `:755`。纯函数，无状态；特别保留 waiting-human prompt，再逐级删 logs/details。
13. [`ws_send.py`](../../../../../jiuwenswarm/server/ws_send.py)：统一发送预算。符号：`_oversized_payload(actual_bytes: int) -> dict[str, Any]`（`:29`）、`_build_oversized_fallback(wire, actual_bytes) -> dict[str, Any]`（`:38`）、`enforce_send_budget(wire: dict[str, Any]) -> tuple[str, bool]`（`:96`）、`send_wire_payload(ws: Any, wire: dict[str, Any]) -> bool` async（`:132`）。发送超限替代帧；替代帧仍超限则抛异常。
14. [`utils/utils.py`](../../../../../jiuwenswarm/server/utils/utils.py) 已在 utils 小节列出，顶层服务文件实际为 13 个；本标题中的“14”按 brief 的 `server/*.py` 匹配含 `__init__.py` 后应为 13。覆盖总数仍按实际枚举 43，以下分类和总表复核为准。

### gateway_push（3）

15. [`gateway_push/__init__.py`](../../../../../jiuwenswarm/server/gateway_push/__init__.py)：重导出 `GatewayPushTransport`、`WebSocketGatewayPushTransport`、`build_server_push_wire`；无定义/状态。
16. [`gateway_push/transport.py`](../../../../../jiuwenswarm/server/gateway_push/transport.py)：符号 `GatewayPushTransport(Protocol)`（`:11`，`send_push(self, msg: dict[str, Any]) -> None` async `:12`）、`WebSocketGatewayPushTransport`（`:17`，同签名 `:20`）。只委托 singleton server；server 未初始化时相应错误向上传播。
17. [`gateway_push/wire.py`](../../../../../jiuwenswarm/server/gateway_push/wire.py)：`build_server_push_wire(msg: dict[str, Any]) -> dict[str, Any]`（`:21`）。纯编码；读取 response_kind/payload/metadata 并附 server-push marker。

### handlers（15）

18. [`handlers/__init__.py`](../../../../../jiuwenswarm/server/handlers/__init__.py)：导入并重导出 12 个域模块；无函数/类。导入期会促成 handler 模块加载。
19. [`handlers/_default.py`](../../../../../jiuwenswarm/server/handlers/_default.py)：fallback/runtime 适配。顶层签名见第 4 节，另有 `_is_stateless_method_request(request) -> bool` `:51`、`_is_readonly_goal_get_request` `:64`、`_is_explicit_plan_entry_request` `:79`、`_should_sync_code_mode_state` `:86`、`_session_mode_sync_lock(session_id) -> asyncio.Lock` `:98`、`_get_stateless_agent(ctx, channel_id)` async `:108`、`_push_plan_mode_exited` `:137`、`_check_post_process_plan_exit` `:154`、`_ensure_code_mode_state` `:201`、`_prepare_code_mode_chat_turn` `:311`、`_get_tenant_agent_manager` `:451`、`_prepare_tenant_code_mode_chat_turn` `:461`、`_handle_unary_impl` `:520`、`_handle_stream_impl` `:644`。持 stateless agent 与 session lock 弱引用、stream task；并发/取消见 2.4。
20. [`handlers/_shared.py`](../../../../../jiuwenswarm/server/handlers/_shared.py)：跨域模式/路径/bootstrap/KVC 工具。签名：`_log_background_session_kvc_failure(task: asyncio.Task) -> None` `:52`；`send_error_wire(request, error, code=None) -> dict` `:66`；`resolve_request_project_dir(request: AgentRequest) -> str | None` `:91`；`resolve_agent_request_mode(raw_mode, *, work_mode=None) -> tuple[str, str | None, str]` `:122`；`_apply_resolved_mode_to_request(request, *, work_mode=None) -> tuple[str, str | None]` `:178`；`_resolve_model(ctx, model_name=None) -> Optional[Any]` `:191`；`_is_team_metadata_mode` `:209`；`_sessions_dir_for_request` `:214`；`_agent_workspace_dir_for_request` `:220`；`_effective_config_for_request` `:226`；`bootstrap_preconditions(request)` async contextmanager `:270`；`_sync_chat_request_metadata(...) -> str | None` `:314`；`_inject_plan_mode_activation_reminder` `:403`；`_request_query_text` `:447`；`_uses_tenant_pool` `:457`；`_session_team_binding_lock(session_id) -> asyncio.Lock` `:476`。持后台 KVC task set、弱锁、plan-exited session set；bootstrap 负责加锁/准备上下文并保证退出清理。
21. [`handlers/agents.py`](../../../../../jiuwenswarm/server/handlers/agents.py)：Agent CRUD；顶层 `_generate_agent_with_llm(config_service, prompt, existing, *, model=None) -> tuple[Any, bool]` async（`:56`）及第 4 节七个 handler。写 agent/config 文件并 reload；错误多为 payload error，缺统一语义码。
22. [`handlers/bootstrap.py`](../../../../../jiuwenswarm/server/handlers/bootstrap.py)：初始化/创建/分叉/ACP 回执；四个 handler 精确签名见第 4 节。主要状态在 agent manager、session metadata、prewarm claim 与 ACP manager。
23. [`handlers/chat.py`](../../../../../jiuwenswarm/server/handlers/chat.py)：中断与自动 team binding。顶层：`_is_client_disconnect_cancel_request(request: AgentRequest) -> bool` `:38`、`_cleanup_client_disconnect_session_runtime(ctx, request) -> bool` async `:46`、`_build_team_interrupt_response(...)` `:82`、`_handle_cancel(ctx, *, allow_create, intent) -> None` async `:109`、`handle_chat_cancel_dispatch` `:263`、`_ensure_auto_team_binding_for_chat` `:332`。stream task/team runtime 是并发状态。
24. [`handlers/commands.py`](../../../../../jiuwenswarm/server/handlers/commands.py)：命令集合；第 4 节列出 14 个 handler。辅助签名 `_build_simplify_prompt(target: str = "") -> str`（`:122`）、`_extract_compact_summary_processor(summary: str) -> str`（`:135`）、`_is_env_api_base_placeholder(env_updates: dict) -> bool`（`:143`）。副作用跨 trusted dirs、context/history、env/model cache。
25. [`handlers/extensions.py`](../../../../../jiuwenswarm/server/handlers/extensions.py)：Rail/hooks/harness；第 4 节列出 10 个 handler；辅助 `_harness_error_code(exc: BaseException) -> str`（`:27`）。涉及文件导入、扩展配置/hot reload、package 状态。
26. [`handlers/mcp.py`](../../../../../jiuwenswarm/server/handlers/mcp.py)：MCP 单入口。辅助签名 `_normalize_mcp_payload(...)`（`:28`）、`_mask_sensitive_fields(payload: Any) -> Any`（`:72`）、`_pre_check_mcp_server(server_payload: dict[str, Any]) -> tuple[bool, str]` async（`:92`）、`_fetch_mcp_tools_from_config(entry) -> list[dict[str, Any]]` async（`:157`）、`_normalize_mcp_add_payload(ctx, params) -> dict`（`:220`）、`_normalize_mcp_update_payload(ctx, params) -> dict`（`:224`）；handler `:234`。网络/子进程预检有 timeout，敏感信息输出前遮罩。
27. [`handlers/ops.py`](../../../../../jiuwenswarm/server/handlers/ops.py)：运维操作；辅助 `_reset_active_browser_runtimes_if_available(browser_move: Any) -> int` async（`:20`）与六个 handler（第 4 节）。修改 cache/runtime/tenant agents；OfficeClaw guard 是唯一局部租户约束。
28. [`handlers/permissions.py`](../../../../../jiuwenswarm/server/handlers/permissions.py)：权限配置桥。`_log_permission_reload_failure(task: asyncio.Task) -> None`（`:27`）、`handle_permissions_config(ctx: RequestContext) -> None` async（`:37`）。mutation 后后台 reload；reload 失败只日志。
29. [`handlers/sandbox.py`](../../../../../jiuwenswarm/server/handlers/sandbox.py)：sandbox 命令和 policy 转换。顶层签名/行：`_resolve_active_project_dir(...)` `:52`、`_resolve_active_is_code_agent(ctx, channel_id) -> bool` `:101`、`allocate_internal_jiuwenbox_port(...)` `:130`、`_dry_run_files_policy(...)` `:157`、`_read_landlock_compatibility(policy_path) -> str` `:176`、`_effective_files_from_adapter(adapter) -> dict | None` `:193`、`_apply_sandbox_runtime_patch(...)` async `:209`、`parse_sandbox_host_port(url: str) -> tuple[str, int]` `:224`、`_require_sandbox_supported() -> None` `:236`、`_handle_sandbox_enable` async `:256`、`_handle_sandbox_disable` async `:363`、exclude add/remove async `:394/:413`、files set/remove async `:432/:510`、`_attach_effective_sandbox_files` `:575`、`_attach_landlock_status` async `:640`、`_canonicalize_sandbox_files_path` `:664`、`_file_entry_matches_path` `:697`、`_reject_extra_sandbox_files_params` `:727`、handler `:736`。跨配置文件、runner 子进程与 agent recreation；有 OS/模式检查。
30. [`handlers/schedule.py`](../../../../../jiuwenswarm/server/handlers/schedule.py)：scheduler/issue 统一入口。`_set_scheduler_agent(ctx, agent: Any) -> None`（`:24`）、`handle_schedule_request(ctx: RequestContext, action: str) -> None` async（`:39`）。惰性启动 service、pin/unpin agent；异常统一失败 wire。
31. [`handlers/session.py`](../../../../../jiuwenswarm/server/handlers/session.py)：会话 CRUD/回退/历史。辅助 `_coerce_int(value: object, default: int) -> int` `:55`、`_resolve_rewind_agent(...)` async `:71`、`get_conversation_history(session_id: str, page_idx: int) -> dict[str, Any] | None` `:128`、`_is_restorable_history_record(record: Any) -> bool` `:178`；八个 handler 见第 4 节。文件系统删除/恢复有路径与历史边界检查；switch 用 per connection/channel lock。
32. [`handlers/team.py`](../../../../../jiuwenswarm/server/handlers/team.py)：team 模板/绑定/runtime/MQ/history。辅助 `_team_binding_payload(binding) -> dict` `:45`、`_create_team_binding_from_template(...)` `:53`、`_create_generated_team_binding(...)` async `:101`、`_find_team_session_ids(...)` async `:151`、`_active_team_session_map(...)` `:190`、`_legacy_team_bindings_from_sessions(...)` `:216`、`_snapshot_tasks(payload) -> list[Any]` `:1139`；12 个 handler 见第 4 节。多存储协调以 session/team locks 和补偿回滚保证一致性，但仍可能返回下游部分失败。

### hooks（3）

33. [`hooks/__init__.py`](../../../../../jiuwenswarm/server/hooks/__init__.py)：仅包 docstring；无定义/状态。
34. [`hooks/executor.py`](../../../../../jiuwenswarm/server/hooks/executor.py)：hook 执行器。顶层 `HookOutcome`（`:17`）、`HookResult` dataclass（`:24`）、`HookExecutor`（`:32`）。类方法签名：`run_all(self, hook_configs: list[dict], hook_input: dict, session_id: str = "") -> list[HookResult]` async（`:35`）、`_run_command_hook(...)` async（`:60`）、`parse_command_output(stdout: str) -> HookResult` static（`:141`）、`_run_prompt_hook(...)` async（`:173`）、`_query_llm(prompt: str, model_name: str = "") -> str` async（`:223`）、`extract_json_from_response(text: str) -> dict` static（`:270`）。并发 gather；子进程/LLM timeout；配置 shell 是信任边界。
35. [`hooks/user_hook_rail.py`](../../../../../jiuwenswarm/server/hooks/user_hook_rail.py)：`UserHookRail(DeepAgentRail)`（`:18`）；`__init__(self, hooks_config, *, executor=None)`（`:27`）、`before_tool_call(self, tool_call, invocation)` async（`:34`）、`after_tool_call(...)` async（`:77`）、`on_tool_exception(...)` async（`:110`）、`after_invoke(...)` async（`:132`）。修改 invocation/tool_call 或追加反馈；exception hook 结果不改变原异常。

### sandbox（2）

36. [`sandbox/__init__.py`](../../../../../jiuwenswarm/server/sandbox/__init__.py)：仅版权行，空包，无符号/状态。
37. [`sandbox/jiuwenbox_runner.py`](../../../../../jiuwenswarm/server/sandbox/jiuwenbox_runner.py)：子进程生命周期。顶层 `_resolve_jiuwenbox_src_dir() -> Optional[Path]`（`:40`）、`_try_set_pdeathsig() -> None`（`:54`）、`JiuwenBoxRunner`（`:70`）。类关键签名：`instance(cls) -> JiuwenBoxRunner` `:98`、`is_healthy(timeout=0.3) -> bool` async `:135`、`fetch_health(timeout=0.3) -> dict | None` async `:146`、`ensure_running(host, port, *, policy_json="", startup_timeout=15.0, ownership="internal") -> str` async `:161`、`stop() -> None` async `:435`。singleton/lock/pump tasks；external ownership 禁止接管，internal 可重启。

### transports（3）

38. [`transports/__init__.py`](../../../../../jiuwenswarm/server/transports/__init__.py)：重导出 `ResponseSink/SSESink/UnaryHTTPSink/WSSink`；无定义/状态。
39. [`transports/push_registry.py`](../../../../../jiuwenswarm/server/transports/push_registry.py)：subscriber 注册表。顶层 `make_ws_push_subscriber_id(ws: Any) -> str`（`:57`）、`_Subscriber` frozen dataclass（`:68`，`matches(msg) -> bool` `:93`）、`PushRegistry`（`:106`）、`get_push_registry() -> PushRegistry`（`:281`）。类 `register(...)` async `:130`、`unregister(subscriber_id)` async `:170`、`push_reverse_rpc(wire) -> int` async `:189`、`push(wire) -> int` async `:220`；async lock 保护 registry/owner，发送在快照上完成，避免持锁 I/O。
40. [`transports/sink.py`](../../../../../jiuwenswarm/server/transports/sink.py)：响应出口抽象。顶层 `ResponseSink(Protocol)`（`:30`，send_unary/chunk/error/wire `:33-54`）、`_error_response(...) -> AgentResponse`（`:61`）、`WSSink`（`:70`）、`UnaryHTTPSink`（`:107`）、`SSESink`（`:229`）。WS 用传入 send lock；Unary 保存 frames/last_frame；SSE 是 bounded queue + sentinel，均执行 wire budget。

### utils（3）

41. `utils/__init__.py`：0 字节空文件；无符号/状态。
42. [`utils/diff_service.py`](../../../../../jiuwenswarm/server/utils/diff_service.py)：会话 turn/git/file-op 差异与恢复服务。顶层 `DiffHistoryExpiredError(RuntimeError)`（`:54`）、`DiffService`（`:58`）、`get_diff_service() -> DiffService`（`:2363`）。主要公开签名：`get_turn_diffs(self, session_id, project_dir=None) -> list[dict]` `:64`、`get_turn_diff_summaries(...) -> list[dict]` `:84`、`get_turn_diff(...) -> dict | None` `:117`、`mark_turn_discarded(...)` `:462`、`unmark_turn_discarded(...)` `:496`、`resolve_project_dir(session_id) -> str | None` `:648`、`get_git_diff(self, project_dir, *, ...) -> dict` `:1806`、`get_files_to_restore(...)` `:1978`、`get_files_to_redo(...)` `:2050`、`truncate_file_ops_by_timestamp(...)` `:2171`、`restore_rewound_entries_by_timestamp(...)` `:2284`。内部按 session 读写 change-set/snapshot JSON，扫描 agent history，调用只读 git 子命令并解析 numstat/name-status/porcelain/hunks/untracked；文件历史过期抛专用异常，git 失败多降为 None/空结果。模块 singleton 共享但持久一致性依赖文件写入。
43. [`utils/stream_utils.py`](../../../../../jiuwenswarm/server/utils/stream_utils.py)：把多种 runtime chunk 归一为前端 payload。精确顶层签名：`_propagate_stream_source_id(src_payload: Any) -> dict[str, Any]` `:13`；`parse_stream_chunk(chunk: Any, *, _has_streamed_content: bool = False) -> dict[str, Any] | None` `:28`；`_parse_dict_chunk(chunk: dict[str, Any], _has_streamed_content: bool) -> dict | None` `:65`；`_serialize_chunk_recursive(obj: Any) -> Any` `:126`；`_parse_typed_chunk(chunk: Any, _has_streamed_content: bool) -> dict | None` `:135`；`parse_ask_user_question_payload(payload: Any) -> dict[str, Any]` `:497`；`_parse_interaction_payload(payload) -> dict | None` `:511`；`_find_interaction_payloads(obj, *, ...)` `:533`；`_find_interaction_payload(obj, *, ...)` `:590`；`_parse_event_typed_chunk(chunk) -> dict` `:601`；`_serialize_value(value) -> Any` `:627`；`_parse_response_chunk(chunk, _has_streamed_content) -> dict | None` `:641`。纯转换，无状态；忽略不可展示中间块，保留 interaction/ask-user/source id，递归序列化未知对象。

## 7. 关键疑点与文档建议

1. **入口无统一认证证据。** 本范围未见 bearer/session identity 校验。Origin/CORS 只约束浏览器，企业 skills guard 只约束一类方法，permissions handler 自身也未见管理员校验。若部署边界依赖网关，正式文档必须明确“认证在何处终止”。
2. **SSE reverse-RPC 能力由 header 声明。** `x-jiuwen-push-consumer: gateway` 即可使该 subscriber 成为 ACP reverse-RPC owner（[`agent_http_routes.py:632`](../../../../../jiuwenswarm/server/agent_http_routes.py#L632)）；源码注释也指出当前是 trusted-header 约定。应明确只允许可信反向代理注入。
3. **HTTP 常规路由吞非法 JSON。** `_safe_json_body` 把解析失败当 `{}`，而 `/e2a` 返回 400，协议行为不一致。
4. **传输间互锁不完整。** 所有 HTTP 共用 connection id `http`，但 WS 使用各自 id；同一 session 的 HTTP 与 WS switch 不互斥（[`agent_http_server.py:82`](../../../../../jiuwenswarm/server/agent_http_server.py#L82) 注释明确说明）。
5. **route 与显式 handler 不能一一对应。** 181 条 REST route 并不代表 181 个 `handlers/*.py` 函数；很多操作有意进入 `_default` 和 runtime。正式文档应分开写“外部可调用操作”与“本层显式处理器”。
6. **若干兼容/mock 行为需要标注。** `COMMAND_CHROME`、`COMMAND_RESUME`、`COMMAND_SESSION` 当前是 mock/兼容响应；scheduler 未知 action 仍 `ok=True`；`COMMAND_BTW` 缺问题也以成功 envelope 包含失败状态。
7. **错误语义有折叠。** HTTP 会按 message 关键字猜测泛化错误状态，且 `SANDBOX_BAD_REQUEST` 映射 503；handler 中仍有大量仅 `{error}`、无 code 的失败。因此客户端不能只依赖 HTTP status 精确识别域错误。
8. **push 失败策略不对称。** WS push sink 把普通发送异常转为 `False`，且 WS subscriber 配置不因 stall 超时移除；SSE 会在 5 秒超时/失败时移除。坏 WS 主要依赖连接 finally 清理。

## 8. 取证计数

- 范围文件：43（13 个 `server/*.py` + gateway_push 3 + handlers 15 + hooks 3 + sandbox 2 + transports 3 + utils 3 + `pipeline/tool_concurrency/wire_parse/wire_truncate/ws_send` 已包含在 13 个顶层计数；按实际枚举总数 43）。
- HTTP：181 条 `RouteSpec` + 7 条特殊路由。
- 显式分派：79 条静态 `HANDLERS` + 17 条 permissions 动态注册 = 96 条。
- 空/仅包说明文件：[`sandbox/__init__.py`](../../../../../jiuwenswarm/server/sandbox/__init__.py)（仅版权）、`utils/__init__.py`（0 字节）、[`hooks/__init__.py`](../../../../../jiuwenswarm/server/hooks/__init__.py)（仅 docstring）；其余 `__init__.py` 均有重导出或惰性导出行为。
