# Server 入口、协议与 Handler Python API

覆盖 `server` 的 HTTP/WebSocket 入口、分发、Handler、Hook、传输、沙箱和通用服务辅助接口（不含 `runtime`）。

> 签名与行号取自当前源码 AST。这里同时列出公开和内部顶级接口；名称以下划线开头者是实现细节，不承诺稳定兼容。行为语义与调用约束请结合对应模块设计分册阅读。

## `jiuwenswarm/server/__init__.py`

[打开源码](../../../../../jiuwenswarm/server/__init__.py#L1)

**模块职责：** AgentServer 模块.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `__all__` | `未显式标注` | [L13](../../../../../jiuwenswarm/server/__init__.py#L13) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __getattr__(name: str) -> Any` | 源码未提供函数级文档字符串。 | [L16](../../../../../jiuwenswarm/server/__init__.py#L16) |

## `jiuwenswarm/server/agent_http_routes.py`

[打开源码](../../../../../jiuwenswarm/server/agent_http_routes.py#L1)

**模块职责：** HTTP 路由表：RESTful资源路径 → ``ReqMethod``。

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `logger` | `未显式标注` | [L38](../../../../../jiuwenswarm/server/agent_http_routes.py#L38) |
| `REQUEST_ID_PLACEHOLDER` | `未显式标注` | [L43](../../../../../jiuwenswarm/server/agent_http_routes.py#L43) |
| `ROUTES` | `list[RouteSpec]` | [L82](../../../../../jiuwenswarm/server/agent_http_routes.py#L82) |

### [`class RequestContext`](../../../../../jiuwenswarm/server/agent_http_routes.py#L47)

HTTP 请求身份与租户键。

装饰器：`@dataclass(frozen=True)`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `request_id` | `str` | `—` | [L50](../../../../../jiuwenswarm/server/agent_http_routes.py#L50) |
| `channel_id` | `str` | `—` | [L51](../../../../../jiuwenswarm/server/agent_http_routes.py#L51) |
| `session_id` | `str \| None` | `—` | [L52](../../../../../jiuwenswarm/server/agent_http_routes.py#L52) |
| `user_id` | `str \| None` | `—` | [L53](../../../../../jiuwenswarm/server/agent_http_routes.py#L53) |
| `routing` | `dict[str, str]` | `—` | [L54](../../../../../jiuwenswarm/server/agent_http_routes.py#L54) |
| `tenant_ids` | `dict[str, str]` | `—` | [L55](../../../../../jiuwenswarm/server/agent_http_routes.py#L55) |

### [`class RouteSpec`](../../../../../jiuwenswarm/server/agent_http_routes.py#L59)

一条声明式路由。

装饰器：`@dataclass(frozen=True)`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `verb` | `str` | `—` | [L72](../../../../../jiuwenswarm/server/agent_http_routes.py#L72) |
| `path` | `str` | `—` | [L73](../../../../../jiuwenswarm/server/agent_http_routes.py#L73) |
| `method` | `str` | `—` | [L74](../../../../../jiuwenswarm/server/agent_http_routes.py#L74) |
| `status` | `int` | `200` | [L75](../../../../../jiuwenswarm/server/agent_http_routes.py#L75) |
| `param_defaults` | `Mapping[str, Any]` | `field(default_factory=dict)` | [L76](../../../../../jiuwenswarm/server/agent_http_routes.py#L76) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `async def _safe_json_body(request: Any) -> dict[str, Any]` | 读取 JSON body；空体或非法 JSON 返回 {}。 | [L436](../../../../../jiuwenswarm/server/agent_http_routes.py#L436) |
| `async def collect_params(request: Any) -> dict[str, Any]` | 合并 query / path / body 为单一 ``params`` 字典。 | [L454](../../../../../jiuwenswarm/server/agent_http_routes.py#L454) |
| `def request_context(request: Any) -> RequestContext` | 从请求头/路径提取身份与租户键。 | [L465](../../../../../jiuwenswarm/server/agent_http_routes.py#L465) |
| `def _envelope_wants_stream(request: Any, envelope: dict[str, Any]) -> bool` | ``/e2a`` 是否该走 SSE。 | [L510](../../../../../jiuwenswarm/server/agent_http_routes.py#L510) |
| `def wants_stream(request: Any, params: dict[str, Any]) -> bool` | 判定是否走 SSE：``Accept: text/event-stream`` 或 ``enable_streaming``。 | [L524](../../../../../jiuwenswarm/server/agent_http_routes.py#L524) |
| `def build_fastapi_app(server: AgentHTTPServer) -> Any` | 构建 FastAPI 应用并注册全部路由。 | [L537](../../../../../jiuwenswarm/server/agent_http_routes.py#L537) |
| `def _register_special_routes(app: Any, server: AgentHTTPServer) -> None` | 注册流式与通用透传接口。 | [L599](../../../../../jiuwenswarm/server/agent_http_routes.py#L599) |
| `async def _invoke_raw_envelope(server: AgentHTTPServer, envelope: dict[str, Any], request_id: str) -> Any` | 直接把 E2A 信封交给共享入口（非流式）。 | [L817](../../../../../jiuwenswarm/server/agent_http_routes.py#L817) |

## `jiuwenswarm/server/agent_http_server.py`

[打开源码](../../../../../jiuwenswarm/server/agent_http_server.py#L1)

**模块职责：** AgentServer的HTTP+SSE入口

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `logger` | `未显式标注` | [L18](../../../../../jiuwenswarm/server/agent_http_server.py#L18) |
| `DEFAULT_HTTP_HOST` | `未显式标注` | [L20](../../../../../jiuwenswarm/server/agent_http_server.py#L20) |
| `DEFAULT_HTTP_PORT` | `未显式标注` | [L21](../../../../../jiuwenswarm/server/agent_http_server.py#L21) |
| `API_PREFIX` | `未显式标注` | [L22](../../../../../jiuwenswarm/server/agent_http_server.py#L22) |
| `PORT_SCAN_RANGE` | `未显式标注` | [L25](../../../../../jiuwenswarm/server/agent_http_server.py#L25) |
| `PORT_SCAN_STEP` | `未显式标注` | [L26](../../../../../jiuwenswarm/server/agent_http_server.py#L26) |
| `_ENTERPRISE_SKILL_ALLOWED` | `未显式标注` | [L31](../../../../../jiuwenswarm/server/agent_http_server.py#L31) |
| `HTTP_CONNECTION_ID` | `未显式标注` | [L82](../../../../../jiuwenswarm/server/agent_http_server.py#L82) |
| `SHUTDOWN_TIMEOUT` | `未显式标注` | [L85](../../../../../jiuwenswarm/server/agent_http_server.py#L85) |
| `SHUTDOWN_TIMEOUT_ON_START_FAILURE` | `未显式标注` | [L87](../../../../../jiuwenswarm/server/agent_http_server.py#L87) |
| `ERROR_CODE_STATUS` | `dict[str, int]` | [L90](../../../../../jiuwenswarm/server/agent_http_server.py#L90) |
| `GENERIC_ERROR_CODES` | `未显式标注` | [L107](../../../../../jiuwenswarm/server/agent_http_server.py#L107) |
| `MESSAGE_STATUS_HINTS` | `tuple[tuple[str, int], ...]` | [L110](../../../../../jiuwenswarm/server/agent_http_server.py#L110) |

### [`class AgentHTTPServer`](../../../../../jiuwenswarm/server/agent_http_server.py#L427)

HTTP/SSE 服务端；持有 ``AgentWebSocketServer`` 实例以复用其 handler。

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self, ws_server: Any, host: str = DEFAULT_HTTP_HOST, port: int = DEFAULT_HTTP_PORT) -> None` | 源码未提供方法级文档字符串。 | [L430](../../../../../jiuwenswarm/server/agent_http_server.py#L430) |
| `@property def port(self) -> int` | 实际监听的端口。 | [L443](../../../../../jiuwenswarm/server/agent_http_server.py#L443) |
| `def _make_ctx(self, sink: Any, request: Any) -> Any` | 构造与 WS 侧同形的 ``RequestContext`` | [L453](../../../../../jiuwenswarm/server/agent_http_server.py#L453) |
| `async def dispatch_raw_envelope(self, sink: Any, raw: str) -> None` | 把**原始 JSON 字节**按与 WS 完全相同的规则解析后交给汇合点。 | [L464](../../../../../jiuwenswarm/server/agent_http_server.py#L464) |
| `async def _dispatch_request(self, sink: Any, request: Any) -> None` | 分发构造造好的``AgentRequest``，发给pipeline | [L479](../../../../../jiuwenswarm/server/agent_http_server.py#L479) |
| `async def invoke_unary(self, method: str, params: dict[str, Any], *, request_id: str, session_id: str \| None = None, channel_id: str = 'web', user_id: str \| None = None, routing: dict[str, str] \| None = None, tenant_ids: dict[str, str] \| None = None) -> tuple[dict[str, Any], int]` | 非流式调用，返回 (响应体, 状态码)。 | [L486](../../../../../jiuwenswarm/server/agent_http_server.py#L486) |
| `async def iter_stream(self, method: str, params: dict[str, Any], *, request_id: str, session_id: str \| None = None, channel_id: str = 'web', user_id: str \| None = None, routing: dict[str, str] \| None = None, tenant_ids: dict[str, str] \| None = None) -> AsyncIterator[dict[str, Any]]` | 流式调用（结构化入口），产出 sse_starlette 所需的事件 dict。 | [L538](../../../../../jiuwenswarm/server/agent_http_server.py#L538) |
| `async def iter_raw_envelope(self, raw: str, *, request_id: str) -> AsyncIterator[dict[str, Any]]` | 流式调用（``/e2a`` 原始信封入口）。 | [L571](../../../../../jiuwenswarm/server/agent_http_server.py#L571) |
| `async def _pump_sse(self, sink: SSESink, run: Callable[[], Awaitable[None]], *, request_id: str, label: str) -> AsyncIterator[dict[str, Any]]` | 把「一个把结果写进 ``SSESink`` 的协程」变成 SSE 事件流。 | [L595](../../../../../jiuwenswarm/server/agent_http_server.py#L595) |
| `def _find_bindable_port(self) -> int \| None` | 端口被占时向上扫描，语义与实例端口组一致（+1000 步进） | [L654](../../../../../jiuwenswarm/server/agent_http_server.py#L654) |
| `def build_app(self) -> Any` | 源码未提供方法级文档字符串。 | [L664](../../../../../jiuwenswarm/server/agent_http_server.py#L664) |
| `async def start(self) -> bool` | 启动 HTTP 服务，返回是否成功。 | [L669](../../../../../jiuwenswarm/server/agent_http_server.py#L669) |
| `async def stop(self, *, timeout: float = SHUTDOWN_TIMEOUT) -> None` | 停止 HTTP 服务。 | [L745](../../../../../jiuwenswarm/server/agent_http_server.py#L745) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def _is_enterprise_skill_forbidden(method: str) -> bool` | AgentServer 侧防御性校验：企业版下拒绝白名单外的 ``skills.*`` 写操作。 | [L48](../../../../../jiuwenswarm/server/agent_http_server.py#L48) |
| `def resolve_error_status(code: str, message: str) -> int` | 错误码 + 描述 → HTTP 状态码。 | [L124](../../../../../jiuwenswarm/server/agent_http_server.py#L124) |
| `def resolve_cors_origins() -> tuple[list[str], bool]` | 允许跨域的来源 → ``(origins, allow_credentials)``。 优先级 **env > config.yaml > 按端口推导**： | [L140](../../../../../jiuwenswarm/server/agent_http_server.py#L140) |
| `def new_request_id() -> str` | 源码未提供函数级文档字符串。 | [L198](../../../../../jiuwenswarm/server/agent_http_server.py#L198) |
| `def is_port_available(host: str, port: int) -> bool` | 端口预检：能 bind 上即视为可用。 | [L202](../../../../../jiuwenswarm/server/agent_http_server.py#L202) |
| `def _as_bool(value: Any, default: bool = False) -> bool` | 源码未提供函数级文档字符串。 | [L220](../../../../../jiuwenswarm/server/agent_http_server.py#L220) |
| `def resolve_http_server_settings(agent_host: str) -> tuple[bool, str, int]` | 解析 HTTP 入口配置，返回 ``(enabled, host, port)``。 | [L234](../../../../../jiuwenswarm/server/agent_http_server.py#L234) |
| `def build_envelope_json(*, method: str, params: dict[str, Any], request_id: str, session_id: str \| None, channel_id: str, user_id: str \| None, is_stream: bool, routing: dict[str, str] \| None = None, tenant_ids: dict[str, str] \| None = None) -> str` | 组装与 WS 客户端等价的 E2A 信封 JSON。 | [L281](../../../../../jiuwenswarm/server/agent_http_server.py#L281) |
| `def build_agent_request(*, method: str, params: dict[str, Any], request_id: str, session_id: str \| None, channel_id: str, user_id: str \| None, is_stream: bool, routing: dict[str, str] \| None = None, tenant_ids: dict[str, str] \| None = None) -> Any` | 构造``AgentRequest``。 | [L317](../../../../../jiuwenswarm/server/agent_http_server.py#L317) |
| `def _frame_event_name(frame: dict[str, Any]) -> str` | 从 wire 帧推导 SSE ``event`` 名。 | [L358](../../../../../jiuwenswarm/server/agent_http_server.py#L358) |
| `def frame_to_http_envelope(frame: dict[str, Any] \| None, request_id: str) -> tuple[dict[str, Any], int]` | wire 帧 → (HTTP 响应体, HTTP 状态码)。 | [L378](../../../../../jiuwenswarm/server/agent_http_server.py#L378) |
| `def is_valid_req_method(method: str) -> bool` | 源码未提供函数级文档字符串。 | [L765](../../../../../jiuwenswarm/server/agent_http_server.py#L765) |

## `jiuwenswarm/server/agent_ws_server.py`

[打开源码](../../../../../jiuwenswarm/server/agent_ws_server.py#L1)

**模块职责：** AgentWebSocketServer - Gateway 与 AgentServer 之间的 WebSocket 服务端.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `logger` | `未显式标注` | [L157](../../../../../jiuwenswarm/server/agent_ws_server.py#L157) |
| `_INTERFACE_DEEP_MODULE` | `未显式标注` | [L159](../../../../../jiuwenswarm/server/agent_ws_server.py#L159) |
| `_startup_warmup_task` | `asyncio.Task[None] \| None` | [L160](../../../../../jiuwenswarm/server/agent_ws_server.py#L160) |

### [`class _GatewayWSPushSink`](../../../../../jiuwenswarm/server/agent_ws_server.py#L253)

Gateway WS 连接在 :class:`PushRegistry` 里的推送出口。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `__slots__` | `未显式标注` | `('_inner',)` | [L266](../../../../../jiuwenswarm/server/agent_ws_server.py#L266) |

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self, ws: Any, send_lock: asyncio.Lock) -> None` | 源码未提供方法级文档字符串。 | [L268](../../../../../jiuwenswarm/server/agent_ws_server.py#L268) |
| `async def send_wire(self, wire: dict[str, Any]) -> bool` | 源码未提供方法级文档字符串。 | [L271](../../../../../jiuwenswarm/server/agent_ws_server.py#L271) |
| `async def send_unary(self, resp: Any, *, response_id: str \| None = None) -> bool` | 源码未提供方法级文档字符串。 | [L280](../../../../../jiuwenswarm/server/agent_ws_server.py#L280) |
| `async def send_chunk(self, chunk: Any, *, sequence: int, response_id: str \| None = None) -> bool` | 源码未提供方法级文档字符串。 | [L283](../../../../../jiuwenswarm/server/agent_ws_server.py#L283) |
| `async def send_error(self, request_id: str, message: str, *, code: str = 'INTERNAL_ERROR', channel_id: str = '') -> bool` | 源码未提供方法级文档字符串。 | [L286](../../../../../jiuwenswarm/server/agent_ws_server.py#L286) |

### [`class AgentWebSocketServer`](../../../../../jiuwenswarm/server/agent_ws_server.py#L292)

Gateway 与 AgentServer 之间的 WebSocket 服务端（单例）.

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `_instance` | `ClassVar[AgentWebSocketServer \| None]` | `None` | [L304](../../../../../jiuwenswarm/server/agent_ws_server.py#L304) |

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self, host: str = '127.0.0.1', port: int = 18000, *, ping_interval: float \| None = 30.0, ping_timeout: float \| None = 300.0) -> None` | 源码未提供方法级文档字符串。 | [L306](../../../../../jiuwenswarm/server/agent_ws_server.py#L306) |
| `def set_proactive_engine(self, engine: Any) -> None` | Store the proactive engine instance for debug trigger interface. | [L353](../../../../../jiuwenswarm/server/agent_ws_server.py#L353) |
| `@staticmethod def _ws_capabilities_key(ws: Any) -> str` | 连接标识。 返回``str(id(ws))``而非``id(ws)``，与``RequestContext.connection_id`` 对齐 —— 业务层用``ctx.connection_id``写入、传输层用``ws``读取. | [L358](../../../../../jiuwenswarm/server/agent_ws_server.py#L358) |
| `def _set_acp_client_capabilities(self, connection_id: str, capabilities: dict[str, Any] \| None) -> None` | 源码未提供方法级文档字符串。 | [L365](../../../../../jiuwenswarm/server/agent_ws_server.py#L365) |
| `def _get_acp_client_capabilities(self, connection_id: str) -> dict[str, Any]` | 源码未提供方法级文档字符串。 | [L373](../../../../../jiuwenswarm/server/agent_ws_server.py#L373) |
| `def _set_ws_acp_client_capabilities(self, ws: Any, capabilities: dict[str, Any] \| None) -> None` | 源码未提供方法级文档字符串。 | [L377](../../../../../jiuwenswarm/server/agent_ws_server.py#L377) |
| `def _get_ws_acp_client_capabilities(self, ws: Any) -> dict[str, Any]` | 源码未提供方法级文档字符串。 | [L380](../../../../../jiuwenswarm/server/agent_ws_server.py#L380) |
| `def _clear_ws_acp_client_capabilities(self, ws: Any) -> None` | 源码未提供方法级文档字符串。 | [L383](../../../../../jiuwenswarm/server/agent_ws_server.py#L383) |
| `@classmethod def get_instance(cls, *, host: str = '127.0.0.1', port: int = 18000, ping_interval: float \| None = 30.0, ping_timeout: float \| None = 300.0) -> 'AgentWebSocketServer'` | 返回单例实例。 | [L387](../../../../../jiuwenswarm/server/agent_ws_server.py#L387) |
| `@classmethod def reset_instance(cls) -> None` | 重置单例（仅用于测试）。 | [L410](../../../../../jiuwenswarm/server/agent_ws_server.py#L410) |
| `@property def host(self) -> str` | 源码未提供方法级文档字符串。 | [L415](../../../../../jiuwenswarm/server/agent_ws_server.py#L415) |
| `@property def port(self) -> int` | 源码未提供方法级文档字符串。 | [L419](../../../../../jiuwenswarm/server/agent_ws_server.py#L419) |
| `async def start(self) -> None` | 启动 WebSocket 服务端，开始监听连接。优先使用 legacy.server.serve 以与 Gateway 的 legacy client 握手兼容. | [L424](../../../../../jiuwenswarm/server/agent_ws_server.py#L424) |
| `def _start_loop_lag_monitor(self) -> None` | 启动事件循环 lag 观测 task（验收用，不主动断连/不发应用心跳）。 | [L512](../../../../../jiuwenswarm/server/agent_ws_server.py#L512) |
| `async def _loop_lag_monitor(self) -> None` | 每隔约 1s 测量预期唤醒 vs 实际唤醒延迟。 | [L521](../../../../../jiuwenswarm/server/agent_ws_server.py#L521) |
| `async def _bootstrap_internal_jiuwenbox(self) -> None` | 启动时按 ``config.yaml::sandbox`` 自动拉起 jiuwenbox 子进程。 | [L560](../../../../../jiuwenswarm/server/agent_ws_server.py#L560) |
| `async def _stop_scheduler(self) -> None` | Stop the auto_harness scheduler. | [L723](../../../../../jiuwenswarm/server/agent_ws_server.py#L723) |
| `async def _process_request(self, *args: Any) -> Any` | 在握手阶段执行 Origin 校验，兼容 legacy/new websockets APIs。 | [L740](../../../../../jiuwenswarm/server/agent_ws_server.py#L740) |
| `async def stop(self) -> None` | 停止 WebSocket 服务端. | [L774](../../../../../jiuwenswarm/server/agent_ws_server.py#L774) |
| `async def _connection_handler(self, ws: Any) -> None` | 处理单个 Gateway WebSocket 连接，同一连接可并发处理多个请求. | [L818](../../../../../jiuwenswarm/server/agent_ws_server.py#L818) |
| `async def _handle_message(self, ws: Any, raw: str \| bytes, send_lock: asyncio.Lock) -> None` | 解析一条 JSON 请求并交给汇合点处理。 | [L940](../../../../../jiuwenswarm/server/agent_ws_server.py#L940) |
| `@staticmethod async def _trigger_before_ws_server_start_hook() -> None` | 在首次启动之前触发扩展；未初始化 ExtensionRegistry 时跳过。 | [L972](../../../../../jiuwenswarm/server/agent_ws_server.py#L972) |
| `@staticmethod async def _trigger_agent_server_started_hook() -> None` | 在agentserver启动成功触发扩展；未初始化 ExtensionRegistry 时跳过。 | [L988](../../../../../jiuwenswarm/server/agent_ws_server.py#L988) |
| `@staticmethod def _resolve_code_language() -> str` | Determine the display language for code mode plan approval messages. | [L1002](../../../../../jiuwenswarm/server/agent_ws_server.py#L1002) |
| `async def _prepare_session_switch_owner(self, *, channel_id: str, target_session_id: str, previous_session_id: str, params: dict[str, Any], reason: str) -> tuple[bool, str, Any, Any, Any]` | Resolve switch context and run product-owner prepare (team switch). | [L1014](../../../../../jiuwenswarm/server/agent_ws_server.py#L1014) |
| `async def _dispatch_session_switch_kvc(self, *, channel_id: str, target_session_id: str, previous_session_id: str, reason: str, context: Any, team_manager: Any, dispatch_signals: Any) -> None` | Optional KVC signals after the product owner has prepared the switch. | [L1072](../../../../../jiuwenswarm/server/agent_ws_server.py#L1072) |
| `async def _ensure_persistent_checkpointer_response(self, request: AgentRequest) -> AgentResponse \| None` | Return an error response when persistent checkpoint storage is unavailable. | [L1096](../../../../../jiuwenswarm/server/agent_ws_server.py#L1096) |
| `@staticmethod def _resolve_adapter(agent: Any) -> Any` | 从 JiuwenSwarm 中提取底层 Deep/Code Adapter (持 _sys_operation_card 的实例). | [L1122](../../../../../jiuwenswarm/server/agent_ws_server.py#L1122) |
| `@staticmethod def resolve_adapter(agent: Any) -> Any` | Public wrapper for :meth:`_resolve_adapter` (避开 protected-access). | [L1136](../../../../../jiuwenswarm/server/agent_ws_server.py#L1136) |
| `@staticmethod def _is_tcp_port_bindable(host: str, port: int) -> bool` | ``True`` 表示当前能在 ``host:port`` 上 ``bind`` 成功 (即没有被占用)。 | [L1141](../../../../../jiuwenswarm/server/agent_ws_server.py#L1141) |
| `@staticmethod def _pick_free_tcp_port(host: str) -> int` | 让内核挑一个空闲端口 (``bind`` 到 0); 仅用于绑定测试, 不会真正监听。 | [L1160](../../../../../jiuwenswarm/server/agent_ws_server.py#L1160) |
| `@staticmethod def _tenant_pool() -> TenantAgentPool` | 源码未提供方法级文档字符串。 | [L1173](../../../../../jiuwenswarm/server/agent_ws_server.py#L1173) |
| `async def send_push(self, msg) -> int` | AgentServer 主动向 Gateway 推送消息。 | [L1176](../../../../../jiuwenswarm/server/agent_ws_server.py#L1176) |
| `def get_agent(self)` | 获取 default agent 实例（向后兼容）. | [L1249](../../../../../jiuwenswarm/server/agent_ws_server.py#L1249) |
| `def get_agent_manager(self) -> AgentManager` | 获取 AgentManager 实例. | [L1253](../../../../../jiuwenswarm/server/agent_ws_server.py#L1253) |
| `async def handle_acp_tool_response_for_test(self, ws: Any, request: AgentRequest, send_lock: asyncio.Lock) -> None` | Public test helper that delegates to ACP tool-response handling. | [L1257](../../../../../jiuwenswarm/server/agent_ws_server.py#L1257) |
| `def is_working(self) -> bool` | 返回 Agent 是否正在工作. | [L1277](../../../../../jiuwenswarm/server/agent_ws_server.py#L1277) |
| `def _build_model_cache(self) -> None` | Build model cache from jiuwenswarm config.yaml (reuse interface_deep logic). | [L1287](../../../../../jiuwenswarm/server/agent_ws_server.py#L1287) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def _import_interface_deep_blocking() -> None` | 源码未提供函数级文档字符串。 | [L163](../../../../../jiuwenswarm/server/agent_ws_server.py#L163) |
| `async def _warm_interface_deep_module() -> None` | Import interface_deep in a worker thread so listen is not blocked. | [L168](../../../../../jiuwenswarm/server/agent_ws_server.py#L168) |
| `async def ensure_interface_deep_and_checkpointer() -> None` | Make interface_deep importable without a synchronous import on this task. | [L182](../../../../../jiuwenswarm/server/agent_ws_server.py#L182) |

## `jiuwenswarm/server/app_agentserver.py`

[打开源码](../../../../../jiuwenswarm/server/app_agentserver.py#L1)

**模块职责：** Standalone AgentServer entrypoint.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `_workspace_dir` | `未显式标注` | [L58](../../../../../jiuwenswarm/server/app_agentserver.py#L58) |
| `_config_file` | `未显式标注` | [L59](../../../../../jiuwenswarm/server/app_agentserver.py#L59) |
| `_new_workspace` | `未显式标注` | [L60](../../../../../jiuwenswarm/server/app_agentserver.py#L60) |
| `_old_workspace` | `未显式标注` | [L61](../../../../../jiuwenswarm/server/app_agentserver.py#L61) |
| `_loaded_logging_yaml` | `未显式标注` | [L73](../../../../../jiuwenswarm/server/app_agentserver.py#L73) |
| `_EXIT_REASON` | `未显式标注` | [L199](../../../../../jiuwenswarm/server/app_agentserver.py#L199) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def is_enterprise() -> bool` | 判断当前 AgentServer 是否运行在企业版。 | [L29](../../../../../jiuwenswarm/server/app_agentserver.py#L29) |
| `def _set_exit_reason(reason: str) -> None` | 源码未提供函数级文档字符串。 | [L202](../../../../../jiuwenswarm/server/app_agentserver.py#L202) |
| `def _atexit_log_exit_reason() -> None` | 源码未提供函数级文档字符串。 | [L207](../../../../../jiuwenswarm/server/app_agentserver.py#L207) |
| `async def _run(host: str, port: int) -> None` | 源码未提供函数级文档字符串。 | [L221](../../../../../jiuwenswarm/server/app_agentserver.py#L221) |
| `async def _run_with_telemetry(host: str, port: int, telemetry_lifecycle) -> None` | 源码未提供函数级文档字符串。 | [L234](../../../../../jiuwenswarm/server/app_agentserver.py#L234) |
| `def main() -> None` | 源码未提供函数级文档字符串。 | [L450](../../../../../jiuwenswarm/server/app_agentserver.py#L450) |

## `jiuwenswarm/server/context.py`

[打开源码](../../../../../jiuwenswarm/server/context.py#L1)

**模块职责：** ``RequestContext``：业务handler的入参

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `SERVICE_MEMBERS` | `dict[str, str]` | [L28](../../../../../jiuwenswarm/server/context.py#L28) |

### [`class AgentServerServices`](../../../../../jiuwenswarm/server/context.py#L58)

``ctx.services``，业务层看得见的服务端面。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `__slots__` | `未显式标注` | `('_server',)` | [L65](../../../../../jiuwenswarm/server/context.py#L65) |

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self, server: Any) -> None` | 源码未提供方法级文档字符串。 | [L67](../../../../../jiuwenswarm/server/context.py#L67) |
| `@property def raw_server(self) -> Any` | 逃生舱：仅供**传输层**自己使用，业务层不要碰。 | [L71](../../../../../jiuwenswarm/server/context.py#L71) |
| `def __getattr__(self, name: str) -> Any` | 源码未提供方法级文档字符串。 | [L75](../../../../../jiuwenswarm/server/context.py#L75) |
| `def __setattr__(self, name: str, value: Any) -> None` | 源码未提供方法级文档字符串。 | [L85](../../../../../jiuwenswarm/server/context.py#L85) |

### [`class RequestContext`](../../../../../jiuwenswarm/server/context.py#L93)

一次请求的全部上下文。

装饰器：`@dataclass(frozen=True)`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `request` | `AgentRequest` | `—` | [L110](../../../../../jiuwenswarm/server/context.py#L110) |
| `sink` | `ResponseSink` | `—` | [L111](../../../../../jiuwenswarm/server/context.py#L111) |
| `connection_id` | `str` | `—` | [L112](../../../../../jiuwenswarm/server/context.py#L112) |
| `services` | `Any` | `None` | [L113](../../../../../jiuwenswarm/server/context.py#L113) |

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `@property def params(self) -> dict[str, Any]` | ``request.params`` 的安全访问（非 dict 时返回空 dict）。 | [L116](../../../../../jiuwenswarm/server/context.py#L116) |
| `@property def request_id(self) -> str` | 源码未提供方法级文档字符串。 | [L121](../../../../../jiuwenswarm/server/context.py#L121) |
| `@property def channel_id(self) -> str` | 源码未提供方法级文档字符串。 | [L125](../../../../../jiuwenswarm/server/context.py#L125) |
| `@property def session_id(self) -> str \| None` | 源码未提供方法级文档字符串。 | [L129](../../../../../jiuwenswarm/server/context.py#L129) |

## `jiuwenswarm/server/dispatch.py`

[打开源码](../../../../../jiuwenswarm/server/dispatch.py#L1)

**模块职责：** 请求分发注册表

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `logger` | `未显式标注` | [L28](../../../../../jiuwenswarm/server/dispatch.py#L28) |
| `HANDLERS` | `dict[ReqMethod, HandlerSpec]` | [L67](../../../../../jiuwenswarm/server/dispatch.py#L67) |

### [`class HandlerSpec`](../../../../../jiuwenswarm/server/dispatch.py#L32)

一条分发规则：指向一个**传输无关的自由函数**。

装饰器：`@dataclass(frozen=True)`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `fn` | `Any` | `None` | [L42](../../../../../jiuwenswarm/server/dispatch.py#L42) |
| `args` | `tuple[Any, ...]` | `()` | [L43](../../../../../jiuwenswarm/server/dispatch.py#L43) |
| `kwargs` | `Mapping[str, Any]` | `field(default_factory=dict)` | [L44](../../../../../jiuwenswarm/server/dispatch.py#L44) |
| `stream_fn` | `Any` | `None` | [L45](../../../../../jiuwenswarm/server/dispatch.py#L45) |

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __post_init__(self) -> None` | 源码未提供方法级文档字符串。 | [L47](../../../../../jiuwenswarm/server/dispatch.py#L47) |
| `def resolve_fn(self, is_stream: bool) -> Any` | 解析实际调用的函数。 | [L55](../../../../../jiuwenswarm/server/dispatch.py#L55) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def _schedule(action: str) -> HandlerSpec` | ``schedule.*`` / ``issue.*`` 共用一个 handler，按 action 分派。 | [L62](../../../../../jiuwenswarm/server/dispatch.py#L62) |
| `def _register_permissions_methods() -> None` | ``permissions.*`` 是一组方法共用 ``_handle_permissions_config``。 | [L177](../../../../../jiuwenswarm/server/dispatch.py#L177) |
| `def supported_methods() -> frozenset[ReqMethod]` | 表驱动分发覆盖的方法集合。 | [L189](../../../../../jiuwenswarm/server/dispatch.py#L189) |
| `async def dispatch_with_context(ctx: Any, request: Any) -> bool` | 按表分发，使用**调用方已构造好的** ctx。 | [L194](../../../../../jiuwenswarm/server/dispatch.py#L194) |
| `async def dispatch_to_handler(server: Any, ws: Any, request: Any, send_lock: Any, *, context_factory: Any = None) -> bool` | 按表分发到传输无关的 handler。 | [L201](../../../../../jiuwenswarm/server/dispatch.py#L201) |
| `def _default_context(server: Any, ws: Any, send_lock: Any, request: Any) -> Any` | 为新式 handler 构造默认（WebSocket）上下文。 | [L230](../../../../../jiuwenswarm/server/dispatch.py#L230) |

## `jiuwenswarm/server/event_loop_monitor.py`

[打开源码](../../../../../jiuwenswarm/server/event_loop_monitor.py#L1)

**模块职责：** 事件循环停摆探针与主线程栈采样器

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `logger` | `未显式标注` | [L18](../../../../../jiuwenswarm/server/event_loop_monitor.py#L18) |
| `_ENABLED` | `未显式标注` | [L21](../../../../../jiuwenswarm/server/event_loop_monitor.py#L21) |
| `_HEARTBEAT_INTERVAL_S` | `未显式标注` | [L25](../../../../../jiuwenswarm/server/event_loop_monitor.py#L25) |
| `_STALL_THRESHOLD_S` | `未显式标注` | [L27](../../../../../jiuwenswarm/server/event_loop_monitor.py#L27) |
| `_MIN_REPORT_INTERVAL_S` | `未显式标注` | [L32](../../../../../jiuwenswarm/server/event_loop_monitor.py#L32) |
| `_SAMPLE_INTERVAL_S` | `未显式标注` | [L36](../../../../../jiuwenswarm/server/event_loop_monitor.py#L36) |
| `_RING_SIZE` | `未显式标注` | [L38](../../../../../jiuwenswarm/server/event_loop_monitor.py#L38) |
| `_SIG_TOP_FRAMES` | `未显式标注` | [L40](../../../../../jiuwenswarm/server/event_loop_monitor.py#L40) |
| `_BOOST_SAMPLES` | `未显式标注` | [L42](../../../../../jiuwenswarm/server/event_loop_monitor.py#L42) |
| `_BOOST_INTERVAL_S` | `未显式标注` | [L43](../../../../../jiuwenswarm/server/event_loop_monitor.py#L43) |
| `_REPLAY_SLOP_S` | `未显式标注` | [L45](../../../../../jiuwenswarm/server/event_loop_monitor.py#L45) |
| `_MEM_TREND_INTERVAL_S` | `未显式标注` | [L47](../../../../../jiuwenswarm/server/event_loop_monitor.py#L47) |
| `_MEM_WARN_PCT` | `未显式标注` | [L52](../../../../../jiuwenswarm/server/event_loop_monitor.py#L52) |
| `_installed` | `未显式标注` | [L57](../../../../../jiuwenswarm/server/event_loop_monitor.py#L57) |
| `_heartbeat_task` | `Optional[asyncio.Task]` | [L59](../../../../../jiuwenswarm/server/event_loop_monitor.py#L59) |
| `_mem_trend_task` | `Optional[asyncio.Task]` | [L61](../../../../../jiuwenswarm/server/event_loop_monitor.py#L61) |
| `_sampler_thread` | `Optional[threading.Thread]` | [L63](../../../../../jiuwenswarm/server/event_loop_monitor.py#L63) |
| `_stop_sampler` | `未显式标注` | [L65](../../../../../jiuwenswarm/server/event_loop_monitor.py#L65) |
| `_events` | `'queue.SimpleQueue[tuple[float, float]]'` | [L67](../../../../../jiuwenswarm/server/event_loop_monitor.py#L67) |
| `_ring` | `deque[tuple[float, str]]` | [L69](../../../../../jiuwenswarm/server/event_loop_monitor.py#L69) |

### [`class StackSampler`](../../../../../jiuwenswarm/server/event_loop_monitor.py#L203)

采样线程：周期性缓存主线程栈签名，停摆时回放并补采全栈。

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `@classmethod def start(cls) -> None` | 源码未提供方法级文档字符串。 | [L207](../../../../../jiuwenswarm/server/event_loop_monitor.py#L207) |
| `@classmethod def _main_thread_id(cls) -> Optional[int]` | 源码未提供方法级文档字符串。 | [L226](../../../../../jiuwenswarm/server/event_loop_monitor.py#L226) |
| `@classmethod def _sample(cls) -> tuple[float, Optional[str]]` | 源码未提供方法级文档字符串。 | [L236](../../../../../jiuwenswarm/server/event_loop_monitor.py#L236) |
| `@classmethod def _emit(cls, gap: float, end_ts: float) -> None` | 打停摆窗口证据 + 补采全栈。 | [L245](../../../../../jiuwenswarm/server/event_loop_monitor.py#L245) |
| `@classmethod def _run(cls) -> None` | 源码未提供方法级文档字符串。 | [L277](../../../../../jiuwenswarm/server/event_loop_monitor.py#L277) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def _frame_signature(frame: Any) -> str` | 把一条调用栈压成 ``file:line:func <- ...`` 的紧凑签名（存缓冲用）。 | [L72](../../../../../jiuwenswarm/server/event_loop_monitor.py#L72) |
| `def _all_thread_frames() -> dict[int, Any]` | 返回所有线程的当前栈帧字典（键为线程标识）。 | [L87](../../../../../jiuwenswarm/server/event_loop_monitor.py#L87) |
| `def _format_full_stack(frame: Any) -> str` | 格式化整条调用栈（补采全栈用）。 | [L93](../../../../../jiuwenswarm/server/event_loop_monitor.py#L93) |
| `def _mem_stats() -> tuple[float, float]` | 返回 (进程RSS MB, 系统总内存 MB)。 | [L98](../../../../../jiuwenswarm/server/event_loop_monitor.py#L98) |
| `async def memory_trend_loop() -> None` | 内存趋势日志：每 ``_MEM_TREND_INTERVAL_S`` 打一条 RSS 走势。 | [L133](../../../../../jiuwenswarm/server/event_loop_monitor.py#L133) |
| `async def loop_heartbeat_loop() -> None` | 事件循环心跳：差分测停摆，阻塞结束后第一拍即告警并通知采样器。 | [L177](../../../../../jiuwenswarm/server/event_loop_monitor.py#L177) |
| `async def ensure_event_loop_monitor() -> None` | 幂等挂载事件循环停摆探针（采样线程 + 心跳协程 + 内存趋势协程）。 | [L296](../../../../../jiuwenswarm/server/event_loop_monitor.py#L296) |
| `def stop_event_loop_monitor() -> None` | 停止事件循环监控（供测试与事件循环重启场景使用）。 | [L331](../../../../../jiuwenswarm/server/event_loop_monitor.py#L331) |

## `jiuwenswarm/server/gateway_push/__init__.py`

[打开源码](../../../../../jiuwenswarm/server/gateway_push/__init__.py#L1)

**模块职责：** 包初始化与选择性重导出。

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `__all__` | `未显式标注` | [L9](../../../../../jiuwenswarm/server/gateway_push/__init__.py#L9) |

## `jiuwenswarm/server/gateway_push/transport.py`

[打开源码](../../../../../jiuwenswarm/server/gateway_push/transport.py#L1)

**模块职责：** AgentServer → Gateway 下行推送抽象与 WebSocket 默认实现。

### [`class GatewayPushTransport(Protocol)`](../../../../../jiuwenswarm/server/gateway_push/transport.py#L11)

源码未提供类级文档字符串。

装饰器：`@runtime_checkable`。

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `async def send_push(self, msg: dict[str, Any]) -> None` | 向 Gateway 发送一条 server_push 语义的消息（与 AgentWebSocketServer.send_push 入参一致）。 | [L12](../../../../../jiuwenswarm/server/gateway_push/transport.py#L12) |

### [`class WebSocketGatewayPushTransport`](../../../../../jiuwenswarm/server/gateway_push/transport.py#L17)

通过进程内 AgentWebSocketServer 单例推送（分离部署 + WebSocket 默认路径）。

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `async def send_push(self, msg: dict[str, Any]) -> None` | 源码未提供方法级文档字符串。 | [L20](../../../../../jiuwenswarm/server/gateway_push/transport.py#L20) |

## `jiuwenswarm/server/gateway_push/wire.py`

[打开源码](../../../../../jiuwenswarm/server/gateway_push/wire.py#L1)

**模块职责：** E2A server_push 线编码：WebSocket 与 HTTP SSE 下行共用同一 wire 形状。

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `_CONVERTER` | `未显式标注` | [L18](../../../../../jiuwenswarm/server/gateway_push/wire.py#L18) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def build_server_push_wire(msg: dict[str, Any]) -> dict[str, Any]` | 将 send_push 入参编码为与 WebSocket 单帧一致的 E2A 响应线 dict。 | [L21](../../../../../jiuwenswarm/server/gateway_push/wire.py#L21) |

## `jiuwenswarm/server/handlers/__init__.py`

[打开源码](../../../../../jiuwenswarm/server/handlers/__init__.py#L1)

**模块职责：** 传输无关的业务 handler，按域分模块。

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `__all__` | `未显式标注` | [L31](../../../../../jiuwenswarm/server/handlers/__init__.py#L31) |

## `jiuwenswarm/server/handlers/_default.py`

[打开源码](../../../../../jiuwenswarm/server/handlers/_default.py#L1)

**模块职责：** 默认路径，分发表未命中时走的通用agent调用。

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `logger` | `未显式标注` | [L44](../../../../../jiuwenswarm/server/handlers/_default.py#L44) |
| `_STREAM_HEARTBEAT_INTERVAL_SECONDS` | `未显式标注` | [L48](../../../../../jiuwenswarm/server/handlers/_default.py#L48) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def _is_stateless_method_request(request: AgentRequest) -> bool` | skills / skilldev / plugins / symphony 为无状态 RPC，无需 mode 解析与 adapter. | [L51](../../../../../jiuwenswarm/server/handlers/_default.py#L51) |
| `def _is_readonly_goal_get_request(request: AgentRequest) -> bool` | ``command.goal`` + ``action=get``：只读查询，不得兜底新建 session metadata. | [L64](../../../../../jiuwenswarm/server/handlers/_default.py#L64) |
| `def _is_explicit_plan_entry_request(request: AgentRequest) -> bool` | 源码未提供函数级文档字符串。 | [L79](../../../../../jiuwenswarm/server/handlers/_default.py#L79) |
| `def _should_sync_code_mode_state(request: AgentRequest) -> bool` | Only agent chat turns may change plan/normal mode. | [L86](../../../../../jiuwenswarm/server/handlers/_default.py#L86) |
| `def _session_mode_sync_lock(session_id: str) -> asyncio.Lock` | 源码未提供函数级文档字符串。 | [L98](../../../../../jiuwenswarm/server/handlers/_default.py#L98) |
| `async def _get_stateless_agent(ctx, channel_id: str) -> Any` | 为无状态请求取 agent，**不触发任何 mode 的 adapter 重建**. | [L108](../../../../../jiuwenswarm/server/handlers/_default.py#L108) |
| `async def _push_plan_mode_exited(ctx, request: AgentRequest) -> None` | Notify the client that plan mode ended after user approval. | [L137](../../../../../jiuwenswarm/server/handlers/_default.py#L137) |
| `async def _check_post_process_plan_exit(ctx, request: AgentRequest, agent: Any) -> None` | Detect plan→normal transition that happened inside tool execution. | [L154](../../../../../jiuwenswarm/server/handlers/_default.py#L154) |
| `async def _ensure_code_mode_state(ctx, request: AgentRequest, mode: str, sub_mode: str, agent: Any) -> bool` | code 模式：确保 agent 的 plan_mode 状态正确，必要时执行 switch_mode 并持久化. | [L201](../../../../../jiuwenswarm/server/handlers/_default.py#L201) |
| `async def _prepare_code_mode_chat_turn(ctx, request: AgentRequest, channel_id: str, *, sync_metadata: bool = True, agent_manager: Any \| None = None) -> tuple[str, str \| None, Any]` | Mode resolution and correct agent instance selection. | [L311](../../../../../jiuwenswarm/server/handlers/_default.py#L311) |
| `async def _get_tenant_agent_manager(ctx, request: AgentRequest) -> Any` | Resolve the tenant-pool AgentManager for an officeclaw/E2A request. | [L451](../../../../../jiuwenswarm/server/handlers/_default.py#L451) |
| `async def _prepare_tenant_code_mode_chat_turn(ctx, request: AgentRequest, channel_id: str) -> tuple[str, str \| None, Any] \| None` | Run the same code.plan sync as the default WS path on tenant-pool chats. | [L461](../../../../../jiuwenswarm/server/handlers/_default.py#L461) |
| `async def _handle_unary(ctx: RequestContext, request: AgentRequest) -> None` | 源码未提供函数级文档字符串。 | [L492](../../../../../jiuwenswarm/server/handlers/_default.py#L492) |
| `async def _handle_unary_impl(ctx: RequestContext, request: AgentRequest) -> None` | 非流式处理：调用 process_message，返回一条 E2AResponse 线 JSON。 | [L520](../../../../../jiuwenswarm/server/handlers/_default.py#L520) |
| `async def _handle_stream(ctx: RequestContext, request: AgentRequest) -> None` | 源码未提供函数级文档字符串。 | [L616](../../../../../jiuwenswarm/server/handlers/_default.py#L616) |
| `async def _handle_stream_impl(ctx: RequestContext, request: AgentRequest) -> None` | 流式处理：调用 process_message_stream，逐条发送 E2AResponse 线 JSON。 | [L644](../../../../../jiuwenswarm/server/handlers/_default.py#L644) |

## `jiuwenswarm/server/handlers/_shared.py`

[打开源码](../../../../../jiuwenswarm/server/handlers/_shared.py#L1)

**模块职责：** 跨域共享依赖，handler各域模块与``agent_ws_server``的公共下层。

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `logger` | `未显式标注` | [L28](../../../../../jiuwenswarm/server/handlers/_shared.py#L28) |
| `_background_session_kvc_tasks` | `set[asyncio.Task]` | [L33](../../../../../jiuwenswarm/server/handlers/_shared.py#L33) |
| `_plan_exited_sessions` | `set[str]` | [L38](../../../../../jiuwenswarm/server/handlers/_shared.py#L38) |
| `_session_mode_sync_locks` | `'WeakValueDictionary[str, asyncio.Lock]'` | [L42](../../../../../jiuwenswarm/server/handlers/_shared.py#L42) |
| `_CODE_MODE_SYNC_METHODS` | `未显式标注` | [L45](../../../../../jiuwenswarm/server/handlers/_shared.py#L45) |
| `_session_team_binding_locks` | `WeakValueDictionary[str, asyncio.Lock]` | [L473](../../../../../jiuwenswarm/server/handlers/_shared.py#L473) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def _log_background_session_kvc_failure(task: asyncio.Task) -> None` | Log optional post-response KVC failures without changing session state. | [L52](../../../../../jiuwenswarm/server/handlers/_shared.py#L52) |
| `def send_error_wire(request: AgentRequest, error: str, code: str \| None = None) -> dict[str, Any]` | Build an error AgentResponse wire payload. | [L66](../../../../../jiuwenswarm/server/handlers/_shared.py#L66) |
| `def resolve_request_project_dir(request: AgentRequest) -> str \| None` | Resolve the stable project identity for agent construction. | [L91](../../../../../jiuwenswarm/server/handlers/_shared.py#L91) |
| `def resolve_agent_request_mode(raw_mode: Any, *, work_mode: Any = None) -> tuple[str, str \| None, str]` | Resolve request params.mode into manager mode, sub_mode, and canonical value. | [L122](../../../../../jiuwenswarm/server/handlers/_shared.py#L122) |
| `def _apply_resolved_mode_to_request(request: AgentRequest, *, work_mode: Any = None) -> tuple[str, str \| None]` | 源码未提供函数级文档字符串。 | [L178](../../../../../jiuwenswarm/server/handlers/_shared.py#L178) |
| `def _resolve_model(ctx, model_name: Optional[str] = None) -> Optional[Any]` | Resolve model from jiuwenswarm config. | [L191](../../../../../jiuwenswarm/server/handlers/_shared.py#L191) |
| `def _is_team_metadata_mode(metadata: dict[str, Any]) -> bool` | 源码未提供函数级文档字符串。 | [L209](../../../../../jiuwenswarm/server/handlers/_shared.py#L209) |
| `def _sessions_dir_for_request(request: AgentRequest) -> Path` | Resolve tenant ``workspace_{key}/agent/sessions`` for an AgentRequest. | [L214](../../../../../jiuwenswarm/server/handlers/_shared.py#L214) |
| `def _agent_workspace_dir_for_request(request: AgentRequest) -> Path` | Resolve tenant ``workspace_{key}/agent/workspace`` for a request. | [L220](../../../../../jiuwenswarm/server/handlers/_shared.py#L220) |
| `def _effective_config_for_request(request: AgentRequest) -> Any` | Return the resolved OfficeClaw tenant snapshot; native gateway keeps disk config. | [L226](../../../../../jiuwenswarm/server/handlers/_shared.py#L226) |
| `@asynccontextmanager async def bootstrap_preconditions(request: AgentRequest)` | 连接引导类方法的前置条件。 | [L270](../../../../../jiuwenswarm/server/handlers/_shared.py#L270) |
| `def _sync_chat_request_metadata(request: AgentRequest, project_dir: str \| None, mode: str, explicit_mode_provided: bool = False) -> str \| None` | 将本次 chat 请求的参数同步到会话元数据，返回生效的 project_dir。 | [L314](../../../../../jiuwenswarm/server/handlers/_shared.py#L314) |
| `def _inject_plan_mode_activation_reminder(request: AgentRequest) -> None` | 在用户消息中注入 <system-reminder> 告知 LLM 当前处于 plan 模式. | [L403](../../../../../jiuwenswarm/server/handlers/_shared.py#L403) |
| `def _request_query_text(request: AgentRequest) -> str` | Return text chat query only; structured events are handled downstream. | [L447](../../../../../jiuwenswarm/server/handlers/_shared.py#L447) |
| `def _uses_tenant_pool(request: AgentRequest) -> bool` | 是否走多租户池（officeclaw 渠道，或带非默认 agent_id/service_id）。 | [L457](../../../../../jiuwenswarm/server/handlers/_shared.py#L457) |
| `def _session_team_binding_lock(session_id: str) -> asyncio.Lock` | 源码未提供函数级文档字符串。 | [L476](../../../../../jiuwenswarm/server/handlers/_shared.py#L476) |

## `jiuwenswarm/server/handlers/agents.py`

[打开源码](../../../../../jiuwenswarm/server/handlers/agents.py#L1)

**模块职责：** 智能体域 handler

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `logger` | `未显式标注` | [L19](../../../../../jiuwenswarm/server/handlers/agents.py#L19) |
| `_AGENT_CREATION_SYSTEM_PROMPT` | `未显式标注` | [L23](../../../../../jiuwenswarm/server/handlers/agents.py#L23) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `async def _generate_agent_with_llm(ctx, name: str, description: str) -> tuple[str, str] \| None` | 调用 LLM 生成 agent 的 whenToUse 和 systemPrompt。 | [L56](../../../../../jiuwenswarm/server/handlers/agents.py#L56) |
| `async def handle_agents_list(ctx: RequestContext) -> None` | 源码未提供函数级文档字符串。 | [L111](../../../../../jiuwenswarm/server/handlers/agents.py#L111) |
| `async def handle_agents_get(ctx: RequestContext) -> None` | 源码未提供函数级文档字符串。 | [L139](../../../../../jiuwenswarm/server/handlers/agents.py#L139) |
| `async def handle_agents_create(ctx: RequestContext) -> None` | 源码未提供函数级文档字符串。 | [L177](../../../../../jiuwenswarm/server/handlers/agents.py#L177) |
| `async def handle_agents_update(ctx: RequestContext) -> None` | 源码未提供函数级文档字符串。 | [L237](../../../../../jiuwenswarm/server/handlers/agents.py#L237) |
| `async def handle_agents_delete(ctx: RequestContext) -> None` | 源码未提供函数级文档字符串。 | [L296](../../../../../jiuwenswarm/server/handlers/agents.py#L296) |
| `async def handle_agents_set_enabled(ctx: RequestContext, enabled: bool) -> None` | 源码未提供函数级文档字符串。 | [L335](../../../../../jiuwenswarm/server/handlers/agents.py#L335) |
| `async def handle_agents_tools_list(ctx: RequestContext) -> None` | 源码未提供函数级文档字符串。 | [L387](../../../../../jiuwenswarm/server/handlers/agents.py#L387) |

## `jiuwenswarm/server/handlers/bootstrap.py`

[打开源码](../../../../../jiuwenswarm/server/handlers/bootstrap.py#L1)

**模块职责：** 连接引导/会话域 handler

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `logger` | `未显式标注` | [L24](../../../../../jiuwenswarm/server/handlers/bootstrap.py#L24) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `async def handle_initialize(ctx: RequestContext) -> None` | 处理 initialize 方法（非流式）. | [L27](../../../../../jiuwenswarm/server/handlers/bootstrap.py#L27) |
| `async def handle_session_create(ctx: RequestContext) -> None` | 处理 session.create 方法. | [L86](../../../../../jiuwenswarm/server/handlers/bootstrap.py#L86) |
| `async def handle_session_fork(ctx: RequestContext) -> None` | Handle session.fork: filesystem copy + in-memory context copy. | [L373](../../../../../jiuwenswarm/server/handlers/bootstrap.py#L373) |
| `async def handle_acp_tool_response(ctx: RequestContext) -> None` | 源码未提供函数级文档字符串。 | [L483](../../../../../jiuwenswarm/server/handlers/bootstrap.py#L483) |

## `jiuwenswarm/server/handlers/chat.py`

[打开源码](../../../../../jiuwenswarm/server/handlers/chat.py#L1)

**模块职责：** 聊天域 handler

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `logger` | `未显式标注` | [L35](../../../../../jiuwenswarm/server/handlers/chat.py#L35) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def _is_client_disconnect_cancel_request(request: AgentRequest) -> bool` | 源码未提供函数级文档字符串。 | [L38](../../../../../jiuwenswarm/server/handlers/chat.py#L38) |
| `async def _cleanup_client_disconnect_session_runtime(ctx, request: AgentRequest) -> bool` | 源码未提供函数级文档字符串。 | [L46](../../../../../jiuwenswarm/server/handlers/chat.py#L46) |
| `def _build_team_interrupt_response(request: AgentRequest, *, intent: str, success: bool, message: str) -> AgentResponse` | Build a chat.interrupt_result response for the team-mode short-circuit. | [L82](../../../../../jiuwenswarm/server/handlers/chat.py#L82) |
| `async def _handle_cancel(ctx: RequestContext, *, allow_create: bool = False, send_response: bool = True) -> AgentResponse` | 处理 CHAT_CANCEL 中断请求：复用已有 agent 实例，避免创建新实例。 | [L109](../../../../../jiuwenswarm/server/handlers/chat.py#L109) |
| `async def handle_chat_cancel_dispatch(ctx: RequestContext) -> None` | 处理 chat.interrupt：按 intent 决定是否取消流式任务。 | [L263](../../../../../jiuwenswarm/server/handlers/chat.py#L263) |
| `async def _ensure_auto_team_binding_for_chat(ctx, request: AgentRequest) -> Any \| None` | Create and bind a team before the first team chat without consuming its query. | [L332](../../../../../jiuwenswarm/server/handlers/chat.py#L332) |

## `jiuwenswarm/server/handlers/commands.py`

[打开源码](../../../../../jiuwenswarm/server/handlers/commands.py#L1)

**模块职责：** 命令域 handler

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `logger` | `未显式标注` | [L53](../../../../../jiuwenswarm/server/handlers/commands.py#L53) |
| `_SIMPLIFY_PROMPT_TEMPLATE` | `未显式标注` | [L62](../../../../../jiuwenswarm/server/handlers/commands.py#L62) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def _build_simplify_prompt(target: str = '') -> str` | Build the prompt for the /simplify command. | [L122](../../../../../jiuwenswarm/server/handlers/commands.py#L122) |
| `def _extract_compact_summary_processor(summary: str) -> str` | 源码未提供函数级文档字符串。 | [L135](../../../../../jiuwenswarm/server/handlers/commands.py#L135) |
| `def _is_env_api_base_placeholder(env_updates: dict) -> bool` | 检查 env_updates 中的 API_BASE 是否指向 example.* 等占位域名。 | [L143](../../../../../jiuwenswarm/server/handlers/commands.py#L143) |
| `async def handle_command_workflows(ctx: RequestContext) -> None` | Handle command.workflows RPC — list summaries or get one workflow detail. | [L148](../../../../../jiuwenswarm/server/handlers/commands.py#L148) |
| `async def handle_command_add_dir(ctx: RequestContext) -> None` | 源码未提供函数级文档字符串。 | [L344](../../../../../jiuwenswarm/server/handlers/commands.py#L344) |
| `async def handle_command_chrome(ctx: RequestContext) -> None` | 源码未提供函数级文档字符串。 | [L382](../../../../../jiuwenswarm/server/handlers/commands.py#L382) |
| `async def handle_command_compact(ctx: RequestContext) -> None` | 源码未提供函数级文档字符串。 | [L403](../../../../../jiuwenswarm/server/handlers/commands.py#L403) |
| `async def handle_command_compact_partial(ctx: RequestContext) -> None` | 源码未提供函数级文档字符串。 | [L511](../../../../../jiuwenswarm/server/handlers/commands.py#L511) |
| `async def handle_command_context(ctx: RequestContext) -> None` | 源码未提供函数级文档字符串。 | [L561](../../../../../jiuwenswarm/server/handlers/commands.py#L561) |
| `async def handle_command_recap(ctx: RequestContext) -> None` | 处理 /recap 命令：生成会话快速回顾（read-only，不修改历史） | [L600](../../../../../jiuwenswarm/server/handlers/commands.py#L600) |
| `async def handle_command_btw(ctx: RequestContext) -> None` | 处理 /btw 命令：独立、无工具、单轮 LLM 侧问题查询。 | [L643](../../../../../jiuwenswarm/server/handlers/commands.py#L643) |
| `async def handle_command_diff(ctx: RequestContext) -> None` | 源码未提供函数级文档字符串。 | [L720](../../../../../jiuwenswarm/server/handlers/commands.py#L720) |
| `async def handle_command_simplify(ctx: RequestContext) -> None` | 处理 /simplify 命令：组装代码精简审查 prompt 并返回（由前端作为消息发送给 Agent）。 | [L779](../../../../../jiuwenswarm/server/handlers/commands.py#L779) |
| `async def handle_command_model(ctx: RequestContext) -> None` | 源码未提供函数级文档字符串。 | [L812](../../../../../jiuwenswarm/server/handlers/commands.py#L812) |
| `async def handle_command_resume(ctx: RequestContext) -> None` | 源码未提供函数级文档字符串。 | [L913](../../../../../jiuwenswarm/server/handlers/commands.py#L913) |
| `async def handle_command_session(ctx: RequestContext) -> None` | 源码未提供函数级文档字符串。 | [L942](../../../../../jiuwenswarm/server/handlers/commands.py#L942) |
| `async def handle_command_status(ctx: RequestContext) -> None` | 源码未提供函数级文档字符串。 | [L968](../../../../../jiuwenswarm/server/handlers/commands.py#L968) |

## `jiuwenswarm/server/handlers/extensions.py`

[打开源码](../../../../../jiuwenswarm/server/handlers/extensions.py#L1)

**模块职责：** 扩展域 handler

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `logger` | `未显式标注` | [L24](../../../../../jiuwenswarm/server/handlers/extensions.py#L24) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def _harness_error_code(exc: BaseException) -> str` | Map a harness package exception to a wire ``code`` for the frontend. | [L27](../../../../../jiuwenswarm/server/handlers/extensions.py#L27) |
| `async def handle_extensions_list(ctx: RequestContext) -> None` | 获取所有 Rail 扩展列表. | [L45](../../../../../jiuwenswarm/server/handlers/extensions.py#L45) |
| `async def handle_extensions_import(ctx: RequestContext) -> None` | 导入新的 Rail 扩展（文件夹结构）. | [L71](../../../../../jiuwenswarm/server/handlers/extensions.py#L71) |
| `async def handle_extensions_delete(ctx: RequestContext) -> None` | 删除 Rail 扩展. | [L107](../../../../../jiuwenswarm/server/handlers/extensions.py#L107) |
| `async def handle_extensions_toggle(ctx: RequestContext) -> None` | 切换 Rail 扩展的启用状态，并触发热更新. | [L139](../../../../../jiuwenswarm/server/handlers/extensions.py#L139) |
| `async def handle_hooks_list(ctx: RequestContext) -> None` | 获取当前 hooks 配置（供 TUI /hooks 命令浏览）. | [L186](../../../../../jiuwenswarm/server/handlers/extensions.py#L186) |
| `async def handle_harness_packages_get(ctx: RequestContext) -> None` | Handle harness.packages.get request - retrieve packages info. | [L217](../../../../../jiuwenswarm/server/handlers/extensions.py#L217) |
| `async def handle_harness_packages_scan(ctx: RequestContext) -> None` | Handle harness.packages.scan request - scan runtime extensions. | [L242](../../../../../jiuwenswarm/server/handlers/extensions.py#L242) |
| `async def handle_harness_packages_activate(ctx: RequestContext) -> None` | Handle harness.packages.activate request - activate a harness package. | [L268](../../../../../jiuwenswarm/server/handlers/extensions.py#L268) |
| `async def handle_harness_packages_deactivate(ctx: RequestContext) -> None` | Handle harness.packages.deactivate request - deactivate a harness package. | [L338](../../../../../jiuwenswarm/server/handlers/extensions.py#L338) |
| `async def handle_harness_packages_delete(ctx: RequestContext) -> None` | Handle harness.packages.delete request - delete a harness package. | [L403](../../../../../jiuwenswarm/server/handlers/extensions.py#L403) |

## `jiuwenswarm/server/handlers/mcp.py`

[打开源码](../../../../../jiuwenswarm/server/handlers/mcp.py#L1)

**模块职责：** MCP 域 handler

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `logger` | `未显式标注` | [L25](../../../../../jiuwenswarm/server/handlers/mcp.py#L25) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def _normalize_mcp_payload(params: dict[str, Any], current: dict[str, Any] \| None = None) -> dict[str, Any]` | 源码未提供函数级文档字符串。 | [L28](../../../../../jiuwenswarm/server/handlers/mcp.py#L28) |
| `def _mask_sensitive_fields(payload: Any) -> Any` | 源码未提供函数级文档字符串。 | [L72](../../../../../jiuwenswarm/server/handlers/mcp.py#L72) |
| `async def _pre_check_mcp_server(server_payload: dict[str, Any]) -> tuple[bool, str]` | Try a temporary connection to verify the MCP server is reachable. | [L92](../../../../../jiuwenswarm/server/handlers/mcp.py#L92) |
| `async def _fetch_mcp_tools_from_config(entry: dict[str, Any]) -> list[dict[str, Any]]` | Create a temporary MCP connection from config entry and list tools. | [L157](../../../../../jiuwenswarm/server/handlers/mcp.py#L157) |
| `def _normalize_mcp_add_payload(ctx, params: dict[str, Any]) -> dict[str, Any]` | 源码未提供函数级文档字符串。 | [L220](../../../../../jiuwenswarm/server/handlers/mcp.py#L220) |
| `def _normalize_mcp_update_payload(ctx, params: dict[str, Any]) -> dict[str, Any]` | 源码未提供函数级文档字符串。 | [L224](../../../../../jiuwenswarm/server/handlers/mcp.py#L224) |
| `async def handle_command_mcp(ctx: RequestContext) -> None` | 源码未提供函数级文档字符串。 | [L234](../../../../../jiuwenswarm/server/handlers/mcp.py#L234) |

## `jiuwenswarm/server/handlers/ops.py`

[打开源码](../../../../../jiuwenswarm/server/handlers/ops.py#L1)

**模块职责：** 运维域 handler

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `logger` | `未显式标注` | [L17](../../../../../jiuwenswarm/server/handlers/ops.py#L17) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `async def _reset_active_browser_runtimes_if_available(browser_move: Any) -> int` | Reset active browser runtimes when supported by the installed SDK. | [L20](../../../../../jiuwenswarm/server/handlers/ops.py#L20) |
| `async def handle_proactive_tick(ctx: RequestContext) -> None` | Handle proactive.tick request from CronScheduler. | [L37](../../../../../jiuwenswarm/server/handlers/ops.py#L37) |
| `async def handle_browser_runtime_restart(ctx: RequestContext) -> None` | 源码未提供函数级文档字符串。 | [L83](../../../../../jiuwenswarm/server/handlers/ops.py#L83) |
| `async def handle_config_cache_clear(ctx: RequestContext) -> None` | 源码未提供函数级文档字符串。 | [L114](../../../../../jiuwenswarm/server/handlers/ops.py#L114) |
| `async def handle_agent_reload_config(ctx: RequestContext) -> None` | 源码未提供函数级文档字符串。 | [L143](../../../../../jiuwenswarm/server/handlers/ops.py#L143) |
| `async def handle_sync_agents_configs(ctx: RequestContext) -> None` | 源码未提供函数级文档字符串。 | [L230](../../../../../jiuwenswarm/server/handlers/ops.py#L230) |
| `async def handle_agent_prewarm_sync(ctx: RequestContext) -> None` | Reconcile background prewarming for the Gateway's live channels. | [L280](../../../../../jiuwenswarm/server/handlers/ops.py#L280) |

## `jiuwenswarm/server/handlers/permissions.py`

[打开源码](../../../../../jiuwenswarm/server/handlers/permissions.py#L1)

**模块职责：** 权限域 handler

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `logger` | `未显式标注` | [L20](../../../../../jiuwenswarm/server/handlers/permissions.py#L20) |
| `_background_permission_reload_tasks` | `set[asyncio.Task]` | [L24](../../../../../jiuwenswarm/server/handlers/permissions.py#L24) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def _log_permission_reload_failure(task: asyncio.Task) -> None` | 后台权限重载任务完成回调: 仅在异常时记 debug(与原同步 try/except 语义一致)。 | [L27](../../../../../jiuwenswarm/server/handlers/permissions.py#L27) |
| `async def handle_permissions_config(ctx: RequestContext) -> None` | 处理 permissions.* E2A 请求（与 Web ``register_method`` 同名 method）。 | [L37](../../../../../jiuwenswarm/server/handlers/permissions.py#L37) |

## `jiuwenswarm/server/handlers/sandbox.py`

[打开源码](../../../../../jiuwenswarm/server/handlers/sandbox.py#L1)

**模块职责：** 沙箱域 handler

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `logger` | `未显式标注` | [L36](../../../../../jiuwenswarm/server/handlers/sandbox.py#L36) |
| `_SANDBOX_FILES_PARAMS` | `未显式标注` | [L39](../../../../../jiuwenswarm/server/handlers/sandbox.py#L39) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def _resolve_active_project_dir(ctx, channel_id: str, params: dict[str, Any] \| None = None) -> str \| None` | Resolve the user project dir for the current ``/sandbox`` view. | [L52](../../../../../jiuwenswarm/server/handlers/sandbox.py#L52) |
| `def _resolve_active_is_code_agent(ctx, channel_id: str) -> bool` | Look up whether ``channel_id``'s adapter is the code-agent flavor. | [L101](../../../../../jiuwenswarm/server/handlers/sandbox.py#L101) |
| `def allocate_internal_jiuwenbox_port(services, host: str, preferred_port: int) -> int` | internal 模式下确定 jiuwenbox 实际监听端口。 | [L130](../../../../../jiuwenswarm/server/handlers/sandbox.py#L130) |
| `def _dry_run_files_policy(ctx, channel_id: str, params: dict[str, Any], files: dict[str, Any]) -> None` | 源码未提供函数级文档字符串。 | [L157](../../../../../jiuwenswarm/server/handlers/sandbox.py#L157) |
| `def _read_landlock_compatibility(policy_path: Path \| None) -> str` | 源码未提供函数级文档字符串。 | [L176](../../../../../jiuwenswarm/server/handlers/sandbox.py#L176) |
| `def _effective_files_from_adapter(adapter: Any) -> dict[str, list[dict[str, str]]] \| None` | Read effective sandbox file mounts from the adapter's active sysop card. | [L193](../../../../../jiuwenswarm/server/handlers/sandbox.py#L193) |
| `async def _apply_sandbox_runtime_patch(ctx, channel_id: str, runtime: dict[str, Any], *, files_changed: bool) -> None` | 源码未提供函数级文档字符串。 | [L209](../../../../../jiuwenswarm/server/handlers/sandbox.py#L209) |
| `def parse_sandbox_host_port(url: str) -> tuple[str, int]` | 从 sandbox url 解析 host:port; 默认 127.0.0.1:8321. | [L224](../../../../../jiuwenswarm/server/handlers/sandbox.py#L224) |
| `def _require_sandbox_supported() -> None` | Reject ``/sandbox`` commands on non-Linux hosts. | [L236](../../../../../jiuwenswarm/server/handlers/sandbox.py#L236) |
| `async def _handle_sandbox_enable(ctx, channel_id: str) -> dict[str, Any]` | 源码未提供函数级文档字符串。 | [L256](../../../../../jiuwenswarm/server/handlers/sandbox.py#L256) |
| `async def _handle_sandbox_disable(ctx, channel_id: str) -> dict[str, Any]` | 源码未提供函数级文档字符串。 | [L363](../../../../../jiuwenswarm/server/handlers/sandbox.py#L363) |
| `async def _handle_sandbox_exclude_add(ctx, channel_id: str, params: dict[str, Any]) -> dict[str, Any]` | 源码未提供函数级文档字符串。 | [L394](../../../../../jiuwenswarm/server/handlers/sandbox.py#L394) |
| `async def _handle_sandbox_exclude_remove(ctx, channel_id: str, params: dict[str, Any]) -> dict[str, Any]` | 源码未提供函数级文档字符串。 | [L413](../../../../../jiuwenswarm/server/handlers/sandbox.py#L413) |
| `async def _handle_sandbox_files_set(ctx, channel_id: str, params: dict[str, Any], *, bucket: str) -> dict[str, Any]` | 源码未提供函数级文档字符串。 | [L432](../../../../../jiuwenswarm/server/handlers/sandbox.py#L432) |
| `async def _handle_sandbox_files_remove(ctx, channel_id: str, params: dict[str, Any]) -> dict[str, Any]` | 源码未提供函数级文档字符串。 | [L510](../../../../../jiuwenswarm/server/handlers/sandbox.py#L510) |
| `def _attach_effective_sandbox_files(ctx, payload: dict[str, Any], channel_id: str, params: dict[str, Any] \| None = None) -> None` | Inject ``effective_files`` into the ``/sandbox`` response payload. | [L575](../../../../../jiuwenswarm/server/handlers/sandbox.py#L575) |
| `async def _attach_landlock_status(ctx, payload: dict[str, Any]) -> None` | Attach jiuwenbox Landlock capability summary to sandbox responses. | [L640](../../../../../jiuwenswarm/server/handlers/sandbox.py#L640) |
| `def _canonicalize_sandbox_files_path(path: str) -> str` | 把 TUI 传来的 ``path`` 展开成 absolute resolved 形式 (绝对、去 ``..``、 展开 ``~``、按需展开 symlink) 后作为 ``sandbox.files.{allow,deny}`` 的 canonical key. | [L664](../../../../../jiuwenswarm/server/handlers/sandbox.py#L664) |
| `def _file_entry_matches_path(entry: Any, path: str) -> bool` | 判断 ``sandbox.files.{allow,deny}`` 中的一项是否指向给定 ``path``. | [L697](../../../../../jiuwenswarm/server/handlers/sandbox.py#L697) |
| `def _reject_extra_sandbox_files_params(params: dict[str, Any]) -> None` | 源码未提供函数级文档字符串。 | [L727](../../../../../jiuwenswarm/server/handlers/sandbox.py#L727) |
| `async def handle_command_sandbox(ctx: RequestContext) -> None` | 处理 ``/sandbox`` 命令. | [L736](../../../../../jiuwenswarm/server/handlers/sandbox.py#L736) |

## `jiuwenswarm/server/handlers/schedule.py`

[打开源码](../../../../../jiuwenswarm/server/handlers/schedule.py#L1)

**模块职责：** 调度/议题域 handler

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `logger` | `未显式标注` | [L21](../../../../../jiuwenswarm/server/handlers/schedule.py#L21) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def _set_scheduler_agent(ctx, agent: Any) -> None` | Pin the facade whose DeepAgent is retained by the scheduler. | [L24](../../../../../jiuwenswarm/server/handlers/schedule.py#L24) |
| `async def handle_schedule_request(ctx: RequestContext, action: str) -> None` | Handle schedule.* requests - schedule task management. | [L39](../../../../../jiuwenswarm/server/handlers/schedule.py#L39) |

## `jiuwenswarm/server/handlers/session.py`

[打开源码](../../../../../jiuwenswarm/server/handlers/session.py#L1)

**模块职责：** 会话域 handler

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `logger` | `未显式标注` | [L41](../../../../../jiuwenswarm/server/handlers/session.py#L41) |
| `_LIMIT_DEFAULT` | `未显式标注` | [L44](../../../../../jiuwenswarm/server/handlers/session.py#L44) |
| `_LIMIT_MAX` | `未显式标注` | [L45](../../../../../jiuwenswarm/server/handlers/session.py#L45) |
| `_session_switch_locks` | `WeakValueDictionary[str, asyncio.Lock]` | [L50](../../../../../jiuwenswarm/server/handlers/session.py#L50) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def _coerce_int(value: object, default: int) -> int` | 宽松解析整数：兼容 int / 整数值 float / 数字字符串。 | [L55](../../../../../jiuwenswarm/server/handlers/session.py#L55) |
| `async def _resolve_rewind_agent(ctx, channel_id: str, session_id: str \| None = None) -> tuple[Any, Any] \| None` | Return (deep_agent, react_agent) for rewind context rebuild. | [L71](../../../../../jiuwenswarm/server/handlers/session.py#L71) |
| `def get_conversation_history(session_id: str, page_idx: int) -> dict[str, Any] \| None` | 源码未提供函数级文档字符串。 | [L128](../../../../../jiuwenswarm/server/handlers/session.py#L128) |
| `def _is_restorable_history_record(record: Any) -> bool` | Coarsely filter records that the web history UI cannot use for pagination. | [L178](../../../../../jiuwenswarm/server/handlers/session.py#L178) |
| `async def handle_session_list(ctx: RequestContext) -> None` | 处理 session.list 请求：返回历史会话基础信息列表。 | [L212](../../../../../jiuwenswarm/server/handlers/session.py#L212) |
| `async def handle_session_rename(ctx: RequestContext) -> None` | 处理 session.rename：与 CLI Gateway 本地回退共用 apply_session_rename。 | [L247](../../../../../jiuwenswarm/server/handlers/session.py#L247) |
| `async def handle_session_switch(ctx: RequestContext) -> None` | Switch product sessions without deleting recoverable session state. | [L279](../../../../../jiuwenswarm/server/handlers/session.py#L279) |
| `async def handle_session_delete(ctx: RequestContext) -> None` | Delete a single session and its recoverable runtime state. | [L367](../../../../../jiuwenswarm/server/handlers/session.py#L367) |
| `async def handle_session_rewind_full(ctx: RequestContext, restore_files: bool = False, compact: bool = False) -> None` | Full rewind: truncate history.json + context_engine + update checkpointer. | [L499](../../../../../jiuwenswarm/server/handlers/session.py#L499) |
| `async def handle_session_rewind_context(ctx: RequestContext) -> None` | Truncate history.json + in-memory context_engine for a session. | [L700](../../../../../jiuwenswarm/server/handlers/session.py#L700) |
| `async def handle_history_get(ctx: RequestContext) -> None` | 源码未提供函数级文档字符串。 | [L781](../../../../../jiuwenswarm/server/handlers/session.py#L781) |
| `async def handle_history_get_stream(ctx: RequestContext) -> None` | 源码未提供函数级文档字符串。 | [L806](../../../../../jiuwenswarm/server/handlers/session.py#L806) |

## `jiuwenswarm/server/handlers/team.py`

[打开源码](../../../../../jiuwenswarm/server/handlers/team.py#L1)

**模块职责：** 团队域 handler

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `logger` | `未显式标注` | [L42](../../../../../jiuwenswarm/server/handlers/team.py#L42) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def _team_binding_payload(binding: Any) -> dict[str, Any]` | 源码未提供函数级文档字符串。 | [L45](../../../../../jiuwenswarm/server/handlers/team.py#L45) |
| `def _create_team_binding_from_template(*, team_name: str, template_id: str, config_base: dict[str, Any]) -> Any` | 源码未提供函数级文档字符串。 | [L53](../../../../../jiuwenswarm/server/handlers/team.py#L53) |
| `async def _create_generated_team_binding(*, description: str, config_base: dict[str, Any]) -> tuple[Any, dict[str, Any]]` | Generate a unique team name and persist its binding and entity. | [L101](../../../../../jiuwenswarm/server/handlers/team.py#L101) |
| `async def _find_team_session_ids(team_name: str, *, sessions_root: str \| Path \| None = None) -> list[str]` | 源码未提供函数级文档字符串。 | [L151](../../../../../jiuwenswarm/server/handlers/team.py#L151) |
| `def _active_team_session_map(*, sessions_root: str \| Path \| None = None) -> dict[str, str]` | 源码未提供函数级文档字符串。 | [L190](../../../../../jiuwenswarm/server/handlers/team.py#L190) |
| `def _legacy_team_bindings_from_sessions(known_team_names: set[str]) -> list[dict[str, Any]]` | 源码未提供函数级文档字符串。 | [L216](../../../../../jiuwenswarm/server/handlers/team.py#L216) |
| `async def handle_team_templates_list(ctx: RequestContext) -> None` | 源码未提供函数级文档字符串。 | [L253](../../../../../jiuwenswarm/server/handlers/team.py#L253) |
| `async def handle_team_bindings_list(ctx: RequestContext) -> None` | 源码未提供函数级文档字符串。 | [L273](../../../../../jiuwenswarm/server/handlers/team.py#L273) |
| `async def handle_team_binding_create(ctx: RequestContext) -> None` | 源码未提供函数级文档字符串。 | [L352](../../../../../jiuwenswarm/server/handlers/team.py#L352) |
| `async def handle_team_binding_generate(ctx: RequestContext) -> None` | 源码未提供函数级文档字符串。 | [L387](../../../../../jiuwenswarm/server/handlers/team.py#L387) |
| `async def handle_team_session_bind(ctx: RequestContext) -> None` | 源码未提供函数级文档字符串。 | [L435](../../../../../jiuwenswarm/server/handlers/team.py#L435) |
| `async def handle_team_delete(ctx: RequestContext) -> None` | Delete a team and all team sessions that persist that team. | [L572](../../../../../jiuwenswarm/server/handlers/team.py#L572) |
| `async def handle_team_session_reset(ctx: RequestContext) -> None` | Reset a single team session: drop its task board + release its checkpoint, KEEP team_info / roster / team_home / session binding. | [L786](../../../../../jiuwenswarm/server/handlers/team.py#L786) |
| `async def handle_team_runtime_dissolve(ctx: RequestContext) -> None` | Dissolve one session's team runtime after a template/config change. | [L880](../../../../../jiuwenswarm/server/handlers/team.py#L880) |
| `async def handle_team_snapshot(ctx: RequestContext) -> None` | 源码未提供函数级文档字符串。 | [L1042](../../../../../jiuwenswarm/server/handlers/team.py#L1042) |
| `def _snapshot_tasks(payload: dict[str, Any] \| None) -> list[Any]` | 源码未提供函数级文档字符串。 | [L1139](../../../../../jiuwenswarm/server/handlers/team.py#L1139) |
| `async def handle_team_mq_publish(ctx: RequestContext) -> None` | Relay one external team event into the active core team runtime. | [L1148](../../../../../jiuwenswarm/server/handlers/team.py#L1148) |
| `async def handle_team_history_get(ctx: RequestContext) -> None` | 返回 team 模式历史记录的分页，避免与 history.get 并发竞争。 | [L1176](../../../../../jiuwenswarm/server/handlers/team.py#L1176) |
| `async def handle_team_members_get(ctx: RequestContext) -> None` | 返回 team human_agent 席位列表供 /join 校验。 | [L1265](../../../../../jiuwenswarm/server/handlers/team.py#L1265) |

## `jiuwenswarm/server/hooks/__init__.py`

[打开源码](../../../../../jiuwenswarm/server/hooks/__init__.py#L1)

**模块职责：** Server 层 Hooks 执行引擎.

本文件不定义顶级类、函数或模块状态；它只承担包标识/导入边界作用。

## `jiuwenswarm/server/hooks/executor.py`

[打开源码](../../../../../jiuwenswarm/server/hooks/executor.py#L1)

**模块职责：** Hook 执行器 —— 执行 command / prompt 两类 hook，返回统一 HookResult.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `logger` | `未显式标注` | [L14](../../../../../jiuwenswarm/server/hooks/executor.py#L14) |

### [`class HookOutcome`](../../../../../jiuwenswarm/server/hooks/executor.py#L17)

源码未提供类级文档字符串。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `SUCCESS` | `未显式标注` | `'success'` | [L18](../../../../../jiuwenswarm/server/hooks/executor.py#L18) |
| `BLOCKING` | `未显式标注` | `'blocking'` | [L19](../../../../../jiuwenswarm/server/hooks/executor.py#L19) |
| `NON_BLOCKING_ERROR` | `未显式标注` | `'non_blocking_error'` | [L20](../../../../../jiuwenswarm/server/hooks/executor.py#L20) |

### [`class HookResult`](../../../../../jiuwenswarm/server/hooks/executor.py#L24)

源码未提供类级文档字符串。

装饰器：`@dataclass`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `outcome` | `str` | `HookOutcome.SUCCESS` | [L25](../../../../../jiuwenswarm/server/hooks/executor.py#L25) |
| `error` | `str` | `''` | [L26](../../../../../jiuwenswarm/server/hooks/executor.py#L26) |
| `show_to_model` | `bool` | `False` | [L27](../../../../../jiuwenswarm/server/hooks/executor.py#L27) |
| `modified_input` | `dict \| None` | `None` | [L28](../../../../../jiuwenswarm/server/hooks/executor.py#L28) |
| `additional_context` | `str` | `''` | [L29](../../../../../jiuwenswarm/server/hooks/executor.py#L29) |

### [`class HookExecutor`](../../../../../jiuwenswarm/server/hooks/executor.py#L32)

统一调度 command / prompt hook 执行.

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `async def run_all(self, hook_configs: list[dict], hook_input: dict, session_id: str = '') -> list[HookResult]` | 并行执行同一 matcher 下的所有 hooks. | [L35](../../../../../jiuwenswarm/server/hooks/executor.py#L35) |
| `async def _run_command_hook(self, config: dict, hook_input: dict) -> HookResult` | 执行 command 类型 hook（子进程）. | [L60](../../../../../jiuwenswarm/server/hooks/executor.py#L60) |
| `@staticmethod def parse_command_output(stdout: str) -> HookResult` | 解析 command hook 的 stdout JSON 协议. | [L141](../../../../../jiuwenswarm/server/hooks/executor.py#L141) |
| `async def _run_prompt_hook(self, config: dict, hook_input: dict) -> HookResult` | 执行 prompt 类型 hook（LLM 审核）. | [L173](../../../../../jiuwenswarm/server/hooks/executor.py#L173) |
| `async def _query_llm(self, prompt: str, model_name: str = '') -> str` | 调用 LLM 执行 hook 审查. | [L223](../../../../../jiuwenswarm/server/hooks/executor.py#L223) |
| `@staticmethod def extract_json_from_response(text: str) -> dict` | 从 LLM 响应中提取 JSON 对象. | [L270](../../../../../jiuwenswarm/server/hooks/executor.py#L270) |

## `jiuwenswarm/server/hooks/user_hook_rail.py`

[打开源码](../../../../../jiuwenswarm/server/hooks/user_hook_rail.py#L1)

**模块职责：** UserHookRail —— 将用户配置的 hooks 以 Rail 形态注册到 DeepAgent，拦截工具调用和 Agent 生命周期.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `logger` | `未显式标注` | [L15](../../../../../jiuwenswarm/server/hooks/user_hook_rail.py#L15) |

### [`class UserHookRail(DeepAgentRail)`](../../../../../jiuwenswarm/server/hooks/user_hook_rail.py#L18)

用户配置的 hooks 执行引擎.

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `priority` | `未显式标注` | `60` | [L25](../../../../../jiuwenswarm/server/hooks/user_hook_rail.py#L25) |

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self, hooks_config: HooksConfig)` | 源码未提供方法级文档字符串。 | [L27](../../../../../jiuwenswarm/server/hooks/user_hook_rail.py#L27) |
| `async def before_tool_call(self, ctx: AgentCallbackContext) -> None` | 源码未提供方法级文档字符串。 | [L34](../../../../../jiuwenswarm/server/hooks/user_hook_rail.py#L34) |
| `async def after_tool_call(self, ctx: AgentCallbackContext) -> None` | 源码未提供方法级文档字符串。 | [L77](../../../../../jiuwenswarm/server/hooks/user_hook_rail.py#L77) |
| `async def on_tool_exception(self, ctx: AgentCallbackContext) -> None` | 源码未提供方法级文档字符串。 | [L110](../../../../../jiuwenswarm/server/hooks/user_hook_rail.py#L110) |
| `async def after_invoke(self, ctx: AgentCallbackContext) -> None` | 源码未提供方法级文档字符串。 | [L132](../../../../../jiuwenswarm/server/hooks/user_hook_rail.py#L132) |

## `jiuwenswarm/server/pipeline.py`

[打开源码](../../../../../jiuwenswarm/server/pipeline.py#L1)

**模块职责：** 请求流水线，ws与http两种传输的汇合点

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `logger` | `未显式标注` | [L33](../../../../../jiuwenswarm/server/pipeline.py#L33) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def _should_trigger_before_chat_request_hook(request: AgentRequest) -> bool` | 源码未提供函数级文档字符串。 | [L36](../../../../../jiuwenswarm/server/pipeline.py#L36) |
| `async def _trigger_before_chat_request_hook(request: AgentRequest) -> None` | 源码未提供函数级文档字符串。 | [L44](../../../../../jiuwenswarm/server/pipeline.py#L44) |
| `async def dispatch_parsed_request(ctx: RequestContext, request: AgentRequest, *, peer: Any = None) -> None` | 已解析出 ``AgentRequest`` 之后的**通用处理流水线**。 | [L61](../../../../../jiuwenswarm/server/pipeline.py#L61) |

## `jiuwenswarm/server/sandbox/__init__.py`

[打开源码](../../../../../jiuwenswarm/server/sandbox/__init__.py#L1)

**模块职责：** 包初始化与选择性重导出。

本文件不定义顶级类、函数或模块状态；它只承担包标识/导入边界作用。

## `jiuwenswarm/server/sandbox/jiuwenbox_runner.py`

[打开源码](../../../../../jiuwenswarm/server/sandbox/jiuwenbox_runner.py#L1)

**模块职责：** 管理本地 jiuwenbox uvicorn 子进程 — 由 ``/sandbox enable`` 触发启动.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `logger` | `未显式标注` | [L33](../../../../../jiuwenswarm/server/sandbox/jiuwenbox_runner.py#L33) |
| `_PR_SET_PDEATHSIG` | `未显式标注` | [L37](../../../../../jiuwenswarm/server/sandbox/jiuwenbox_runner.py#L37) |

### [`class JiuwenBoxRunner`](../../../../../jiuwenswarm/server/sandbox/jiuwenbox_runner.py#L70)

单例形态管理本地 jiuwenbox 子进程.

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `_INSTANCE` | `'JiuwenBoxRunner \| None'` | `None` | [L73](../../../../../jiuwenswarm/server/sandbox/jiuwenbox_runner.py#L73) |
| `_STDERR_TAIL_MAX` | `int` | `80` | [L75](../../../../../jiuwenswarm/server/sandbox/jiuwenbox_runner.py#L75) |

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self) -> None` | 源码未提供方法级文档字符串。 | [L77](../../../../../jiuwenswarm/server/sandbox/jiuwenbox_runner.py#L77) |
| `@classmethod def instance(cls) -> 'JiuwenBoxRunner'` | 源码未提供方法级文档字符串。 | [L98](../../../../../jiuwenswarm/server/sandbox/jiuwenbox_runner.py#L98) |
| `@property def base_url(self) -> str` | 源码未提供方法级文档字符串。 | [L104](../../../../../jiuwenswarm/server/sandbox/jiuwenbox_runner.py#L104) |
| `def get_stderr_tail(self, lines: int = 40) -> str` | 返回最近 ``lines`` 行子进程 stderr, 便于错误诊断. | [L107](../../../../../jiuwenswarm/server/sandbox/jiuwenbox_runner.py#L107) |
| `def is_owned_listener(self, host: str, port: int) -> bool` | ``True`` 表示当前 runner 持有一个仍在跑的子进程, 且监听在 ``host:port``. | [L113](../../../../../jiuwenswarm/server/sandbox/jiuwenbox_runner.py#L113) |
| `def get_owned_endpoint(self) -> Optional[tuple[str, int]]` | 返回当前由本 runner 拥有的 (host, port); 没有就返回 None. | [L126](../../../../../jiuwenswarm/server/sandbox/jiuwenbox_runner.py#L126) |
| `async def health_check(self, host: str \| None = None, port: int \| None = None) -> bool` | 源码未提供方法级文档字符串。 | [L135](../../../../../jiuwenswarm/server/sandbox/jiuwenbox_runner.py#L135) |
| `async def fetch_health(self, host: str \| None = None, port: int \| None = None) -> dict[str, Any] \| None` | Return parsed jiuwenbox ``/health`` JSON, or ``None`` on failure. | [L146](../../../../../jiuwenswarm/server/sandbox/jiuwenbox_runner.py#L146) |
| `async def ensure_running(self, host: str = '127.0.0.1', port: int = 8321, *, timeout: float = 30.0, startup_mode: str = 'internal', policy_path: Optional[Path] = None) -> bool` | 确保 jiuwenbox 在 ``host:port`` 已就绪。 | [L161](../../../../../jiuwenswarm/server/sandbox/jiuwenbox_runner.py#L161) |
| `async def _pump_stream(self, stream: Any, kind: str) -> None` | 持续读取子进程 stdout/stderr, 写入 logger debug; stderr 额外保留滚动尾部. | [L347](../../../../../jiuwenswarm/server/sandbox/jiuwenbox_runner.py#L347) |
| `async def _wait_until_ready(self, host: str, port: int, *, timeout: float) -> bool` | 源码未提供方法级文档字符串。 | [L371](../../../../../jiuwenswarm/server/sandbox/jiuwenbox_runner.py#L371) |
| `def _register_atexit_once(self) -> None` | 源码未提供方法级文档字符串。 | [L390](../../../../../jiuwenswarm/server/sandbox/jiuwenbox_runner.py#L390) |
| `def _sync_terminate(self) -> None` | 同步退出兜底: ``atexit`` / 异常退出场景调用, 不依赖事件循环. | [L399](../../../../../jiuwenswarm/server/sandbox/jiuwenbox_runner.py#L399) |
| `async def stop(self) -> None` | 优雅停止由本 runner 启动的子进程. | [L435](../../../../../jiuwenswarm/server/sandbox/jiuwenbox_runner.py#L435) |
| `async def _stop_no_lock(self) -> None` | ``stop()`` 的去锁版本; 调用方必须已经持有 ``self._lock``. | [L440](../../../../../jiuwenswarm/server/sandbox/jiuwenbox_runner.py#L440) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def _resolve_jiuwenbox_src_dir() -> Optional[Path]` | 探测仓库内 ``code_agent/jiuwenbox/src``; 若存在则供 PYTHONPATH 注入用. | [L40](../../../../../jiuwenswarm/server/sandbox/jiuwenbox_runner.py#L40) |
| `def _try_set_pdeathsig() -> None` | Linux: 让子进程在父进程退出时收到 SIGTERM, 避免 SIGKILL 父进程时 jiuwenbox 残留. | [L54](../../../../../jiuwenswarm/server/sandbox/jiuwenbox_runner.py#L54) |

## `jiuwenswarm/server/tool_concurrency.py`

[打开源码](../../../../../jiuwenswarm/server/tool_concurrency.py#L1)

**模块职责：** JiuWenSwarm adapter: load react.concurrency config and register core hook.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `_LOG_PREFIX` | `未显式标注` | [L12](../../../../../jiuwenswarm/server/tool_concurrency.py#L12) |
| `_logger` | `未显式标注` | [L13](../../../../../jiuwenswarm/server/tool_concurrency.py#L13) |
| `_controller` | `未显式标注` | [L33](../../../../../jiuwenswarm/server/tool_concurrency.py#L33) |

### [`class ToolConcurrencyRule`](../../../../../jiuwenswarm/server/tool_concurrency.py#L17)

源码未提供类级文档字符串。

装饰器：`@dataclass(frozen=True)`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `limit` | `int` | `—` | [L18](../../../../../jiuwenswarm/server/tool_concurrency.py#L18) |

### [`class ConcurrencyPolicy`](../../../../../jiuwenswarm/server/tool_concurrency.py#L22)

源码未提供类级文档字符串。

装饰器：`@dataclass(frozen=True)`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `enabled` | `bool` | `True` | [L23](../../../../../jiuwenswarm/server/tool_concurrency.py#L23) |
| `tools` | `dict[str, ToolConcurrencyRule]` | `field(default_factory=dict)` | [L24](../../../../../jiuwenswarm/server/tool_concurrency.py#L24) |

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def as_log_text(self) -> str` | 源码未提供方法级文档字符串。 | [L26](../../../../../jiuwenswarm/server/tool_concurrency.py#L26) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def _normalize_tool_name(name: str \| None) -> str` | 源码未提供函数级文档字符串。 | [L36](../../../../../jiuwenswarm/server/tool_concurrency.py#L36) |
| `def resolve_concurrency_policy(config_provider: Callable[[], Mapping[str, Any]] \| None = None) -> ConcurrencyPolicy` | 源码未提供函数级文档字符串。 | [L40](../../../../../jiuwenswarm/server/tool_concurrency.py#L40) |
| `def _parse_limit(raw: Any) -> int \| None` | 源码未提供函数级文档字符串。 | [L48](../../../../../jiuwenswarm/server/tool_concurrency.py#L48) |
| `def _load_policy_from_mapping(config: Mapping[str, Any] \| None) -> ConcurrencyPolicy` | 源码未提供函数级文档字符串。 | [L72](../../../../../jiuwenswarm/server/tool_concurrency.py#L72) |
| `def _load_policy_from_config() -> ConcurrencyPolicy` | 源码未提供函数级文档字符串。 | [L102](../../../../../jiuwenswarm/server/tool_concurrency.py#L102) |
| `def _to_core_policy(policy: ConcurrencyPolicy)` | 源码未提供函数级文档字符串。 | [L121](../../../../../jiuwenswarm/server/tool_concurrency.py#L121) |
| `def _get_controller()` | Return the process-wide controller singleton (asyncio event loop only). | [L136](../../../../../jiuwenswarm/server/tool_concurrency.py#L136) |
| `def register_tool_batch_concurrency() -> None` | Wire jiuwenswarm config into AbilityManager via openjiuwen core hook. | [L150](../../../../../jiuwenswarm/server/tool_concurrency.py#L150) |
| `def apply_tool_concurrency_limit() -> None` | Register batch tool concurrency via openjiuwen AbilityManager core hook. | [L175](../../../../../jiuwenswarm/server/tool_concurrency.py#L175) |

## `jiuwenswarm/server/transports/__init__.py`

[打开源码](../../../../../jiuwenswarm/server/transports/__init__.py#L1)

**模块职责：** 传输层：WebSocket / HTTP / SSE 的对等实现，共享传输无关的业务层。

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `__all__` | `未显式标注` | [L21](../../../../../jiuwenswarm/server/transports/__init__.py#L21) |

## `jiuwenswarm/server/transports/push_registry.py`

[打开源码](../../../../../jiuwenswarm/server/transports/push_registry.py#L1)

**模块职责：** 服务端主动推送的订阅者注册表

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `logger` | `未显式标注` | [L48](../../../../../jiuwenswarm/server/transports/push_registry.py#L48) |
| `WS_PUSH_SUBSCRIBER_ID_PREFIX` | `未显式标注` | [L53](../../../../../jiuwenswarm/server/transports/push_registry.py#L53) |
| `WS_PUSH_SUBSCRIBER_ID` | `未显式标注` | [L54](../../../../../jiuwenswarm/server/transports/push_registry.py#L54) |
| `SEND_TIMEOUT` | `未显式标注` | [L64](../../../../../jiuwenswarm/server/transports/push_registry.py#L64) |
| `_REGISTRY` | `未显式标注` | [L278](../../../../../jiuwenswarm/server/transports/push_registry.py#L278) |

### [`class _Subscriber`](../../../../../jiuwenswarm/server/transports/push_registry.py#L68)

一个已注册的推送订阅者。

装饰器：`@dataclass(frozen=True)`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `sink` | `ResponseSink` | `—` | [L88](../../../../../jiuwenswarm/server/transports/push_registry.py#L88) |
| `session_id` | `str \| None` | `None` | [L89](../../../../../jiuwenswarm/server/transports/push_registry.py#L89) |
| `channel_id` | `str \| None` | `None` | [L90](../../../../../jiuwenswarm/server/transports/push_registry.py#L90) |
| `drop_on_stall` | `bool` | `True` | [L91](../../../../../jiuwenswarm/server/transports/push_registry.py#L91) |

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def matches(self, wire: dict[str, Any]) -> bool` | 本订阅者是否该收到这条推送。 | [L93](../../../../../jiuwenswarm/server/transports/push_registry.py#L93) |

### [`class PushRegistry`](../../../../../jiuwenswarm/server/transports/push_registry.py#L106)

推送订阅者注册表：把「推给当前连接」变成「推给匹配的订阅者」。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `__slots__` | `未显式标注` | `('_reverse_rpc_owner_id', '_reverse_rpc_owner_lost_callback', '_subscribers')` | [L109](../../../../../jiuwenswarm/server/transports/push_registry.py#L109) |

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self) -> None` | 源码未提供方法级文档字符串。 | [L115](../../../../../jiuwenswarm/server/transports/push_registry.py#L115) |
| `def set_reverse_rpc_owner_lost_callback(self, callback: Callable[[], None] \| None) -> None` | 源码未提供方法级文档字符串。 | [L120](../../../../../jiuwenswarm/server/transports/push_registry.py#L120) |
| `def _notify_reverse_rpc_owner_lost(self) -> None` | 源码未提供方法级文档字符串。 | [L125](../../../../../jiuwenswarm/server/transports/push_registry.py#L125) |
| `def register(self, subscriber_id: str, sink: ResponseSink, *, session_id: str \| None = None, channel_id: str \| None = None, drop_on_stall: bool = True, reverse_rpc_capable: bool = False) -> None` | 登记一个订阅者。同 ``subscriber_id`` 重复注册会覆盖旧的。 | [L130](../../../../../jiuwenswarm/server/transports/push_registry.py#L130) |
| `def unregister(self, subscriber_id: str) -> None` | 注销订阅者。不存在时静默返回（断连清理可能重入）。 | [L170](../../../../../jiuwenswarm/server/transports/push_registry.py#L170) |
| `def subscriber_count(self) -> int` | 源码未提供方法级文档字符串。 | [L182](../../../../../jiuwenswarm/server/transports/push_registry.py#L182) |
| `def reverse_rpc_ready(self) -> bool` | 源码未提供方法级文档字符串。 | [L185](../../../../../jiuwenswarm/server/transports/push_registry.py#L185) |
| `async def push_reverse_rpc(self, wire: dict[str, Any]) -> int` | Deliver a point-to-point reverse RPC to the current Gateway owner. | [L189](../../../../../jiuwenswarm/server/transports/push_registry.py#L189) |
| `async def push(self, wire: dict[str, Any]) -> int` | 向匹配的订阅者扇出一条已构造好的 wire 帧。 | [L220](../../../../../jiuwenswarm/server/transports/push_registry.py#L220) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def make_ws_push_subscriber_id(ws: Any) -> str` | 为一条 Gateway/Relay WebSocket 生成 PushRegistry 订阅 id。 | [L57](../../../../../jiuwenswarm/server/transports/push_registry.py#L57) |
| `def get_push_registry() -> PushRegistry` | 取进程级推送注册表。 | [L281](../../../../../jiuwenswarm/server/transports/push_registry.py#L281) |

## `jiuwenswarm/server/transports/sink.py`

[打开源码](../../../../../jiuwenswarm/server/transports/sink.py#L1)

**模块职责：** ``ResponseSink``：业务层的统一出口，如何发送消息收敛到传输层。

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `logger` | `未显式标注` | [L19](../../../../../jiuwenswarm/server/transports/sink.py#L19) |
| `STREAM_DONE` | `未显式标注` | [L22](../../../../../jiuwenswarm/server/transports/sink.py#L22) |
| `FINISH_TIMEOUT` | `未显式标注` | [L26](../../../../../jiuwenswarm/server/transports/sink.py#L26) |

### [`class ResponseSink(Protocol)`](../../../../../jiuwenswarm/server/transports/sink.py#L30)

业务层出口协议。实现方负责编码与实际发送。

装饰器：`@runtime_checkable`。

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `async def send_unary(self, resp: AgentResponse, *, response_id: str \| None = None) -> bool` | 发送非流式响应。返回是否原样送出。 | [L33](../../../../../jiuwenswarm/server/transports/sink.py#L33) |
| `async def send_chunk(self, chunk: AgentResponseChunk, *, sequence: int, response_id: str \| None = None) -> bool` | 发送一个流式分片。返回是否原样送出。 | [L37](../../../../../jiuwenswarm/server/transports/sink.py#L37) |
| `async def send_error(self, request_id: str, message: str, *, code: str = 'INTERNAL_ERROR', channel_id: str = '') -> bool` | 发送错误响应。 | [L47](../../../../../jiuwenswarm/server/transports/sink.py#L47) |
| `async def send_wire(self, wire: dict[str, Any]) -> bool` | 逃生通道：已自行构造好 wire 帧的少数场景。 | [L53](../../../../../jiuwenswarm/server/transports/sink.py#L53) |

### [`class WSSink`](../../../../../jiuwenswarm/server/transports/sink.py#L70)

WebSocket 实现：编码后经 ``send_wire_payload`` 写 socket。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `__slots__` | `未显式标注` | `('_ws', '_send_lock')` | [L77](../../../../../jiuwenswarm/server/transports/sink.py#L77) |

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self, ws: Any, send_lock: asyncio.Lock) -> None` | 源码未提供方法级文档字符串。 | [L79](../../../../../jiuwenswarm/server/transports/sink.py#L79) |
| `async def send_unary(self, resp: AgentResponse, *, response_id: str \| None = None) -> bool` | 源码未提供方法级文档字符串。 | [L83](../../../../../jiuwenswarm/server/transports/sink.py#L83) |
| `async def send_chunk(self, chunk: AgentResponseChunk, *, sequence: int, response_id: str \| None = None) -> bool` | 源码未提供方法级文档字符串。 | [L89](../../../../../jiuwenswarm/server/transports/sink.py#L89) |
| `async def send_error(self, request_id: str, message: str, *, code: str = 'INTERNAL_ERROR', channel_id: str = '') -> bool` | 源码未提供方法级文档字符串。 | [L97](../../../../../jiuwenswarm/server/transports/sink.py#L97) |
| `async def send_wire(self, wire: dict[str, Any]) -> bool` | 源码未提供方法级文档字符串。 | [L102](../../../../../jiuwenswarm/server/transports/sink.py#L102) |

### [`class UnaryHTTPSink`](../../../../../jiuwenswarm/server/transports/sink.py#L107)

HTTP 非流式实现：持有业务对象，把序列化次数降到下限。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `__slots__` | `未显式标注` | `('response', 'wire', 'frames')` | [L132](../../../../../jiuwenswarm/server/transports/sink.py#L132) |

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self) -> None` | 源码未提供方法级文档字符串。 | [L134](../../../../../jiuwenswarm/server/transports/sink.py#L134) |
| `async def send_unary(self, resp: AgentResponse, *, response_id: str \| None = None) -> bool` | 记下业务对象，并施加与 WS 相同的发送预算。 | [L142](../../../../../jiuwenswarm/server/transports/sink.py#L142) |
| `async def send_chunk(self, chunk: AgentResponseChunk, *, sequence: int, response_id: str \| None = None) -> bool` | 非流式入口收到 chunk：保留但不覆盖 response，交由路由层决定如何合并。 | [L166](../../../../../jiuwenswarm/server/transports/sink.py#L166) |
| `async def send_error(self, request_id: str, message: str, *, code: str = 'INTERNAL_ERROR', channel_id: str = '') -> bool` | 源码未提供方法级文档字符串。 | [L181](../../../../../jiuwenswarm/server/transports/sink.py#L181) |
| `@property def last_frame(self) -> dict[str, Any] \| None` | 最后一帧的 wire 形式 —— 与 ``CollectingSink.last_frame`` 同义， | [L187](../../../../../jiuwenswarm/server/transports/sink.py#L187) |
| `async def send_wire(self, wire: dict[str, Any]) -> bool` | 记下帧并施加与 WS 相同的发送预算。 | [L210](../../../../../jiuwenswarm/server/transports/sink.py#L210) |

### [`class SSESink`](../../../../../jiuwenswarm/server/transports/sink.py#L229)

SSE 流式实现：把业务对象编码成 wire 帧后入队，由 SSE 生成器消费。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `__slots__` | `未显式标注` | `('queue',)` | [L238](../../../../../jiuwenswarm/server/transports/sink.py#L238) |

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self, maxsize: int = 256) -> None` | 源码未提供方法级文档字符串。 | [L240](../../../../../jiuwenswarm/server/transports/sink.py#L240) |
| `async def send_unary(self, resp: AgentResponse, *, response_id: str \| None = None) -> bool` | 源码未提供方法级文档字符串。 | [L243](../../../../../jiuwenswarm/server/transports/sink.py#L243) |
| `async def send_chunk(self, chunk: AgentResponseChunk, *, sequence: int, response_id: str \| None = None) -> bool` | 源码未提供方法级文档字符串。 | [L249](../../../../../jiuwenswarm/server/transports/sink.py#L249) |
| `async def send_error(self, request_id: str, message: str, *, code: str = 'INTERNAL_ERROR', channel_id: str = '') -> bool` | 源码未提供方法级文档字符串。 | [L257](../../../../../jiuwenswarm/server/transports/sink.py#L257) |
| `async def send_wire(self, wire: dict[str, Any]) -> bool` | 入队并施加与 WS 相同的发送预算。 | [L262](../../../../../jiuwenswarm/server/transports/sink.py#L262) |
| `async def offer(self, item: Any) -> bool` | **有界**入队，供收尾路径使用；返回是否投递成功。 | [L273](../../../../../jiuwenswarm/server/transports/sink.py#L273) |
| `async def finish(self) -> None` | handler 结束后放入哨兵，通知 SSE 生成器收尾。**保证不会永久阻塞。** | [L303](../../../../../jiuwenswarm/server/transports/sink.py#L303) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def _error_response(request_id: str, message: str, code: str, channel_id: str) -> AgentResponse` | 源码未提供函数级文档字符串。 | [L61](../../../../../jiuwenswarm/server/transports/sink.py#L61) |

## `jiuwenswarm/server/utils/__init__.py`

[打开源码](../../../../../jiuwenswarm/server/utils/__init__.py#L1)

**模块职责：** 包初始化与选择性重导出。

本文件不定义顶级类、函数或模块状态；它只承担包标识/导入边界作用。

## `jiuwenswarm/server/utils/diff_service.py`

[打开源码](../../../../../jiuwenswarm/server/utils/diff_service.py#L1)

**模块职责：** Turn-based diff service for /diff command.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `logger` | `未显式标注` | [L22](../../../../../jiuwenswarm/server/utils/diff_service.py#L22) |
| `INTERNAL_UNTRACKED_DIRS` | `未显式标注` | [L24](../../../../../jiuwenswarm/server/utils/diff_service.py#L24) |
| `MAX_FILES` | `未显式标注` | [L27](../../../../../jiuwenswarm/server/utils/diff_service.py#L27) |
| `MAX_DIFF_SIZE_BYTES` | `未显式标注` | [L28](../../../../../jiuwenswarm/server/utils/diff_service.py#L28) |
| `MAX_LINES_PER_FILE` | `未显式标注` | [L29](../../../../../jiuwenswarm/server/utils/diff_service.py#L29) |
| `MAX_FILES_FOR_DETAILS` | `未显式标注` | [L30](../../../../../jiuwenswarm/server/utils/diff_service.py#L30) |
| `HISTORY_PRIORITY_PROJECT_ROOT` | `未显式标注` | [L31](../../../../../jiuwenswarm/server/utils/diff_service.py#L31) |
| `HISTORY_PRIORITY_SHARED_WORKSPACE` | `未显式标注` | [L32](../../../../../jiuwenswarm/server/utils/diff_service.py#L32) |
| `HISTORY_PRIORITY_EXTRA_ROOT` | `未显式标注` | [L33](../../../../../jiuwenswarm/server/utils/diff_service.py#L33) |
| `HISTORY_PRIORITY_UNKNOWN` | `未显式标注` | [L34](../../../../../jiuwenswarm/server/utils/diff_service.py#L34) |
| `WORKTREE_HISTORY_CONTAINERS` | `tuple[tuple[str, ...], ...]` | [L35](../../../../../jiuwenswarm/server/utils/diff_service.py#L35) |
| `_CHANGE_SET_LOCK` | `未显式标注` | [L41](../../../../../jiuwenswarm/server/utils/diff_service.py#L41) |
| `_REWOUND_KEY` | `未显式标注` | [L45](../../../../../jiuwenswarm/server/utils/diff_service.py#L45) |
| `_DISCARDED_KEY` | `未显式标注` | [L51](../../../../../jiuwenswarm/server/utils/diff_service.py#L51) |
| `_diff_service` | `DiffService \| None` | [L2360](../../../../../jiuwenswarm/server/utils/diff_service.py#L2360) |

### [`class DiffHistoryExpiredError(RuntimeError)`](../../../../../jiuwenswarm/server/utils/diff_service.py#L54)

历史 diff 索引仍存在但详情已无法重建。

### [`class DiffService`](../../../../../jiuwenswarm/server/utils/diff_service.py#L58)

提供 turn-based diff 查询服务.

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def __init__(self) -> None` | 源码未提供方法级文档字符串。 | [L61](../../../../../jiuwenswarm/server/utils/diff_service.py#L61) |
| `def get_turn_diffs(self, session_id: str, project_dir: str \| None = None, repo_context: dict[str, Any] \| None = None, extra_history_roots: list[str] \| None = None) -> list[dict[str, Any]]` | 获取 session 的所有 turn diff（完整信息）. | [L64](../../../../../jiuwenswarm/server/utils/diff_service.py#L64) |
| `def get_turn_diff_summaries(self, session_id: str, project_dir: str \| None = None, repo_context: dict[str, Any] \| None = None, extra_history_roots: list[str] \| None = None) -> list[dict[str, Any]]` | 获取 session 的历史 turn diff 摘要，包含已持久化快照。 | [L84](../../../../../jiuwenswarm/server/utils/diff_service.py#L84) |
| `def get_turn_diff(self, session_id: str, *, turn_index: int \| None = None, change_set_id: str \| None = None, project_dir: str \| None = None, repo_context: dict[str, Any] \| None = None, extra_history_roots: list[str] \| None = None) -> dict[str, Any] \| None` | 获取指定轮次的 turn diff。 | [L117](../../../../../jiuwenswarm/server/utils/diff_service.py#L117) |
| `def _compute_turn_diffs(self, session_id: str, project_dir: str \| None = None, *, extra_history_roots: list[str] \| None = None) -> list[dict[str, Any]]` | 计算 turn-based diffs. | [L193](../../../../../jiuwenswarm/server/utils/diff_service.py#L193) |
| `@staticmethod def _change_sets_path(session_id: str) -> Path` | 源码未提供方法级文档字符串。 | [L305](../../../../../jiuwenswarm/server/utils/diff_service.py#L305) |
| `@staticmethod def _change_set_snapshots_dir(session_id: str) -> Path` | 源码未提供方法级文档字符串。 | [L309](../../../../../jiuwenswarm/server/utils/diff_service.py#L309) |
| `@classmethod def _change_set_snapshot_path(cls, session_id: str, change_set_id: str) -> Path` | 源码未提供方法级文档字符串。 | [L313](../../../../../jiuwenswarm/server/utils/diff_service.py#L313) |
| `def _load_change_sets(self, session_id: str) -> list[dict[str, Any]]` | 源码未提供方法级文档字符串。 | [L317](../../../../../jiuwenswarm/server/utils/diff_service.py#L317) |
| `def _save_change_sets(self, session_id: str, change_sets: list[dict[str, Any]]) -> None` | 源码未提供方法级文档字符串。 | [L329](../../../../../jiuwenswarm/server/utils/diff_service.py#L329) |
| `def _load_turn_snapshot(self, session_id: str, change_set_id: str) -> dict[str, Any] \| None` | 源码未提供方法级文档字符串。 | [L347](../../../../../jiuwenswarm/server/utils/diff_service.py#L347) |
| `def _save_turn_snapshot(self, session_id: str, turn: dict[str, Any]) -> None` | 源码未提供方法级文档字符串。 | [L361](../../../../../jiuwenswarm/server/utils/diff_service.py#L361) |
| `@staticmethod def _entry_matches_turn(entry: dict[str, Any], turn: dict[str, Any]) -> bool` | 校验 change_set entry 是否仍属于当前 turn。 | [L383](../../../../../jiuwenswarm/server/utils/diff_service.py#L383) |
| `@staticmethod def _turn_from_change_set_entry(entry: dict[str, Any]) -> dict[str, Any]` | 源码未提供方法级文档字符串。 | [L396](../../../../../jiuwenswarm/server/utils/diff_service.py#L396) |
| `@staticmethod def _apply_change_set_entry(turn: dict[str, Any], entry: dict[str, Any]) -> None` | 源码未提供方法级文档字符串。 | [L420](../../../../../jiuwenswarm/server/utils/diff_service.py#L420) |
| `@staticmethod def _new_change_set_entry(session_id: str, turn: dict[str, Any], turn_index: int, repo_context: dict[str, Any] \| None = None) -> dict[str, Any]` | 源码未提供方法级文档字符串。 | [L431](../../../../../jiuwenswarm/server/utils/diff_service.py#L431) |
| `def mark_turn_discarded(self, session_id: str, turn_index: int, project_dir: str \| None = None, *, extra_history_roots: list[str] \| None = None) -> str \| None` | 将指定 turn 的 change_set 状态标记为 discarded。 | [L462](../../../../../jiuwenswarm/server/utils/diff_service.py#L462) |
| `def unmark_turn_discarded(self, session_id: str, turn_index: int, project_dir: str \| None = None, *, extra_history_roots: list[str] \| None = None) -> str \| None` | 将指定 turn 的 status 恢复为 completed(与 ``mark_turn_discarded`` 对称). | [L496](../../../../../jiuwenswarm/server/utils/diff_service.py#L496) |
| `def _enrich_with_change_sets(self, session_id: str, turns: list[dict[str, Any]], repo_context: dict[str, Any] \| None = None) -> None` | 惰性回填 change_set 索引并合并元数据到每轮 turn。 | [L561](../../../../../jiuwenswarm/server/utils/diff_service.py#L561) |
| `@staticmethod def _is_turn_end(record: dict[str, Any]) -> bool` | 判断一条记录是否是 turn 的结束. | [L601](../../../../../jiuwenswarm/server/utils/diff_service.py#L601) |
| `@staticmethod def _find_next_user_time(history: list[dict[str, Any]], user_index: int) -> float \| None` | 查找下次用户消息时间. | [L611](../../../../../jiuwenswarm/server/utils/diff_service.py#L611) |
| `@staticmethod def _find_assistant_message_id(history: list[dict[str, Any]], user_index: int) -> str` | 查找当前 user turn 后第一条 assistant 消息 ID。 | [L621](../../../../../jiuwenswarm/server/utils/diff_service.py#L621) |
| `@staticmethod def _read_history(session_id: str) -> list[dict[str, Any]]` | 读取 session history. | [L640](../../../../../jiuwenswarm/server/utils/diff_service.py#L640) |
| `@staticmethod def resolve_project_dir(session_id: str) -> str \| None` | 解析 session 的项目目录(``_get_project_dir_from_metadata`` 的公开入口). | [L648](../../../../../jiuwenswarm/server/utils/diff_service.py#L648) |
| `@staticmethod def _get_project_dir_from_metadata(session_id: str) -> str \| None` | 从 session metadata.json 中读取项目目录. | [L660](../../../../../jiuwenswarm/server/utils/diff_service.py#L660) |
| `@staticmethod def _is_valid_file_ops_file(name: str, session_id: str \| None, require_session: bool = False) -> bool` | 检查文件名是否是有效的 file_ops 文件. | [L700](../../../../../jiuwenswarm/server/utils/diff_service.py#L700) |
| `@staticmethod def _agent_history_dirs_for_roots(history_roots: list[str], *, include_child_workspaces: bool = False) -> list[Path]` | Return .agent_history dirs for roots and optional immediate workspaces. | [L730](../../../../../jiuwenswarm/server/utils/diff_service.py#L730) |
| `@staticmethod def _default_worktree_history_roots(project_dir: str \| None) -> list[str]` | Return known local worktree container dirs under the project root. | [L766](../../../../../jiuwenswarm/server/utils/diff_service.py#L766) |
| `@classmethod def _history_roots_with_worktree_containers(cls, project_dir: str \| None, extra_history_roots: list[str] \| None = None) -> list[str]` | Return explicit roots plus known worktree containers below each root. | [L774](../../../../../jiuwenswarm/server/utils/diff_service.py#L774) |
| `@staticmethod def _get_git_common_worktree_root(worktree_root: Path) -> Path \| None` | Return the canonical repo root for a linked git worktree, if known. | [L810](../../../../../jiuwenswarm/server/utils/diff_service.py#L810) |
| `@staticmethod def _map_worktree_file_path(file_path: str, *, source_root: Path, target_root: Path \| None) -> str` | Map a file-op path from a linked worktree back to the canonical repo. | [L840](../../../../../jiuwenswarm/server/utils/diff_service.py#L840) |
| `def _read_agent_history(self, session_id: str \| None = None, project_dir: str \| None = None, *, extra_history_roots: list[str] \| None = None, include_rewound: bool = False) -> dict[str, Any]` | 读取 .agent_history（同时读取全局与 session-specific 文件并合并）. | [L857](../../../../../jiuwenswarm/server/utils/diff_service.py#L857) |
| `def _find_file_edits_by_time_range(self, agent_history: dict[str, Any], start_time: float, end_time: float \| None) -> dict[str, dict[str, Any]]` | 根据时间范围查找文件编辑记录. | [L1046](../../../../../jiuwenswarm/server/utils/diff_service.py#L1046) |
| `@staticmethod def _iso_to_timestamp(iso_str: str) -> float` | 将 ISO 8601 字符串转换为 Unix timestamp. | [L1079](../../../../../jiuwenswarm/server/utils/diff_service.py#L1079) |
| `@staticmethod def _timestamp_to_iso(timestamp: float) -> str` | 将 Unix timestamp 转换为 ISO 8601 字符串. | [L1085](../../../../../jiuwenswarm/server/utils/diff_service.py#L1085) |
| `@staticmethod def _compute_hunks(old_content: str \| None, new_content: str \| None, max_lines: int = MAX_LINES_PER_FILE) -> tuple[list[dict[str, Any]], bool]` | 计算结构化 diff hunks. | [L1091](../../../../../jiuwenswarm/server/utils/diff_service.py#L1091) |
| `@staticmethod def _decode_c_escaped(inner: str) -> str` | Decode git's C-style escapes in an already-unquoted path segment. | [L1250](../../../../../jiuwenswarm/server/utils/diff_service.py#L1250) |
| `@staticmethod def _unquote_git_path(path: str) -> str` | Decode a git-quoted path back to its raw bytes. | [L1298](../../../../../jiuwenswarm/server/utils/diff_service.py#L1298) |
| `@staticmethod def _extract_diff_header_path(token: str) -> str \| None` | Extract the on-disk relative path from a ``--- a/`` / ``+++ b/`` token. | [L1315](../../../../../jiuwenswarm/server/utils/diff_service.py#L1315) |
| `@staticmethod def _run_git_command(project_dir: str, args: list[str]) -> str \| None` | 在 project_dir 中运行 git 命令，返回 stdout 或 None. | [L1335](../../../../../jiuwenswarm/server/utils/diff_service.py#L1335) |
| `@staticmethod def _get_git_toplevel(project_dir: str) -> str \| None` | 返回 git 仓库根目录；project_dir 可以是仓库内任意子目录. | [L1355](../../../../../jiuwenswarm/server/utils/diff_service.py#L1355) |
| `@staticmethod def _is_in_transient_git_state(project_dir: str) -> bool` | 检测是否处于 merge/rebase/cherry-pick/revert 等瞬态 git 状态. | [L1376](../../../../../jiuwenswarm/server/utils/diff_service.py#L1376) |
| `@staticmethod def _parse_git_numstat(output: str) -> dict[str, dict[str, int \| bool]]` | 解析 git diff --numstat 输出为 per-file 统计. | [L1410](../../../../../jiuwenswarm/server/utils/diff_service.py#L1410) |
| `@staticmethod def _parse_git_name_status(output: str) -> dict[str, str]` | 解析 git diff --name-status 输出为路径到状态的映射。 | [L1456](../../../../../jiuwenswarm/server/utils/diff_service.py#L1456) |
| `@staticmethod def _parse_git_porcelain_status(output: str) -> dict[str, str]` | 解析 git status --porcelain=v1 输出为路径到状态的映射。 | [L1484](../../../../../jiuwenswarm/server/utils/diff_service.py#L1484) |
| `@staticmethod def _parse_shortstat(output: str) -> dict[str, int] \| None` | 解析 git diff --shortstat 输出. | [L1517](../../../../../jiuwenswarm/server/utils/diff_service.py#L1517) |
| `@staticmethod def _parse_git_diff_hunks(output: str) -> dict[str, list[dict[str, Any]]]` | 解析 git diff 输出为按文件分组的 hunk 列表. | [L1538](../../../../../jiuwenswarm/server/utils/diff_service.py#L1538) |
| `@staticmethod def _split_large_file_diffs(output: str) -> tuple[str, set[str]]` | 将 git diff 输出按文件切分，跳过超过 MAX_DIFF_SIZE_BYTES 的文件块. | [L1630](../../../../../jiuwenswarm/server/utils/diff_service.py#L1630) |
| `def _get_untracked_files(self, project_dir: str, max_files: int = MAX_FILES, *, include_hunks: bool = True, hunk_paths: set[str] \| None = None) -> dict[str, dict[str, Any]]` | 获取未跟踪文件列表，并读取内容计算行数与 hunk. | [L1668](../../../../../jiuwenswarm/server/utils/diff_service.py#L1668) |
| `@staticmethod def _is_internal_untracked_path(rel_path: str) -> bool` | 源码未提供方法级文档字符串。 | [L1778](../../../../../jiuwenswarm/server/utils/diff_service.py#L1778) |
| `@staticmethod def _normalize_hunk_paths(repo_dir: str, hunk_paths: list[str] \| set[str] \| tuple[str, ...] \| None) -> set[str] \| None` | Normalize requested detail paths to repo-relative POSIX-style paths. | [L1783](../../../../../jiuwenswarm/server/utils/diff_service.py#L1783) |
| `def get_git_diff(self, project_dir: str \| None, *, include_files: bool = True, include_hunks: bool = True, hunk_paths: list[str] \| set[str] \| tuple[str, ...] \| None = None) -> dict[str, Any] \| None` | 获取工作区相对于 HEAD 的 git diff，含未跟踪文件行数. | [L1806](../../../../../jiuwenswarm/server/utils/diff_service.py#L1806) |
| `@staticmethod def _finalize_turn(turn: dict[str, Any]) -> None` | 完成 turn 的统计信息计算. | [L1968](../../../../../jiuwenswarm/server/utils/diff_service.py#L1968) |
| `def get_files_to_restore(self, session_id: str, turn_index: int, project_dir: str \| None = None, *, extra_history_roots: list[str] \| None = None) -> dict[str, dict[str, Any]]` | 返回需要恢复的文件及其目标内容. | [L1978](../../../../../jiuwenswarm/server/utils/diff_service.py#L1978) |
| `def get_files_to_redo(self, session_id: str, turn_index: int, project_dir: str \| None = None, *, extra_history_roots: list[str] \| None = None) -> dict[str, dict[str, Any]]` | 返回需要重新应用的文件及其新内容(与 ``get_files_to_restore`` 对称). | [L2050](../../../../../jiuwenswarm/server/utils/diff_service.py#L2050) |
| `def _collect_session_file_ops_paths(self, session_id: str, project_dir: str \| None = None, *, extra_history_roots: list[str] \| None = None) -> list[Path]` | 收集所有 session-specific file_ops 文件路径。 | [L2124](../../../../../jiuwenswarm/server/utils/diff_service.py#L2124) |
| `def truncate_file_ops_by_timestamp(self, session_id: str, cutoff_ts: float, project_dir: str \| None = None, soft: bool = False, *, extra_history_roots: list[str] \| None = None, discarded: bool = False) -> None` | 截断 file_ops 日志，移除 timestamp >= cutoff_ts 的条目. | [L2171](../../../../../jiuwenswarm/server/utils/diff_service.py#L2171) |
| `def restore_rewound_entries_by_timestamp(self, session_id: str, cutoff_ts: float, project_dir: str \| None = None, *, extra_history_roots: list[str] \| None = None, discarded: bool = False) -> None` | 去掉 file_ops 中 timestamp >= cutoff_ts 的条目的软删除标记. | [L2284](../../../../../jiuwenswarm/server/utils/diff_service.py#L2284) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def get_diff_service() -> DiffService` | 获取 DiffService 单例实例. | [L2363](../../../../../jiuwenswarm/server/utils/diff_service.py#L2363) |

## `jiuwenswarm/server/utils/stream_utils.py`

[打开源码](../../../../../jiuwenswarm/server/utils/stream_utils.py#L1)

**模块职责：** Stream utilities for parsing agent output chunks.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `logger` | `未显式标注` | [L10](../../../../../jiuwenswarm/server/utils/stream_utils.py#L10) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def _propagate_stream_source_id(src_payload: Any) -> dict[str, Any]` | 从上游 payload 提取 stream_source_id（skill_turbo 并发节点用它标识 source）。 | [L13](../../../../../jiuwenswarm/server/utils/stream_utils.py#L13) |
| `def parse_stream_chunk(chunk: Any, *, _has_streamed_content: bool = False) -> dict[str, Any] \| None` | Parse agent output chunk to frontend-consumable payload dict. | [L28](../../../../../jiuwenswarm/server/utils/stream_utils.py#L28) |
| `def _parse_dict_chunk(chunk: dict[str, Any], _has_streamed_content: bool) -> dict[str, Any] \| None` | Parse dict chunk. | [L65](../../../../../jiuwenswarm/server/utils/stream_utils.py#L65) |
| `def _serialize_chunk_recursive(obj: Any) -> Any` | 递归序列化对象中的 datetime 对象为字符串. | [L126](../../../../../jiuwenswarm/server/utils/stream_utils.py#L126) |
| `def _parse_typed_chunk(chunk: Any, _has_streamed_content: bool) -> dict[str, Any] \| None` | Parse OutputSchema-like chunk with type and payload attributes. | [L135](../../../../../jiuwenswarm/server/utils/stream_utils.py#L135) |
| `def parse_ask_user_question_payload(payload: Any) -> dict[str, Any]` | 源码未提供函数级文档字符串。 | [L497](../../../../../jiuwenswarm/server/utils/stream_utils.py#L497) |
| `def _parse_interaction_payload(payload: Any) -> dict[str, Any] \| None` | Convert a Core interaction payload into a frontend ask-user event. | [L511](../../../../../jiuwenswarm/server/utils/stream_utils.py#L511) |
| `def _find_interaction_payloads(obj: Any, *, _depth: int = 0, _seen: set[int] \| None = None) -> list[Any]` | Find nested ``__interaction__`` payloads inside controller output. | [L533](../../../../../jiuwenswarm/server/utils/stream_utils.py#L533) |
| `def _find_interaction_payload(obj: Any, *, _depth: int = 0, _seen: set[int] \| None = None) -> Any \| None` | Find a nested ``__interaction__`` payload inside controller output. | [L590](../../../../../jiuwenswarm/server/utils/stream_utils.py#L590) |
| `def _parse_event_typed_chunk(chunk: Any) -> dict[str, Any]` | Parse chunk with event_type attribute. | [L601](../../../../../jiuwenswarm/server/utils/stream_utils.py#L601) |
| `def _serialize_value(value: Any) -> Any` | Serialize non-JSON-native values to frontend-safe payloads. | [L627](../../../../../jiuwenswarm/server/utils/stream_utils.py#L627) |
| `def _parse_response_chunk(chunk: Any, _has_streamed_content: bool) -> dict[str, Any] \| None` | Parse AgentResponseChunk-like object. | [L641](../../../../../jiuwenswarm/server/utils/stream_utils.py#L641) |

## `jiuwenswarm/server/utils/utils.py`

[打开源码](../../../../../jiuwenswarm/server/utils/utils.py#L1)

**模块职责：** AgentServer 工具函数.

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def get_chat_id(request: AgentRequest) -> str \| None` | 获取请求的 Chat ID（平台聊天标识）。 | [L10](../../../../../jiuwenswarm/server/utils/utils.py#L10) |
| `def is_team_params(params: Mapping[str, Any] \| None) -> bool` | Return whether params indicate team mode. | [L36](../../../../../jiuwenswarm/server/utils/utils.py#L36) |

## `jiuwenswarm/server/wire_parse.py`

[打开源码](../../../../../jiuwenswarm/server/wire_parse.py#L1)

**模块职责：** 入站原始载荷 → ``AgentRequest`` 的解析段。 **本模块不发送任何东西。** 解析失败时返回一个已编码好的错误帧，由调用方经 自己的出口：``ctx.sink``或裸连接发出去。

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `logger` | `未显式标注` | [L31](../../../../../jiuwenswarm/server/wire_parse.py#L31) |
| `_SYSTEM_PROMPT_USER_HISTORY_PATTERN` | `未显式标注` | [L33](../../../../../jiuwenswarm/server/wire_parse.py#L33) |

### [`class ParseResult`](../../../../../jiuwenswarm/server/wire_parse.py#L142)

解析结果：要么拿到 ``request``，要么拿到一个待发送的 ``error_wire``。

装饰器：`@dataclass(frozen=True)`。

| 字段 | 类型 | 默认值 | 源码 |
| --- | --- | --- | --- |
| `request` | `AgentRequest \| None` | `None` | [L148](../../../../../jiuwenswarm/server/wire_parse.py#L148) |
| `error_wire` | `dict[str, Any] \| None` | `None` | [L149](../../../../../jiuwenswarm/server/wire_parse.py#L149) |
| `log_context` | `dict[str, Any] \| None` | `None` | [L151](../../../../../jiuwenswarm/server/wire_parse.py#L151) |

| 方法签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `@property def ok(self) -> bool` | 源码未提供方法级文档字符串。 | [L154](../../../../../jiuwenswarm/server/wire_parse.py#L154) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def _mask_text_for_log(value: str) -> str` | 源码未提供函数级文档字符串。 | [L38](../../../../../jiuwenswarm/server/wire_parse.py#L38) |
| `def _mask_system_prompt_for_log(system_prompt: str) -> str` | 源码未提供函数级文档字符串。 | [L42](../../../../../jiuwenswarm/server/wire_parse.py#L42) |
| `def _mask_query_for_log(data: dict[str, Any]) -> dict[str, Any]` | 源码未提供函数级文档字符串。 | [L51](../../../../../jiuwenswarm/server/wire_parse.py#L51) |
| `def _log_inbound_payload(raw: str \| bytes, data: dict[str, Any]) -> None` | Log large catalog syncs as metadata while preserving other diagnostics. | [L74](../../../../../jiuwenswarm/server/wire_parse.py#L74) |
| `def _payload_to_request(data: dict[str, Any]) -> AgentRequest` | 将 Gateway 发送的 JSON 载荷解析为 AgentRequest. | [L106](../../../../../jiuwenswarm/server/wire_parse.py#L106) |
| `def parse_inbound(raw: str \| bytes) -> ParseResult` | 把一条入站原始载荷解析成 ``AgentRequest``。 | [L158](../../../../../jiuwenswarm/server/wire_parse.py#L158) |

## `jiuwenswarm/server/wire_truncate.py`

[打开源码](../../../../../jiuwenswarm/server/wire_truncate.py#L1)

**模块职责：** Wire-payload truncation for the AgentWebSocketServer.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `_HISTORY_PAGE_SIZE` | `未显式标注` | [L30](../../../../../jiuwenswarm/server/wire_truncate.py#L30) |
| `_HISTORY_WIRE_STRING_LIMIT` | `未显式标注` | [L31](../../../../../jiuwenswarm/server/wire_truncate.py#L31) |
| `_HISTORY_WIRE_METADATA_STRING_LIMIT` | `未显式标注` | [L32](../../../../../jiuwenswarm/server/wire_truncate.py#L32) |
| `_HISTORY_WIRE_LIST_LIMIT` | `未显式标注` | [L33](../../../../../jiuwenswarm/server/wire_truncate.py#L33) |
| `_HISTORY_WIRE_DEPTH_LIMIT` | `未显式标注` | [L34](../../../../../jiuwenswarm/server/wire_truncate.py#L34) |
| `_HISTORY_WIRE_RECORD_MAX_BYTES` | `未显式标注` | [L35](../../../../../jiuwenswarm/server/wire_truncate.py#L35) |
| `_TEAM_HISTORY_DEFAULT_LIMIT` | `未显式标注` | [L36](../../../../../jiuwenswarm/server/wire_truncate.py#L36) |
| `_TEAM_HISTORY_MAX_LIMIT` | `未显式标注` | [L37](../../../../../jiuwenswarm/server/wire_truncate.py#L37) |
| `_TEAM_HISTORY_DEFAULT_MAX_BYTES` | `未显式标注` | [L38](../../../../../jiuwenswarm/server/wire_truncate.py#L38) |
| `_TEAM_HISTORY_MIN_MAX_BYTES` | `未显式标注` | [L39](../../../../../jiuwenswarm/server/wire_truncate.py#L39) |
| `_TEAM_HISTORY_MAX_MAX_BYTES` | `未显式标注` | [L40](../../../../../jiuwenswarm/server/wire_truncate.py#L40) |
| `_TEAM_HISTORY_FRAME_OVERHEAD_BYTES` | `未显式标注` | [L41](../../../../../jiuwenswarm/server/wire_truncate.py#L41) |
| `_WORKFLOW_SNAPSHOT_MAX_BYTES` | `未显式标注` | [L42](../../../../../jiuwenswarm/server/wire_truncate.py#L42) |
| `_WORKFLOW_SNAPSHOT_FRAME_OVERHEAD_BYTES` | `未显式标注` | [L43](../../../../../jiuwenswarm/server/wire_truncate.py#L43) |
| `_WORKFLOW_SNAPSHOT_MAX_WORKFLOWS` | `未显式标注` | [L44](../../../../../jiuwenswarm/server/wire_truncate.py#L44) |
| `_WORKFLOW_LIST_SUMMARY_STRING_LIMIT` | `未显式标注` | [L45](../../../../../jiuwenswarm/server/wire_truncate.py#L45) |
| `_WORKFLOW_COLLAPSED_AGENT_TEXT_LIMIT` | `未显式标注` | [L46](../../../../../jiuwenswarm/server/wire_truncate.py#L46) |
| `_WORKFLOW_WAITING_HUMAN_PROMPT_MAX_BYTES` | `未显式标注` | [L47](../../../../../jiuwenswarm/server/wire_truncate.py#L47) |
| `_TRUNCATE_SUFFIX` | `未显式标注` | [L49](../../../../../jiuwenswarm/server/wire_truncate.py#L49) |
| `_HISTORY_RESTORABLE_ASSISTANT_EVENT_TYPES` | `未显式标注` | [L51](../../../../../jiuwenswarm/server/wire_truncate.py#L51) |
| `_HISTORY_COLLAPSE_KEEP_KEYS` | `未显式标注` | [L65](../../../../../jiuwenswarm/server/wire_truncate.py#L65) |
| `_WORKFLOW_SNAPSHOT_KEEP_KEYS` | `未显式标注` | [L85](../../../../../jiuwenswarm/server/wire_truncate.py#L85) |
| `_WORKFLOW_LIST_SUMMARY_KEEP_KEYS` | `未显式标注` | [L98](../../../../../jiuwenswarm/server/wire_truncate.py#L98) |
| `__all__` | `未显式标注` | [L760](../../../../../jiuwenswarm/server/wire_truncate.py#L760) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def _json_wire_size(value: Any) -> int` | UTF-8 byte length of ``value``'s JSON wire encoding. | [L116](../../../../../jiuwenswarm/server/wire_truncate.py#L116) |
| `def _coerce_int(value: Any, *, default: int, minimum: int, maximum: int) -> int` | Coerce a request param to a clamped int (default on parse failure). | [L124](../../../../../jiuwenswarm/server/wire_truncate.py#L124) |
| `def _truncate_string_by_bytes(value: str, max_bytes: int) -> str` | Truncate ``value`` to at most ``max_bytes`` UTF-8 bytes. | [L133](../../../../../jiuwenswarm/server/wire_truncate.py#L133) |
| `def _compact_wire_metadata_value(value: Any) -> Any` | Compact a metadata scalar to a short wire-safe string. | [L147](../../../../../jiuwenswarm/server/wire_truncate.py#L147) |
| `def _sanitize_history_wire_value(value: Any, *, depth: int = 0) -> Any` | Recursively bound a value for the wire: strings, lists, depth. | [L156](../../../../../jiuwenswarm/server/wire_truncate.py#L156) |
| `def _collapse_oversized_history_record(record: dict[str, Any]) -> dict[str, Any]` | Collapse a too-large history record to a metadata stub + short content. | [L184](../../../../../jiuwenswarm/server/wire_truncate.py#L184) |
| `def _minimal_history_record_for_wire(record: dict[str, Any]) -> dict[str, Any]` | Smallest history record stub: metadata only, content replaced. | [L205](../../../../../jiuwenswarm/server/wire_truncate.py#L205) |
| `def _sanitize_history_record_for_wire(record: Any) -> dict[str, Any]` | Sanitize one history record, collapsing if it exceeds the per-record budget. | [L217](../../../../../jiuwenswarm/server/wire_truncate.py#L217) |
| `def _select_history_record_page(records: list[dict[str, Any]], *, cursor: int, limit: int, max_bytes: int, session_id: str) -> tuple[list[dict[str, Any]], int]` | Select a byte-bounded page of history records from ``cursor``. | [L229](../../../../../jiuwenswarm/server/wire_truncate.py#L229) |
| `def _is_waiting_human_agent(agent: dict[str, Any]) -> bool` | 源码未提供函数级文档字符串。 | [L292](../../../../../jiuwenswarm/server/wire_truncate.py#L292) |
| `def _extract_waiting_human_prompts(workflow: dict[str, Any]) -> dict[str, str]` | Pull every waiting-human node's prompt, keyed by agent id, before shrink. | [L296](../../../../../jiuwenswarm/server/wire_truncate.py#L296) |
| `def _restore_waiting_human_prompts(item: dict[str, Any], prompts: dict[str, str]) -> None` | Re-attach preserved human prompts onto a shrunk item's waiting nodes. | [L320](../../../../../jiuwenswarm/server/wire_truncate.py#L320) |
| `def _workflow_agent_for_collapse(agent: dict[str, Any]) -> dict[str, Any]` | Collapse one agent: keep identity + short text fields, bigger human_prompt. | [L341](../../../../../jiuwenswarm/server/wire_truncate.py#L341) |
| `def _collapse_oversized_workflow_snapshot_item(item: dict[str, Any]) -> dict[str, Any]` | Collapse a too-large workflow item: keep structure, truncate large text. | [L385](../../../../../jiuwenswarm/server/wire_truncate.py#L385) |
| `def _minimal_workflow_snapshot_item_for_wire(item: dict[str, Any]) -> dict[str, Any]` | Bare workflow item: metadata only, summary replaced, no phases. | [L434](../../../../../jiuwenswarm/server/wire_truncate.py#L434) |
| `def _minimal_workflow_detail_preserving_waiting_human(item: dict[str, Any]) -> dict[str, Any]` | Minimal item that still carries its waiting-human nodes (HITL carve-out). | [L446](../../../../../jiuwenswarm/server/wire_truncate.py#L446) |
| `def _sanitize_workflow_snapshot_item_for_wire(item: Any) -> dict[str, Any]` | Sanitize one workflow item, collapsing if it exceeds the per-record budget. | [L482](../../../../../jiuwenswarm/server/wire_truncate.py#L482) |
| `def _fit_workflow_detail_to_budget(item: dict[str, Any], *, budget: int, preserved_prompts: dict[str, str]) -> dict[str, Any]` | Shrink a workflow detail item until it fits ``budget`` bytes. | [L494](../../../../../jiuwenswarm/server/wire_truncate.py#L494) |
| `def _workflow_list_summary_phase(phase: dict[str, Any]) -> dict[str, Any]` | Phase skeleton for list — counts and status only, no agent bodies. | [L532](../../../../../jiuwenswarm/server/wire_truncate.py#L532) |
| `def _workflow_list_summary_item(item: dict[str, Any]) -> dict[str, Any]` | Compact workflow row for ``command.workflows`` list — omits large text fields. | [L544](../../../../../jiuwenswarm/server/wire_truncate.py#L544) |
| `def _minimal_workflow_list_item(item: dict[str, Any]) -> dict[str, Any]` | Smallest list row when the full summary still exceeds the wire budget. | [L570](../../../../../jiuwenswarm/server/wire_truncate.py#L570) |
| `def _fit_workflow_list_item_for_budget(item: dict[str, Any], budget: int) -> dict[str, Any]` | Shrink a list row until it fits the remaining byte budget. | [L582](../../../../../jiuwenswarm/server/wire_truncate.py#L582) |
| `def _build_workflow_list_payload(workflows: Any, *, session_id: str) -> dict[str, Any]` | Return lightweight workflow summaries — every run listed, detail via ``action=get``. | [L609](../../../../../jiuwenswarm/server/wire_truncate.py#L609) |
| `def _build_workflow_detail_payload(workflow: dict[str, Any], *, session_id: str) -> dict[str, Any]` | Return one workflow with full detail (subject to single-record sanitize/collapse). | [L648](../../../../../jiuwenswarm/server/wire_truncate.py#L648) |
| `def _find_workflow_agent(workflow: dict[str, Any], *, agent_id: str \| None = None, correlation_id: str \| None = None) -> dict[str, Any] \| None` | Locate one agent node across a workflow's phases by id / correlation_id. | [L687](../../../../../jiuwenswarm/server/wire_truncate.py#L687) |
| `def _build_workflow_human_prompt_payload(workflow: dict[str, Any], *, session_id: str, agent_id: str \| None = None, correlation_id: str \| None = None) -> dict[str, Any]` | Build the ``workflow_human_prompt`` payload for ``action=get_human_prompt``. | [L713](../../../../../jiuwenswarm/server/wire_truncate.py#L713) |
| `def _build_workflow_snapshot_payload(workflows: Any, *, session_id: str) -> dict[str, Any]` | Backward-compatible alias — defaults to lightweight list summaries. | [L755](../../../../../jiuwenswarm/server/wire_truncate.py#L755) |

## `jiuwenswarm/server/ws_send.py`

[打开源码](../../../../../jiuwenswarm/server/ws_send.py#L1)

**模块职责：** Bounded WebSocket wire sending for AgentServer responses.

**模块状态与常量**

| 名称 | 类型 | 源码 |
| --- | --- | --- |
| `logger` | `未显式标注` | [L19](../../../../../jiuwenswarm/server/ws_send.py#L19) |
| `_ROUTING_KEYS` | `未显式标注` | [L21](../../../../../jiuwenswarm/server/ws_send.py#L21) |

### 模块级函数

| 签名 | 语义摘要 | 源码 |
| --- | --- | --- |
| `def _oversized_payload(actual_bytes: int) -> dict[str, Any]` | 源码未提供函数级文档字符串。 | [L29](../../../../../jiuwenswarm/server/ws_send.py#L29) |
| `def _build_oversized_fallback(wire: dict[str, Any], actual_bytes: int) -> dict[str, Any]` | 源码未提供函数级文档字符串。 | [L38](../../../../../jiuwenswarm/server/ws_send.py#L38) |
| `def enforce_send_budget(wire: dict[str, Any]) -> tuple[str, bool]` | 把一帧wire序列化，并施加发送预算 | [L96](../../../../../jiuwenswarm/server/ws_send.py#L96) |
| `async def send_wire_payload(ws: Any, wire: dict[str, Any]) -> bool` | Send one bounded wire payload, replacing oversized data with an error. | [L132](../../../../../jiuwenswarm/server/ws_send.py#L132) |
