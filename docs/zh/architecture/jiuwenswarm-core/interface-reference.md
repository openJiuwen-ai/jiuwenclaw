# JiuwenSwarm 核心接口说明总览

## 1. 使用方式

本页解释接口的协议层次、共同字段、调用约束和错误语义。每个 Python 文件的类、字段、函数、方法和精确签名位于四份明细：

- [根包、公共基础与实例管理 API](interfaces/01-root-common-instance-api.md)
- [Server 入口、协议与 Handler API](interfaces/02-server-protocol-api.md)
- [Server Runtime Core API](interfaces/03-runtime-core-api.md)
- [Skill 与 Skill Turbo Runtime API](interfaces/04-skill-runtime-api.md)

明细由当前源码 AST 生成，包含内部符号；本页只把真正的调用契约串起来。

## 2. 接口稳定性分级

| 级别 | 识别方式 | 兼容预期 |
| --- | --- | --- |
| 外部协议 | CLI entry point、HTTP 路由、WS/E2A wire、`ReqMethod`/`EventType` | 影响独立进程、Gateway、Web/IM 客户端，应优先保持兼容或显式版本化。 |
| 跨模块服务接口 | `RequestContext`、`ResponseSink`、AgentManager/JiuWenSwarm 公有方法、provider/rail/PlanNode 抽象 | 仓库内多个模块依赖；改签名前需要检索调用方并更新测试。 |
| 包级重导出 | `instance_manager.__all__`、延迟 `__getattr__`、部分 `__init__.py` | 多用于历史导入路径兼容，不能因“实现不在本文件”就视为无用。 |
| 内部实现接口 | 名称以下划线开头、仅单文件调用的 helper、module-level cache | 不承诺外部稳定，但往往承担并发/回退不变量，修改仍需行为测试。 |

## 3. CLI 与进程入口

入口注册见 [`pyproject.toml`](../../../../pyproject.toml#L130)。本次范围内的主要命令为：

| 命令 | Python 入口 | 输入 | 结果/副作用 |
| --- | --- | --- | --- |
| `jiuwenswarm-start` | [`start_services.main()`](../../../../jiuwenswarm/start_services.py#L1063) | `mode={all,app,web,dev}`；`--name`、`--list`、`--status`、`--stop`、`--restart` | 解析/持久化端口组，拉起或停止进程；以进程退出码表达结果。 |
| `jiuwenswarm-app` | [`app.main()`](../../../../jiuwenswarm/app.py#L49) | `--dotenv`、`--name` | 拉起 AgentServer + Gateway；任一子进程结束后终止其余进程并转发退出码。 |
| `jiuwenswarm-agentserver` | [`app_agentserver.main()`](../../../../jiuwenswarm/server/app_agentserver.py#L450) | `--port/-p`、`--name`、`--dotenv` | 启动 WS，按配置可选启动 HTTP；信号触发有序关闭。 |
| `jiuwenswarm-init` | [`init_workspace.main()`](../../../../jiuwenswarm/init_workspace.py#L1) | 初始化/命名实例相关参数 | 创建/迁移数据目录、模板配置和实例记录。 |

实例管理的可复用 Python API 由 [`instance_manager.__all__`](../../../../jiuwenswarm/instance_manager/__init__.py#L101) 定义，包含配置模型、名称校验、端口分配、YAML、PID/锁、状态和 bootstrap `.env`。

## 4. WebSocket / E2A 接口

### 4.1 入站模型

WebSocket 接收 UTF-8 JSON 文本或字节。标准路径是 [`E2AEnvelope`](../../../../jiuwenswarm/common/e2a/models.py#L87)，关键字段如下：

| 字段 | 类型/缺省 | 语义 |
| --- | --- | --- |
| `protocol_version` | `str`, 当前 `1.0` | E2A wire 版本。 |
| `request_id` | `str \| null` | 请求与全部响应 chunk 的主关联键。 |
| `method` | `str \| null` | 通常是 `ReqMethod.value`，如 `chat.send`。 |
| `params` | `dict` | 唯一业务参数字典；文本、content blocks、附件和控制参数都在此。 |
| `session_id` | `str \| null` | 持久会话与运行时串行化键。 |
| `channel` / `user_id` / `chat_id` | optional | 渠道和触发身份。 |
| `service_id` / `agent_id` / `workspace_key` | optional | 企业/OfficeClaw 租户与工作区键，只从信封顶层读取。 |
| `agent_ref` | `dict \| null` | V2 Agent 路由引用，响应侧原样回带或由 Team 事件设置。 |
| `is_stream` | `bool` | 选择 unary 或 chunk 流。 |
| `provenance` | `E2AProvenance` | 记录原生 E2A 或 ACP/A2A 转换来源。 |
| `channel_context` / `metadata` | `dict` | 规范字段之外的路由/扩展上下文；内部保留键不会下沉到业务 metadata。 |

[`parse_inbound(raw) -> ParseResult`](../../../../jiuwenswarm/server/wire_parse.py#L158) 的兼容顺序是：JSON 解码 → E2A `from_dict` → E2A-to-AgentRequest；E2A 结构解析失败时回退旧 `AgentRequest` 形状。JSON 错误和未知 E2A method 都返回编码后的错误帧，不由 parser 直接写传输。

旧载荷对应 [`AgentRequest`](../../../../jiuwenswarm/common/schema/agent.py#L67)：

```text
request_id, channel_id, session_id, chat_id,
service_id, agent_id, workspace_key,
req_method, params, is_stream, timestamp,
metadata, enable_memory, permission_context, agent_ref
```

`req_method` 字符串必须能构造 [`ReqMethod`](../../../../jiuwenswarm/common/schema/message.py#L10)；旧载荷中非法值会在解析阶段抛出并由请求外层错误处理。

### 4.2 出站模型

业务层产生 [`AgentResponse`](../../../../jiuwenswarm/common/schema/agent.py#L91) 或 [`AgentResponseChunk`](../../../../jiuwenswarm/common/schema/agent.py#L104)。wire 层转换成 `E2AResponse`，典型字段是：

```text
protocol_version, response_id, request_id, sequence,
is_final, status, response_kind, timestamp,
body, metadata, channel, agent_ref, provenance, is_stream
```

`response_id` 标识一次响应流，`sequence` 标识顺序，`is_final` 表示终帧；业务事件种类主要落在 `response_kind` 或 `body.event_type`。转换失败仍返回 E2A 错误并保留 legacy blob，详见 [`wire_codec.py`](../../../../jiuwenswarm/common/e2a/wire_codec.py#L232)。

### 4.3 请求与事件枚举

- [`ReqMethod`](../../../../jiuwenswarm/common/schema/message.py#L10) 是客户端可请求的操作空间。并非所有值都由 AgentServer 的表驱动 Handler 处理；部分由默认 `JiuWenSwarm` 控制 RPC 路由或 Gateway/Web 层处理。
- [`EventType`](../../../../jiuwenswarm/common/schema/message.py#L243) 是 AgentServer 产生的业务事件空间，包含 chat delta/reasoning/final/error、tool、todo/task、plan approval、Team、workflow、heartbeat 等。
- [`Mode`](../../../../jiuwenswarm/common/schema/message.py#L285) 是运行模式值对象；`agent.plan`、`agent.fast` 和裸 `plan/fast` 在解析时归一为 `agent`。

## 5. HTTP / REST / SSE 接口

### 5.1 启用与基础路径

HTTP 默认关闭。配置入口是 `config.yaml.http_server`，环境变量 `AGENT_HTTP_ENABLED/HOST/PORT` 优先；解析函数为 [`resolve_http_server_settings()`](../../../../jiuwenswarm/server/agent_http_server.py#L234)。基础前缀为 `/api/v1`，OpenAPI 位于 `/api/v1/openapi.json`，交互文档位于 `/api/v1/docs`。

HTTP 与 WS 共享 `AgentRequest → pipeline → Handler/Agent`，不是另一套业务实现。声明式路由全集见 [`ROUTES`](../../../../jiuwenswarm/server/agent_http_routes.py#L82)，分组如下：

- 初始化、会话、历史、对话和 session commands；
- Agent 定义、Team、Skill/Skill 来源/Skill evolution；
- Extension、Plugin、Hook、Harness packages；
- Schedule/Issue、Permissions、配置与运维；
- Symphony、Channel 配置、Updater、Heartbeat。

完整动词/路径/`ReqMethod` 映射见 [Server 协议分册](modules/02-server-entry-handlers.md#http-rest-路由矩阵)。

### 5.2 特殊路由

| 方法与路径 | 语义 |
| --- | --- |
| `GET /api/v1/health` | 返回 `status=ready`，只证明 HTTP 应用可响应。 |
| `GET /api/v1/events/stream` | SSE 服务端主动推送订阅；可按 `session_id`/`channel_id` 收窄。`X-Jiuwen-Push-Consumer: gateway` 只标记反向 RPC 能力，不是强认证。 |
| `POST /api/v1/chat/completions` | `chat.send`；`Accept: text/event-stream` 或 `enable_streaming=true` 时返回 SSE。 |
| `POST /api/v1/chat/resume` | `chat.resume`；流式选择规则同上。 |
| `GET /api/v1/sessions/{session_id}/history/stream` | 以 SSE 返回 `history.get` 流。 |
| `POST /api/v1/rpc/{method}` | 对任何合法 `ReqMethod` 的通用透传；未知 method 返回 404/`UNKNOWN_METHOD`。 |
| `POST /api/v1/e2a` | 请求体是完整 E2A 信封；信封 `is_stream` 或 SSE Accept 决定流式。 |

### 5.3 参数合并与身份头

[`collect_params()`](../../../../jiuwenswarm/server/agent_http_routes.py#L454) 合并 query、path 和 JSON body，优先级为 `body > path > query`。非法 JSON body 当前会被记录并按空字典继续，而不是自动返回 400；`/e2a` 是例外，它显式返回 `BAD_REQUEST`。

[`request_context()`](../../../../jiuwenswarm/server/agent_http_routes.py#L465) 识别：

| Header | 用途 |
| --- | --- |
| `X-Request-Id` | 调用方提供关联键；缺省生成 `http_<16 hex>`。所有 HTTP 响应回带该头。 |
| `X-Channel-Id` | 缺省 `web`。 |
| `X-Session-Id` | 路径没有 `session_id` 时的后备。 |
| `X-User-Id`、`X-Group-Id`、`X-Bot-Id`、`X-Gateway-Id` | 规范化为用户与 routing metadata。 |
| `X-Service-Id`、`X-Agent-Id`、`X-Workspace-Key` | 企业租户和工作区路由。 |

路由的 `param_defaults` 只在调用方没有提供真值时补齐；创建 session 的 `create_token` 默认取 request id，使同一 `X-Request-Id` 重试具备幂等键。

### 5.4 HTTP 响应与状态码

成功响应：

```json
{"request_id":"...","ok":true,"data":{},"metadata":{}}
```

失败响应：

```json
{"request_id":"...","ok":false,"error":{"code":"...","message":"...","details":{}}}
```

[`frame_to_http_envelope()`](../../../../jiuwenswarm/server/agent_http_server.py#L378) 先按结构化业务错误码映射 HTTP 状态；通用错误码再用 message 关键词区分 400/401/403/404/409，无法判断时为 500。创建类声明式路由可把成功 200 改成 201。

HTTP CORS 优先级是 env > config > 当前实例前端端口推导；`*` 会强制关闭 credentials。该入口当前没有独立鉴权层，CORS 不能视为身份认证。

## 6. Handler 接口

### 6.1 统一签名

[`HandlerSpec`](../../../../jiuwenswarm/server/dispatch.py#L31) 约定处理函数为：

```python
async def handle_x(ctx: RequestContext, *bound_args, **bound_kwargs) -> None:
    ...
```

Handler 不返回 HTTP/WS 对象；它通过 `ctx.sink` 发结果。`dispatch_to_handler(...) -> bool` 返回是否命中注册表。`stream_fn` 可按 `request.is_stream` 替换实现；当前 `history.get` 使用该能力。

### 6.2 RequestContext

[`RequestContext`](../../../../jiuwenswarm/server/context.py#L92) 的字段：

| 字段 | 类型 | 约束 |
| --- | --- | --- |
| `request` | `AgentRequest` | 已完成 wire/HTTP 规范化。 |
| `sink` | `ResponseSink` | 传输实现；Handler 不应检查其具体类型。 |
| `connection_id` | `str` | 跨同一连接请求稳定；供 ACP capability 和 switch 互斥。 |
| `services` | `AgentServerServices` | 只允许访问 `SERVICE_MEMBERS` 白名单。 |

便捷属性 `params/request_id/channel_id/session_id` 对缺省值做安全归一。要增加业务依赖，应先在 [`SERVICE_MEMBERS`](../../../../jiuwenswarm/server/context.py#L28) 登记公有门面名。

### 6.3 ResponseSink

[`ResponseSink`](../../../../jiuwenswarm/server/transports/sink.py#L29) 是 runtime-checkable protocol：

```python
async def send_unary(resp: AgentResponse, *, response_id: str | None = None) -> bool
async def send_chunk(chunk: AgentResponseChunk, *, sequence: int, response_id: str | None = None) -> bool
async def send_error(request_id: str, message: str, *, code: str = "INTERNAL_ERROR", channel_id: str = "") -> bool
async def send_wire(wire: dict[str, Any]) -> bool
```

返回 `False` 表示原内容已因发送预算等原因降级；流式调用方必须停止继续发送。`send_wire` 是少数已经编码帧的逃生接口，不应成为普通业务默认路径。

## 7. Runtime 服务接口

### 7.1 AgentManager

[`AgentManager`](../../../../jiuwenswarm/server/runtime/agent_manager.py#L114) 的主要公有调用面：

| 方法 | 语义 |
| --- | --- |
| `initialize(channel_id, extra_config)` | 初始化默认配置/Agent 和预热能力。 |
| `create_session(channel_id, session_id)` | 创建或认领会话 id；与预热 create token 协作。 |
| `get_agent(channel_id, mode, project_dir, sub_mode)` | 异步创建/复用 cache-key 对应 facade，并登记借用。 |
| `get_agent_nowait(...)` | 仅查询已有实例，不触发创建。 |
| `pin_agent` / `unpin_agent` | 阻止/允许 retired 实例清理。 |
| `reload_agents_config` / `apply_sync_config` | 计算影响、刷新 fingerprint、标记/重建实例并广播包变化。 |
| `process_message` / `process_message_stream` | 根据请求解析 cache key 并委托 facade。 |
| `cleanup_session_runtime(session_id=...)` | 只清理一个会话在全部相关 facade/adapter 中的活跃状态。 |
| `cancel_all_inflight_work` / `cleanup` | 连接/进程级取消和资源回收。 |

精确可选参数和返回类型见 [Runtime Core API](interfaces/03-runtime-core-api.md#jiuwenswarmserverruntimeagent_managerpy)。

### 7.2 TenantAgentPool 与 RuntimeScope

[`TenantAgentPool`](../../../../jiuwenswarm/server/runtime/tenant_agent_pool.py#L63) 以 `(agent_id, service_id, workspace_key)` 选择 AgentManager；`extract_ids()` 从 `AgentRequest` 提取，`resolve_control_rpc_tenant()` 为控制 RPC 补齐租户，`require_officeclaw_agent()` 在缺少企业目标时返回结构化失败。

[`RuntimeScopeKey`](../../../../jiuwenswarm/server/runtime/runtime_scope.py#L21) 提供 `tenant`、`session_key`、`with_session` 与 `from_request/from_adapter`，用于需要稳定 hash key 的缓存/ledger，不应以散装字符串元组重复实现。

### 7.3 JiuWenSwarm facade

[`JiuWenSwarm`](../../../../jiuwenswarm/server/runtime/agent_adapter/interface.py#L1027) 的稳定职责接口是：

- `create_instance` / `ensure_instance`：按当前模式懒建 Adapter/Agent；
- `reload_agent_config` / package-change：热更新并延迟应用忙碌 Adapter 的变更；
- `prepare_session`：在运行前装载/恢复会话；
- `process_message` / `process_message_stream`：统一业务入口，先短路控制 RPC，再委托 Adapter；
- `compress_context`、`get_context_usage`、`generate_recap`、`compact_partial`、`generate_btw_answer`：会话维护能力；
- `cleanup_session_runtime`、`cancel_inflight_work`、`cleanup`：从窄到宽的回收接口。

调用方通常不直接依赖 `interface_deep.JiuWenSwarmDeepAdapter`；该类是 facade 的实现适配层。

### 7.4 SessionManager

[`SessionManager`](../../../../jiuwenswarm/server/runtime/session/session_manager.py#L23) 的关键接口：

```python
async def ensure_session_processor(session_id: str) -> None
async def submit_task(session_id: str, priority: int, task_factory, ...) -> asyncio.Future
async def submit_and_wait(session_id: str, ...) -> Any
async def cancel_session_task(session_id: str, ...) -> bool
async def close_session(session_id: str, ...) -> None
async def close_all_sessions() -> None
```

`task_factory` 只有轮到该 session 执行时才调用。调用方不应绕过 processor 直接并发修改同一 Agent session 状态。

## 8. Skill 接口

### 8.1 SkillManager 与 SourceRegistry

[`SkillManager`](../../../../jiuwenswarm/server/runtime/skill/skill_manager.py#L473) 的 `handle_skills_*` / `handle_plugins_*` 方法接收 `params: dict` 并返回可序列化 `dict`，由 `JiuWenSwarm._SKILL_ROUTES` 映射 `ReqMethod` 到方法名。控制方法通常不要求重型 Adapter，但安装完成后会通过 hook 刷新运行时 Skill rails。

[`SourceRegistry`](../../../../jiuwenswarm/server/runtime/skill/source_registry.py#L36) 的扩展契约：

```python
register(source_id, provider_factory, config, ...)
bind_extension(source_id, extension_name)
list() -> list[SourceDescriptor]
get_config(source_id) -> SourceConfig
async get(source_id, capability) -> SkillSourceProvider
async close() -> None
```

Provider 的可用 capability 决定 search/check_updates/get_artifact 等操作是否可调用；registry 负责实例化和关闭生命周期。

### 8.2 SkillDev

[`SkillDevService.handle(request)`](../../../../jiuwenswarm/server/runtime/skill/skilldev/service.py#L65) 返回 `AsyncIterator[AgentResponseChunk]`。请求方法统一映射到 start/respond/status/download/cancel/file list/read；pipeline checkpoint 后可通过 task id 恢复。阶段接口是 [`StageHandler.execute(ctx) -> StageResult`](../../../../jiuwenswarm/server/runtime/skill/skilldev/stages/base.py#L24)。

### 8.3 Skill Turbo

核心调用面：

```python
SkillTurbo.run(task, inputs) -> Any
SkillTurbo.run_stream(task, inputs, ...) -> AsyncIterator[AgentResponseChunk]
SkillTurbo.resume_stream(...) -> AsyncIterator[AgentResponseChunk]
SkillTurboPlanner.plan(task, context=None) -> str | None
SkillTurboExecutor.execute_plan(plan_code, inputs) -> Any
SkillTurboExecutor.execute_plan_stream(plan_code, inputs, ...) -> AsyncIterator[AgentResponseChunk]
```

[`PlanNode`](../../../../jiuwenswarm/server/runtime/skill_turbo/plan_node.py#L42) 的子类只覆写 `_execute`/`_execute_stream`；公共 `run` 包装生命周期，`execute_subplan` 维护深度、skip/resume 与回调。工具、LLM、权限、artifact 和事件必须经 executor 注入的 callback 访问。

计划代码在执行前必须通过 [`PlanCodeValidator.validate_or_raise()`](../../../../jiuwenswarm/server/runtime/skill_turbo/validator.py#L239)。不同来源使用不同 `CodeValidationPolicy`，内置代码与动态生成代码的允许导入集合不同。

## 9. 配置、Secret 与兼容接口

- `common.config` 对外主要是 `get_config/get_merged_config_dict/update_config` 类读取/更新能力；缓存键包含 overlay/文件 stamp，调用方不要直接长期保存原字典引用。
- `common.local_env_config` 的 tip bag 与真正 `os.environ` 分轨；租户执行应通过其绑定/查询 API，不能把业务 secret 注入进程全局环境。
- `common.secrets` 以 envelope/store/registry/transform 分层；后端实现（env/file/default_file/db/gateway）是可替换持久化接口。
- `common.reasoning_config`/`reasoning_injector` 把统一 reasoning level 转为供应商 payload；`common.thinking` hook 负责 Agent/TaskTool 的请求级 thinking 传播。
- 根包 `llm_sse_patch`、`openjiuwen_*_patch` 和 `common.openjiuwen_rail_compat` 是显式兼容入口，调用应幂等；应用启动顺序已在 `app_agentserver` 固定。

## 10. 异步、异常与清理契约

- 所有流生成器的取消必须保留 `asyncio.CancelledError`；不要用通用 `except Exception` 把取消变成正常结束。
- Handler 写 sink 后通常返回 `None`；调用方不能依赖 Python 返回值判断业务成功。
- `send_* -> False` 是已降级信号，不是“socket 一定失败”；流式生产者据此停止后续输出。
- HTTP `AgentHTTPServer.start() -> bool` 自己吸收 bind/start 异常，`False` 表示仅 HTTP 不可用，WS 仍可继续。
- manager/facade/session 均有不同粒度 cleanup；删除会话不应直接调用进程级 cleanup。
- provider、后台 worker、history/metadata buffer 和 telemetry 都需要显式 close/flush；只取消顶层 serve task 不构成完整关闭。

## 11. 查找接口的推荐顺序

1. 从 [Server 协议分册](modules/02-server-entry-handlers.md) 找 HTTP/WS method 对应 Handler。
2. 从本页确定 Handler 使用的 service/facade 边界。
3. 在相应 AST API 明细中查精确签名和源码行。
4. 从[全量源码索引](source-inventory.md)定位同目录协作者和文件职责。
5. 对涉及并发、持久化、兼容补丁或 Skill 安装的修改，再阅读对应[模块设计说明书](module-design.md)分节与专项分册。
