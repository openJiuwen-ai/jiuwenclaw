# 根包、公共基础与实例管理模块设计说明书

> 本文是正式归档分册。取证范围严格限定为 `jiuwenswarm/*.py`（直接子文件）、`jiuwenswarm/common/**/*.py`、`jiuwenswarm/instance_manager/**/*.py`，共 93 个 Python 源文件。行号以 2026-09-02 当前工作树为准。本文记录源码事实；范围外调用方与测试只作为交叉证据，不纳入逐文件计数。

## 一、架构边界与依赖图文字稿

### 1. 启动、工作区与补丁注入

```text
CLI / desktop entry
  -> dotenv_early.parse_dotenv_early()       # 必须早于其他 jiuwenswarm 导入；确定实例数据根与端口
  -> common.utils 路径解析/工作区初始化
  -> start_services                          # 端口组探测、实例锁、子进程编排、PID 生命周期
       -> instance_manager                   # instances.yaml / bootstrap .env / lock / status
       -> app                                # AgentServer + Gateway 双进程
       -> channels.web.app_web               # Web 进程

server.app_agentserver 启动期
  -> common.openjiuwen_logging
  -> llm_sse_patch
  -> openjiuwen_skip_tool_patch
  -> openjiuwen_streaming_tool_patch
  -> common.openjiuwen_rail_compat
  -> common.thinking.register_hook
运行期 interface_deep -> common.mcp_call_timeout_patch
```

边界：根包启动文件负责“尽早确定环境、再导入有路径副作用的模块”；补丁文件只做对第三方 `openjiuwen` 的幂等 monkey patch，不承载业务 API。`app.py` 是后端双进程编排器，[`start_services.py`](../../../../../jiuwenswarm/start_services.py) 是面向用户的总启动/实例管理器。

### 2. 公共配置、上下文与路径

```text
config.yaml(template + sparse user override)
  -> common.config
       -> common.local_env_config (service_id/agent_id tip + task ContextVar overlay)
       -> kv_cache_affinity_config
       -> common.utils (配置路径、模板路径、原子文件操作)

transport metadata/query/header
  -> request_identity / request_ext
  -> Message.metadata / AgentRequest

common.utils
  -> 单实例/多租户目录解析、工作区迁移、日志、敏感信息遮蔽、异步 LRU
```

关键约束：`get_config()` 按环境命名空间和任务 overlay 内容缓存 20 秒并返回共享引用；调用方不得修改。配置写回使用进程内非重入锁 + `portalocker` 跨进程锁 + 临时文件原子替换。`local_env_config` 把进程共享的 Track A 与租户业务密钥 Track B 分离；绑定 overlay 是“封闭视图”，未命中不再回退到活跃 tip。

### 3. E2A 与 schema

```text
ACP JSON-RPC / A2A SendMessage / Channel Message
  -> e2a.adapters / gateway_normalize
  -> E2AEnvelope (+ provenance, routing identity, legacy fallback)
  -> agent_compat -> AgentRequest
  -> AgentResponse / AgentResponseChunk
  -> E2AResponse
  -> wire_codec -> AgentServer <-> Gateway JSON wire
  -> ACP session/tool/todo updates 或 A2A stream payload
```

E2A 是传输无关的信封层，`schema` 是进程内旧模型/协议枚举层。转换失败并不丢失请求：`gateway_normalize` 将受 512000 字节上限保护的 legacy 请求放入 `normalize_failed` 信封；线编码失败也生成结构化 E2A error 并携带 legacy 投影。解码器同时识别当前 E2A、带 legacy 投影的 E2A、旧 unary/chunk 形状；完全未知形状抛 `ValueError`。

### 4. Secret / Security

```text
SecretStore (L1 facade, process singleton)
  -> SecretRegistry (L2 logical key -> env/file/db/default target)
  -> SecretTransform (L3 plaintext <-> ENC:v1 envelope / legacy custom crypto)
  -> PersistenceGateway (L4 env/file/db/default JSON adapters)
```

内建算法为 AES-256-GCM 与 RSA-OAEP 包装 DEK + AES-GCM；未配置算法或解密失败采取“保留原始存储串并告警”的兼容策略。`db` adapter 明确是未实现桩。`ws_origin` 独立提供浏览器 WebSocket Origin allowlist；校验默认关闭，只有 `JIUWENSWARM_ENABLE_ORIGIN_CHECK=1` 时才执行。开启后企业版直接放行；非企业版只有显式白名单中的 hostname 可过，无 `Origin` 的连接则要求白名单包含 `none`。

### 5. Thinking

```text
TaskTool subagent hook
  -> adapt_thinking(default|off|on, model)
  -> vendor_map(model_name -> style -> kwargs)
  -> immutable ThinkingProfile
  -> ThinkingInjectRail.before_model_call(ctx)
  -> ctx.llm_call_kwargs 注入
```

无法辨认厂商时降级为空 profile，不猜参数；显式 `off/on` 才挂 rail。kwargs 深冻结，注入时解冻为新字典，避免跨调用共享可变状态。

### 6. 升级

```text
Gateway web handler
  -> UpdaterService (锁保护的 UpdateStatus；后台线程 check/download/upgrade)
  -> VersionSource (GitHub/GitCode/PyPI)
  -> UpgradeExecutor
       -> DesktopExecutor (资产下载 + restart helper)
       -> PipExecutor (uv/pip，拒绝 editable 安装)
```

网络使用超时，版本源跳过 draft/prerelease 并做宽松版本排序。下载写入更新目录；桌面安装通过独立 helper 等待旧端口释放、启动新进程并等待端口恢复。服务状态中的 token 只返回掩码。

### 7. 实例管理

```text
instances.yaml
  -> yaml.py 严格校验 / 原子保存
  -> config.py 名称、端口组、冲突探测
  -> bootstrap.py 每实例 .env
  -> lock.py 跨平台启动锁 + PID 文件
  -> status.py 状态查询 / 停止进程
  -> start_services.py 统一编排
```

端口组算法为 `base + index * 1000`，默认实例 index 0；五类端口必须整组可用。YAML 原子保存防止半写，但源码明确承认两个不同实例的并发读改写仍可能丢更新；现有 `InstanceLock` 是每工作区锁，不是全局 YAML 事务锁。

## 二、逐文件源码记录（93/93）

下列“异常”同时包含显式异常与降级策略；“约束”包含并发、缓存、持久化或调用前置条件。

### A. 根包直接子文件（10）

#### 1. [`jiuwenswarm/__init__.py`](../../../../../jiuwenswarm/__init__.py)

- 空包标记，0 行；无顶级符号、导入、副作用、异常或状态。

#### 2. [`jiuwenswarm/app.py`](../../../../../jiuwenswarm/app.py)

- 职责：双进程启动 AgentServer 与 Gateway。模块导入时即调用 `parse_dotenv_early("jiuwenswarm-app")`（L16-17）、清理/必要时初始化工作区（L33-45）、重载实例 `.env`（L45）。
- 主要接口：`main() -> None`（L49），解析 `--dotenv/--name`，普通安装用 `python -m jiuwenswarm.server.app_agentserver/app_gateway`，冻结安装改用 desktop flags（L80-92）。
- 状态/失败：写 `JIUWENSWARM_START_CMD`；任一子进程退出即终止另一个，12 秒后 kill；Ctrl-C 返回 130；命名实例早期加载失败 `SystemExit(1)`。调用方是 console entry 或 `start_services._build_commands`。

#### 3. [`jiuwenswarm/deployment_mode.py`](../../../../../jiuwenswarm/deployment_mode.py)

- 职责：集中定义 `standalone`、`active-standby`、`distributed` 与存储/选主默认值。
- 接口：`normalize_deployment_mode(raw: object) -> str`（L32，非法回退 standalone）；`uses_gateway_redis(mode: str) -> bool`（L40）；`uses_leader_election(mode: str) -> bool`（L45）；`session_storage_backend(mode: str) -> Literal['local','redis']`（L50）；`history_storage_backend(mode: str) -> Literal['memory','mysql']`（L57）；`default_cron_enabled(mode: str) -> bool`（L72）；`channel_config_overlay_default(mode: str) -> bool`（L77）；`distributed_channel_whitelist() -> frozenset[str]`（L85，`web/tui`）。纯函数，无副作用。

#### 4. [`jiuwenswarm/dotenv_early.py`](../../../../../jiuwenswarm/dotenv_early.py)

- 职责：在任何路径敏感导入前扫描 `sys.argv` 的 `--dotenv/--name`。导入时设置 `GRPC_ENABLE_FORK_SUPPORT=0`、`GRPC_VERBOSITY=ERROR`（L48-49）。
- 接口：`load_dotenv_runtime(dotenv_path: str | Path | None, *, override: bool=True) -> bool`（L95，桌面/CLI 端口映射与 `EXTENSION_DIRS` 保留）；`parse_dotenv_early(component_name: str='jiuwenswarm') -> Path | None`（L131，`--dotenv` 优先）；`set_component_name(name: str) -> None`（L273）；`get_parsed_dotenv() -> Path | None`（L287）；`load_instance_bootstrap_by_name(name: str) -> Path | None`（L292）。
- 状态/失败：模块全局 `_parsed_dotenv/_component_name`；缺文件、非法实例、YAML/工作区失败记录 stderr 并返回 `None`。早期路径刻意复制少量实例逻辑以避免导入环；不移除 argv。

#### 5. [`jiuwenswarm/init_workspace.py`](../../../../../jiuwenswarm/init_workspace.py)

- 职责：`jiuwenswarm-init` CLI，初始化默认或命名实例工作区。
- 接口：`run_init(force: bool=False, name: Optional[str]=None) -> int`（L44）；`main() -> int`（L154）。默认实例调用 `init_user_workspace`；命名实例校验名称、拒绝已存在项、计算端口、写 `instances.yaml`、设置临时 user home 初始化、创建 bootstrap env。
- 失败：返回 0/1/130；`InstancesYamlError` 转用户友好错误；初始化取消/异常不伪装成功。持久化跨 `instances.yaml`、工作区与 `.env`，不是单一原子事务。

#### 6. [`jiuwenswarm/llm_sse_patch.py`](../../../../../jiuwenswarm/llm_sse_patch.py)

- 职责：兼容/修复 `OpenAIModelClient` 的授权头与 SSE 响应组装，并清理 GLM XML 工具标签。
- 接口：`apply_openai_auth_header_patch() -> None`（L167）；`assemble_openai_response(response: str) -> Any`（L318）；`apply_openai_sse_invoke_patch() -> None`（L400）。辅助 `_sanitize_glm_tool_xml_tags(raw: str) -> str`（L67）、`_parse_chunk(chunk_str: str) -> dict | None`（L279）。
- 状态/失败：`_PATCH_APPLIED/_AUTH_HEADER_PATCH_APPLIED` 保证幂等；动态导入/探测不同 SDK 形状，不可用时记录并跳过；流组装支持 content/reasoning/tool_calls、usage、finish reason，畸形 chunk 忽略。实际注入见 [`server/app_agentserver.py:160`](../../../../../jiuwenswarm/server/app_agentserver.py#L160)；授权头补丁还见 [`agents/harness/common/tools/deepresearch/sdk_bridge.py:297`](../../../../../jiuwenswarm/agents/harness/common/tools/deepresearch/sdk_bridge.py#L297)。

#### 7. [`jiuwenswarm/openjiuwen_log_patch.py`](../../../../../jiuwenswarm/openjiuwen_log_patch.py)

- 职责：`LOG_TO_FILE_ENABLED=false` 时改写 openjiuwen structured logging，只保留 console。
- 接口：`apply_openjiuwen_log_to_file_setting() -> None`（L14）。`_LOG_TO_FILE_PATCH_APPLIED` 幂等；依赖第三方内部符号，不兼容时 warning/return。
- 疑点：全仓除定义与生成文档外未检出运行时调用，可能是预留或死入口。

#### 8. [`jiuwenswarm/openjiuwen_skip_tool_patch.py`](../../../../../jiuwenswarm/openjiuwen_skip_tool_patch.py)

- 职责：修补 rail `_skip_tool` 短路时未生成 `ToolMessage` 的第三方行为。
- 接口：`apply_skip_tool_tool_message_patch() -> None`（L10）；用 `_SKIP_TOOL_TOOL_MESSAGE_PATCHED` 幂等，目标不存在则静默返回/兼容。实际调用 [`server/app_agentserver.py:165`](../../../../../jiuwenswarm/server/app_agentserver.py#L165)。

#### 9. [`jiuwenswarm/openjiuwen_streaming_tool_patch.py`](../../../../../jiuwenswarm/openjiuwen_streaming_tool_patch.py)

- 职责：为 ReAct 流式工具等待增加可暂停超时，防永久挂起。
- 主要符号：`_WaitTimeoutClock`（L37；`pause()` L48、`resume()` L54、`wait_unpaused(timeout: float) -> bool` L64）；`pause_streaming_tool_wait_timeout() -> None`（L109）；`resume_streaming_tool_wait_timeout() -> None`（L117）；异步上下文管理器 `streaming_tool_wait_timeout_paused()`（L126）；`apply_streaming_tool_wait_timeout_patch() -> None`（L209）。
- 约束：默认 180 秒，可由环境关闭/调整；`ContextVar` 定位当前 executor，弱引用表（不可弱引对象落强表）保存时钟；超时结果重映射为第三方预期形状。幂等注入见 [`server/app_agentserver.py:166`](../../../../../jiuwenswarm/server/app_agentserver.py#L166)。

#### 10. [`jiuwenswarm/start_services.py`](../../../../../jiuwenswarm/start_services.py)

- 职责：用户级 `jiuwenswarm-start`；支持 `all/web/app/dev` 与 `--name/--list/--status/--stop/--restart`。
- 主要接口：`InstanceCommand(name: str)`（L72；`validate_and_load()` L95、`check_workspace_exists()` L129、`check_running()` L144、`check_ports_available()` L154、`check_ports_conflicts()` L167）；`_resolve_ports_with_fallback(cmd, scan_range=10) -> int | None`（L223）；`do_stop_instance(cmd) -> int`（L380）；`_build_commands(mode, dotenv_path=None)`（L449）；`_run_processes(commands, ports) -> int`（L739）；`_run(mode: str) -> int`（L772）；`main() -> None`（L1063）。
- 状态/失败：默认实例冲突时整组上移并回写主 `.env`；命名实例回写 YAML + bootstrap env。命名启动持 `InstanceLock`，父进程写 PID；子进程继承显式端口并设置 `JIUWENSWARM_CLI_PORTS=1`。启动 readiness 探测进程早退，结束先 terminate 后 kill。返回码 0/1/2/130。

### B. `jiuwenswarm/common`（77）

#### 11. [`jiuwenswarm/common/__init__.py`](../../../../../jiuwenswarm/common/__init__.py)

- 空包标记，0 行；无符号与副作用。

#### 12. [`jiuwenswarm/common/chat_final.py`](../../../../../jiuwenswarm/common/chat_final.py)

- 职责：统一 `chat.final` 模式与 reasoning-only 空回复文案。
- 接口：`annotate_chat_final(payload: Mapping[str, Any] | None, *, final_mode: str='patch_segment') -> dict[str, Any]`（L19，复制返回）；`ensure_final_mode_inplace(payload: MutableMapping[str, Any], *, final_mode='patch_segment') -> None`（L36）；`reasoning_only_empty_reply_fallback_text(lang: str='zh') -> str`（L50）；`fill_reasoning_only_empty_final_content(*, content: str, has_visible_streamed_text: bool, has_reasoning: bool, lang: str='zh') -> str`（L61）。常量三种模式在 L14-16；无 I/O。

#### 13. [`jiuwenswarm/common/cleanup.py`](../../../../../jiuwenswarm/common/cleanup.py)

- 职责：删除超过保留期的 session 目录及孤儿 `file_ops` 日志。
- 接口：`cleanup_old_sessions() -> dict[str,int]`（L52）；`cleanup_orphan_file_ops() -> dict[str,int]`（L86）；`run_cleanup() -> None`（async，L135）；`cleanup_loop(stop_event: asyncio.Event, on_first_done: Callable[[],None] | None=None) -> None`（async，L149）；`start_background_cleanup(on_first_done=None) -> asyncio.Task`（L173）。
- 约束/失败：默认 30 天、每天循环、首轮延迟 10 分钟；路径来自 `common.utils`。单项删除失败 warning 后继续；循环取消传播；必须在运行 event loop 中启动。

#### 14. [`jiuwenswarm/common/coding_memory_paths.py`](../../../../../jiuwenswarm/common/coding_memory_paths.py)

- 职责：把项目目录归一为跨平台 coding-memory 项目名/路径。
- 接口：`resolve_coding_memory_project_name(project_dir: str | PathLike[str] | None) -> str`（L14）；`resolve_project_coding_memory_dir(*, agent_workspace_dir, project_dir) -> str`（L30）；`resolve_project_coding_memory_workspace_path(*, project_dir) -> str`（L43）。空项目用 `default`；用 `ntpath` 兼容 Windows 输入；纯函数。

#### 15. [`jiuwenswarm/common/config.py`](../../../../../jiuwenswarm/common/config.py)

- 职责：模板 + 用户稀疏 override 合并、环境替换、运行时标准化，以及几乎全部 Web 配置读写 API。
- 核心读写：`get_merged_config_dict() -> dict[str,Any]`（L63）；`resolve_env_vars(value: Any) -> Any`（L70，支持 `${VAR:-default}`/`${VAR-default}`）；`get_config()`（L270）；`clear_config_cache(service_id: str | None=None, agent_id: str | None=None) -> None`（L330）；`get_config_raw()`（L371）；`set_config(config)`（L390）；`load_yaml_round_trip(config_path: Path)`（L527）；`dump_yaml_round_trip(config_path: Path, data: Any) -> None`（L535）；`update_config(mutator, *, lock_timeout: float=10.0) -> Any`（L583）。
- 主要领域 API：heartbeat/channel/browser/evolution/permissions/memory/symphony 更新（L615-1552）；模型 `get_default_models(config=None)`（L1673）、`update_default_models_in_config(models_list)`（L1710）、`replace_teams_in_config(front_payload)`（L2041）；MCP `get_mcp_servers()`（L2115）、`upsert_mcp_server_in_config(server)`（L2127）、enable/get/remove（L2156-2189）；迁移 `migrate_config_from_template(template_path,user_config_path)->bool`（L2389）；sandbox `get_sandbox_runtime()`（L2700）、`get/update_sandbox_*`（L2783-3116）。
- 约束/失败：YAML parse 以 `(mtime_ns,size)` 缓存、失败重试 3 次；解析结果深拷贝。resolved config 按 ns/overlay 缓存 20 秒，返回共享引用。写事务锁不可重入，mutator 内再次 `update_config` 会死锁；使用 portalocker + 临时文件 `os.replace`，Windows PermissionError 最多 10 次。校验 API 多以 `ValueError/KeyError` 拒绝非法工具、规则、模型、sandbox 值。
- 源码疑点：`normalize_permissions_tool_level/get_permissions_defaults_level/build_permissions_tools_list_view` 在 L1149-1223 与 L1232-1312 重复定义，后者覆盖前者；文档应只描述运行时生效的第二组并注明重复。

#### 16. [`jiuwenswarm/common/context_keys.py`](../../../../../jiuwenswarm/common/context_keys.py)

- 职责：定义回调上下文键 `JIUWENSWARM_CHANNEL_CONTEXT_KEY='__jiuwenswarm_channel__'`（L10），L13 通过 `__all__` 导出。纯常量。

#### 17. [`jiuwenswarm/common/cron_team_completion.py`](../../../../../jiuwenswarm/common/cron_team_completion.py)

- 职责：Gateway/AgentServer 共用的 cron team round 完成状态机。
- 接口：`is_cron_leader_placeholder_text(text: str) -> bool`（L15）；`new_cron_team_round_state() -> dict[str,Any]`（L33）；`cron_team_round_has_open_tasks/has_active_members/has_result_text(state)`（L48/53/58）；`cron_team_round_should_end(state, *, chunk_complete=False) -> bool`（L117）；`apply_cron_team_round_event(state,event) -> None`（L191）。
- 并发：`_drain_cron_delegation_grace_events(..., grace_seconds=2.0)`（async，L93）在“暂时可结束”时继续消费队列以捕获迟到委派；未知事件忽略，状态原地更新。

#### 18. [`jiuwenswarm/common/debug_dump.py`](../../../../../jiuwenswarm/common/debug_dump.py)

- 职责：死锁/卡顿诊断，枚举线程栈、asyncio Task、等待中的同步原语和 Queue。
- 接口：`dump_async_state(service_name: str) -> Path | None`（L108）；`install_async_dump_handler(service_name: str) -> None`（L143）。输出至日志目录；Unix 注册 `SIGUSR1`，Windows/不支持 signal 时仅记录提示；采集/写入失败返回 `None` 而不打断服务。

#### 19. [`jiuwenswarm/common/e2a/__init__.py`](../../../../../jiuwenswarm/common/e2a/__init__.py)

- 职责：E2A 门面，重导出 adapters/constants/models/agent_compat/gateway_normalize/wire_codec 的 50 余个协议符号（`__all__` L79）。导入本模块会加载全部子模块；无自己的逻辑或状态。

#### 20. [`jiuwenswarm/common/e2a/acp/__init__.py`](../../../../../jiuwenswarm/common/e2a/acp/__init__.py)

- 职责：ACP 辅助门面；L12-19 重导出 initialize/prompt/session list/new 与 session/final/usage update 构造器。无状态。

#### 21. [`jiuwenswarm/common/e2a/acp/acp_tool_updates.py`](../../../../../jiuwenswarm/common/e2a/acp/acp_tool_updates.py)

- 职责：把通用工具调用/结果/todo 事件投影为 ACP SessionUpdate，并推断标题、kind、locations、terminal 内容。
- 主要接口：`is_reasoning_event(event_type: Any, payload: dict[str,Any]) -> bool`（L115）；`normalize_tool_name(tool_name: str) -> str`（L124）；`build_acp_tool_descriptor(tool_name, arguments, *, tool_call_id, status=None, raw_output=None, title=None, kind=None) -> dict`（L129）；`build_acp_tool_call_update(payload, cache=None) -> dict | None`（L161）；`build_acp_tool_result_update(payload, cache=None) -> dict`（L203）；`build_acp_todo_update(payload) -> dict | None`（L277）。
- 兼容：大量 alias 将 search/read/edit/delete/move/execute/fetch 归一；arguments 接受 JSON 字符串或映射，坏 JSON 退为空/legacy；缺 call id 生成稳定回退；无法形成 todo 返回 `None`，不抛协议异常。

#### 22. [`jiuwenswarm/common/e2a/acp/protocol.py`](../../../../../jiuwenswarm/common/e2a/acp/protocol.py)

- 职责：构造 ACP JSON-RPC result 数据。
- 接口：`build_acp_initialize_result() -> dict[str,Any]`（L8，含版本/能力）；`build_acp_session_new_result(session_id: str) -> dict`（L35）；`build_acp_session_list_result(session_ids: list[str]) -> dict`（L42）；`build_acp_prompt_result(*, stop_reason: str, user_message_id: str | None=None) -> dict`（L54）。纯构造器，版本来自 `common.version.__version__`。

#### 23. [`jiuwenswarm/common/e2a/acp/session_updates.py`](../../../../../jiuwenswarm/common/e2a/acp/session_updates.py)

- 职责：有状态地把 `Message + payload` 转换为 ACP message/thought/tool/todo/plan update，维护增量去重。
- 接口：`AcpSessionUpdateState(Protocol)`（L15，要求 assistant/thought message id、累积文本与 tool cache 属性）；`build_acp_session_update(msg: Message, payload: dict, state: AcpSessionUpdateState) -> dict | None`（L101）；`build_acp_final_text_update(payload,state) -> dict | None`（L159）；`build_acp_usage_update(payload) -> dict | None`（L187）。
- 约束：累积文本只发新增后缀，文本回退/重置时换 UUID；reasoning 与正文使用不同 message id；不识别事件返回 `None`。

#### 24. [`jiuwenswarm/common/e2a/adapters.py`](../../../../../jiuwenswarm/common/e2a/adapters.py)

- 职责：ACP/A2A 外部请求与 E2A 请求/响应之间的显式适配，并记录 converter/provenance。
- 接口：`envelope_from_acp_jsonrpc(method, params=None, *, jsonrpc_id=None, session_id=None, channel=None, identity_origin=IdentityOrigin.USER, converter=None, extra_provenance_details=None) -> E2AEnvelope`（L42）；`envelope_from_a2a_send_message(*, task_id, context_id, message_body, metadata=None, configuration=None, channel=None, ...) -> E2AEnvelope`（L80）；`envelope_to_acp_jsonrpc_call(envelope) -> dict`（L127）；`e2a_response_to_acp_jsonrpc_response(response) -> dict | None`（L144）；`e2a_response_to_a2a_stream_payload(response) -> dict | None`（L193）；`build_acp_tool_response_message(...) -> Any`（L229）。
- 失败/兼容：非目标 response kind 返回 `None`；保留 jsonrpc/task/context/session/correlation；ACP 实际调用见 [`channels/acp/app_acp.py:87`](../../../../../jiuwenswarm/channels/acp/app_acp.py#L87) 与 [`gateway/channel_manager/protocol/acp/acp_connect.py:1157`](../../../../../jiuwenswarm/gateway/channel_manager/protocol/acp/acp_connect.py#L1157)。

#### 25. [`jiuwenswarm/common/e2a/agent_compat.py`](../../../../../jiuwenswarm/common/e2a/agent_compat.py)

- 职责：第一阶段桥接 `E2AEnvelope -> AgentRequest`。
- 接口：`e2a_to_agent_request(env: E2AEnvelope) -> AgentRequest`（L32）。前置：不得传 `params['normalize_failed']`，若存在会拒绝/报错以防 fallback 与正规转换混用；把 method 转 `ReqMethod`，未知值留 `None`，合并 routing identity/permission context，E2A 时间戳解析失败回 0.0。

#### 26. [`jiuwenswarm/common/e2a/constants.py`](../../../../../jiuwenswarm/common/e2a/constants.py)

- 职责：协议常量；包括 source protocol（L6-8）、状态（L11-13）、response kind（L16-28）、`E2A_RESPONSE_KINDS`（L31）、A2A stream branches（L48）、ACP client/agent methods 与 notification（L56/73/86）、wire internal keys（L94-103）、ACP SessionUpdate kinds（L112）。无函数/副作用。

#### 27. [`jiuwenswarm/common/e2a/gateway_normalize.py`](../../../../../jiuwenswarm/common/e2a/gateway_normalize.py)

- 职责：Channel/Agent legacy 模型与 E2A 的主转换层。
- 接口：`message_to_legacy_agent_dict(msg: Message) -> dict`（L45）；`build_fallback_e2a(legacy: dict) -> E2AEnvelope`（L93）；`message_to_e2a(msg) -> E2AEnvelope`（L115）；`message_to_e2a_or_fallback(msg) -> E2AEnvelope`（L180）；`e2a_from_agent_fields(...) -> E2AEnvelope`（L207）；`channel_context_for_channel_reply(env) -> dict | None`（L239）；response/chunk 双向转换（L246、307、482、538）。
- 失败/兼容：正规转换异常由 `_or_fallback` 捕获并保存 legacy（最大 512000 JSON bytes，超限改为摘要）；HITL terminal body 特判；保持 `agent_ref` 仅在进程内 projection，不强塞 JSON。主调用见 [`gateway/message_handler/message_handler.py:2676`](../../../../../jiuwenswarm/gateway/message_handler/message_handler.py#L2676)。

#### 28. [`jiuwenswarm/common/e2a/models.py`](../../../../../jiuwenswarm/common/e2a/models.py)

- 职责：E2A v1.0 数据类、序列化、旧字段迁移。
- 主要类型：`IdentityOrigin(str,Enum)`（L34：system/user/agent/service）；`E2AProvenance`（L44）；`E2AFileRef`（L60）；`E2AAuth`（L71）；`E2AEnvelope`（L87，字段 L109-151，`ensure_timestamp/to_dict/from_dict` L153/158/164）；`E2AResponse`（L169，字段 L180-209，同名方法 L211/216/221）。`utc_now_iso() -> str`（L28）；`merge_params_to_acp_prompt(envelope) -> dict`（L500）。
- 兼容：`from_dict` 接受 legacy binding，把 agent/service/workspace 从旧位置迁到一等字段；枚举/数据类递归转 JSON；非法/空 timestamp 归一；缺失集合用新容器，避免共享默认值。

#### 29. [`jiuwenswarm/common/e2a/wire_codec.py`](../../../../../jiuwenswarm/common/e2a/wire_codec.py)

- 职责：AgentServer↔Gateway JSON wire 编解码，当前 E2A 与 legacy 双栈。
- 接口：`is_e2a_response_wire_dict(data) -> bool`（L80）；`parse_agent_server_wire_unary(data) -> AgentResponse`（L112）；`parse_agent_server_wire_chunk(data) -> AgentResponseChunk`（L171）；`encode_agent_response_for_wire(resp, *, response_id, sequence=0) -> dict`（L232）；`encode_agent_chunk_for_wire(chunk, *, response_id, sequence, is_stream=True) -> dict`（L281）；`encode_json_parse_error_wire(*, request_id, channel_id, message, response_id='') -> dict`（L410）。
- 失败/兼容：编码转换异常生成 `e2a.error` fallback（L335/372），保留安全化 legacy；解码接受 deprecated unary/chunk，未知形状分别在 L168/L229 抛 `ValueError`。生产调用见 [`server/ws_send.py:14`](../../../../../jiuwenswarm/server/ws_send.py#L14)、[`gateway/routing/agent_client.py:440`](../../../../../jiuwenswarm/gateway/routing/agent_client.py#L440)；矩阵测试见 [`tests/transport/test_dispatch_matrix.py:11`](../../../../../tests/transport/test_dispatch_matrix.py#L11)。

#### 30. [`jiuwenswarm/common/git_safe_directory.py`](../../../../../jiuwenswarm/common/git_safe_directory.py)

- 职责：识别 Git dubious ownership 并给安全提示。
- 接口：`is_dubious_ownership_error(result: subprocess.CompletedProcess[str]) -> bool`（L13）；`safe_directory_value(path: str) -> str`（L21）；`safe_directory_hint(path: str) -> str`（L29）。只检查 stdout/stderr marker；路径规范化为 POSIX 表示；无执行 Git 的副作用。

#### 31. [`jiuwenswarm/common/hooks_config.py`](../../../../../jiuwenswarm/common/hooks_config.py)

- 职责：`config.yaml` hooks schema、事件分层和 matcher。
- 类型：`HookType`（L15 command/prompt）；`HookEvent`（L20，L22-38）；`CommandHookConfig`（L76）；`PromptHookConfig`（L85）；`HookMatcher.matches(query: str) -> bool`（L98，逗号分隔 pattern、`*`、正则/字面兼容）；`HooksConfig.match(event: str, query: str='') -> list[dict]`（L131）、`get_event_summary()`（L143）。`load_hooks_config(config_base: dict | None=None) -> HooksConfig`（L165）。
- 失败：非法事件/结构记录 warning 后忽略；`disable_all_hooks` 短路为空。事件被划分为 agent rail 与 gateway 两套，调用方如 [`agents/swarm/providers/code_rails.py:488`](../../../../../jiuwenswarm/agents/swarm/providers/code_rails.py#L488)、[`gateway/message_handler/message_handler.py:50`](../../../../../jiuwenswarm/gateway/message_handler/message_handler.py#L50)。

#### 32. [`jiuwenswarm/common/http_proxy_config.py`](../../../../../jiuwenswarm/common/http_proxy_config.py)

- 职责：overlay-aware HTTP/HTTPS proxy、NO_PROXY、TLS verify 解析与 requests 包装。
- 接口：`read_proxy_url() -> str`（L50）；`read_no_proxy_list() -> list[str]`（L55）；`should_bypass_proxy(url: str) -> bool`（L108）；`resolve_requests_proxies(url) -> dict | None`（L120）；`resolve_httpx_proxy(url) -> str | None`（L130）；`prepare_requests_kwargs(url, kwargs=None) -> dict`（L138）；`ssl_verify_enabled(default=True) -> bool`（L159）；`resolve_requests_verify() -> bool | str`（L169）；`requests_request/get/post`（L192/209/213）。
- 兼容：优先 `local_env_config.read_env_if_set`，再真实环境；NO_PROXY 支持域后缀、IP/CIDR、`*`；verify 接受布尔语义或 CA bundle 路径。网络异常原样由 requests 抛出。

#### 33. [`jiuwenswarm/common/kv_cache_affinity_config.py`](../../../../../jiuwenswarm/common/kv_cache_affinity_config.py)

- 职责：Ascend KV cache affinity 纯规则。
- 接口：`parse_bool(value) -> bool`（L21）；default model 选择/提供商读写（L25/34/42/59）；`is_affinity_enabled(config) -> bool`（L86）；`validate_affinity_invariant(config) -> tuple[bool,list[str]]`（L95）；`normalize_affinity_request(params) -> None`（L118）。只有 provider=`AscendAffinity` 才允许 affinity；不一致返回错误列表或原地关闭请求字段，不 I/O。

#### 34. [`jiuwenswarm/common/local_env_config.py`](../../../../../jiuwenswarm/common/local_env_config.py)

- 职责：进程环境双轨隔离、每 `(service_id,agent_id)` active/staged tip、任务级 sealed overlay、密文物化与子进程导出。
- 命名空间/状态接口：`normalize_env_ns_id(...)->str`（L284，非法抛 `EnvNsIdError(ValueError)`）；`resolve_env_ns(...) -> tuple[str,str]`（L301）；active/staged/effective（L354/361/377）；`stage_env_overrides`（L414）；`promote_staged_env`（L442）；`replace_active_env`（L574）；`clear_agent_env_ns`（L606）；`apply_env_removals`（L622）。
- 上下文接口：`build_effective_env_overlay(*extra, service_id=None, agent_id=None) -> dict`（L648）；`bind/reset_agent_env_ns`（L673/679）；`bind/reset_task_env_overlay`（L683/696）；`get_task_env_overlay()`（L700）。读写接口：`set/get_os_environ`（L778/799）、`export_agent_environ`（L816）、`export_spawn_environ`（L841）、`get_local_config`（L1071）、`read_env/read_env_if_set`（L1105/1114）。
- 约束：Track B 敏感业务值不常驻 `os.environ`；overlay 绑定后 miss 即 unset。`seal_env_mapping/materialize_env_mapping`（L490/504）经扩展 crypto；失败时兼容返回原值并告警。裸业务变量摄入 baseline 后从真实环境移除（L987）。默认 headers JSON 非对象/坏 JSON 抛 `ValueError`（L1164）。全局 bags 无显式线程锁，隔离依赖 ContextVar 与调用纪律。

#### 35. [`jiuwenswarm/common/log_preview.py`](../../../../../jiuwenswarm/common/log_preview.py)

- 职责：单行、有界、可禁用的日志文本预览。
- 接口：`preview_text(value: Any, limit: int=200) -> str`（L51）。环境 `JIUWENSWARM_LOG_PREVIEW_USER_CONTENT` 为 false 值时隐藏内容；归一换行并截断；无异常外抛。

#### 36. [`jiuwenswarm/common/mcp_call_timeout_patch.py`](../../../../../jiuwenswarm/common/mcp_call_timeout_patch.py)

- 职责：给 openjiuwen MCP HTTP client 的单次调用注入 anyio timeout。
- 接口：`apply_mcp_call_timeout_patch(default_timeout: float=30.0) -> None`（L55）；`_PATCHED` 与 `_wrapped_methods` 防重复。只包装存在且可调用的方法；已有显式 timeout 尊重调用方；timeout 转第三方可识别错误。实际延迟调用见 [`server/runtime/agent_adapter/interface_deep.py:2016`](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L2016)。

#### 37. [`jiuwenswarm/common/mcp_config.py`](../../../../../jiuwenswarm/common/mcp_config.py)

- 职责：持久 MCP 配置转换、可达性预检、请求级 MCP 安全校验/连接池、OfficeClaw 工具发现缓存与 allowlist。
- 基础接口：`extract_enabled_mcp_server_entries(config_base) -> list[dict]`（L43）；`build_mcp_server_config(entry, *, server_id_scope=None) -> McpServerConfig | None`（L66）；`build_enabled_mcp_server_configs(...)`（L133）；`preflight_mcp_server_reachable(cfg, *, timeout=3.0) -> tuple[bool,str]`（async，L147）；`create_mcp_tool(config_str: str) -> McpServerConfig`（L522）。
- 请求级会话：`acquire_request_scoped_mcp_session(request_id, server_name, params, *, force_rebuild=False) -> Any`（async，L1105）；`release_request_scoped_mcp_sessions(request_id) -> None`（async，L1187）。安全边界拒绝危险 stdio 参数、非可信 cat-cafe 路径、loopback/metadata/非法 remote 主机（L298/374/485）；外层 asyncio cancellation 单独识别，不吞取消。
- OfficeClaw：`OfficeClawMcpRegistration`（L809）；allowlist publish/revoke/query/register（L1238-1323）；`bind_active_office_claw_mcp_tools`（contextmanager，L1349）；`ensure_request_scoped_office_claw_tool_allowed`（L1425）；extract/validate（L1484/1495/1524）；工具发现（L1638/1906）；`RequestScopedOfficeClawMcpTool(Tool)`（L1953，`invoke` L1978）。
- 并发：schema cache 由线程锁、generation 与按 loop inflight task 去重；每 `(request_id,server_name)` 一个 worker/queue；30 秒 call/discovery timeout；释放会取消 worker 并排空待处理 future。未绑定但并发同名工具时 fail closed，避免跨请求越权。

#### 38. [`jiuwenswarm/common/model_config_validation.py`](../../../../../jiuwenswarm/common/model_config_validation.py)

- 职责：`is_placeholder_api_base(api_base: str) -> bool`（L11），识别 example/placeholder host 与假 URL；纯校验函数，空值不误判。

#### 39. [`jiuwenswarm/common/openjiuwen_logging.py`](../../../../../jiuwenswarm/common/openjiuwen_logging.py)

- 职责：将 openjiuwen 文件日志固定到 `agent/.logs/openjiuwen`。
- 接口：`bootstrap_openjiuwen_logging() -> bool`（L37）；内部 `_pin_openjiuwen_log_path(log_root)`（L14）。成功返回是否加载 logging YAML；第三方模块/配置缺失时 best-effort False，不中断启动。调用见 [`server/app_agentserver.py:71`](../../../../../jiuwenswarm/server/app_agentserver.py#L71)、[`gateway/app_gateway.py:55`](../../../../../jiuwenswarm/gateway/app_gateway.py#L55)。

#### 40. [`jiuwenswarm/common/openjiuwen_rail_compat.py`](../../../../../jiuwenswarm/common/openjiuwen_rail_compat.py)

- 职责：旧版 SDK 不接受新 evolution rail kwargs 时，按 `inspect.signature` 过滤多余参数并包装构造器。
- 接口：`filter_unsupported_kwargs(func, kwargs) -> dict`（L16）；`install_evolution_rail_kwargs_compat() -> None`（L44）。类上哨兵幂等；`**kwargs` 构造器不改。调用见 [`server/app_agentserver.py:164`](../../../../../jiuwenswarm/server/app_agentserver.py#L164) 和 [`interface_deep.py:549`](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L549)；测试验证幂等见 [`tests/unit_tests/agentserver/test_openjiuwen_rail_compat.py:34`](../../../../../tests/unit_tests/agentserver/test_openjiuwen_rail_compat.py#L34)。

#### 41. [`jiuwenswarm/common/openrouter_attribution.py`](../../../../../jiuwenswarm/common/openrouter_attribution.py)

- 职责：为 OpenRouter model client 注入 `HTTP-Referer`/`X-Title` 归属头。
- 接口：`is_openrouter_provider(provider: Optional[str]) -> bool`（L17）；`inject_attribution_headers(mcc: dict[str,Any]) -> dict[str,Any]`（L23，原地/返回同配置）；`inject_attribution_to_config(config: dict[str,Any]) -> None`（L45，遍历模型与 react）。已有用户 header 优先，不覆盖；非 OpenRouter no-op。

#### 42. [`jiuwenswarm/common/reasoning_config.py`](../../../../../jiuwenswarm/common/reasoning_config.py)

- 职责：把 api_base/client provider/model 解析为推理厂商与等级。
- 接口：`resolve_reasoning_provider_kind(api_base: str | None) -> ReasoningProviderKind | None`（L45）；`normalize_reasoning_level(raw: Any) -> ReasoningLevel | None`（L61）；`resolve_reasoning_target(*, client_provider, api_base, model_name) -> tuple[ReasoningProviderKind,str] | None`（L79）。枚举/映射定义在文件前部；无法识别返回 `None`，不猜测。

#### 43. [`jiuwenswarm/common/reasoning_injector.py`](../../../../../jiuwenswarm/common/reasoning_injector.py)

- 职责：按 reasoning target 生成运行时模型请求参数。
- 接口：`inject_deepseek_official_payload(model_config_obj: dict, mapped_level: ReasoningEffort) -> None`（L49）；`inject_dashscope_bailian_payload(...)`（L65）；`inject_reasoning_params(*, model_client_config: dict, model_config_obj: Any) -> dict`（L79）；`build_reasoning_model_request_kwargs(*, model_client_config, model_config_obj, model_name) -> dict`（L125）。复制 extra_body/配置避免污染持久对象；未知 provider 返回原配置副本。

#### 44. [`jiuwenswarm/common/request_ext.py`](../../../../../jiuwenswarm/common/request_ext.py)

- 职责：把 Web 握手 query/header 白名单透传到 `Message.metadata['ext']`，并以 ContextVar 暴露当前请求。
- 接口：`set_forward_headers(headers: list[str] | None) -> None`（L31）；`register_forward_header(s)`（L43/55）；`build_ext_from_source(source) -> dict | None`（L74）；`set_current(ext) -> Token`（L98）；`attach_to_metadata(metadata, ext=None) -> dict | None`（L103）；`lift_from_metadata(metadata) -> Token | None`（L120）；`reset_ext(token) -> None`（L130）；`get_ext() -> dict`（L136）。名称小写、仅白名单；返回/挂接时复制，避免共享修改。

#### 45. [`jiuwenswarm/common/request_identity.py`](../../../../../jiuwenswarm/common/request_identity.py)

- 职责：从 transport metadata 多来源归一 `user_id/chat_id/channel_id/...` routing identity。
- 接口：`normalize_routing_identity(*sources) -> dict[str,str]`（L63）；`apply_routing_metadata(metadata, routing) -> dict`（L84）；`web_routing_identity(metadata) -> dict`（L117）；`merge_routing_into_params(params, metadata, *, override=True) -> dict`（L134）。过滤空/非标量，支持 legacy aliases；默认后来源只补空，`override` 控制 params 冲突；纯字典变换。

#### 46. [`jiuwenswarm/common/schema/__init__.py`](../../../../../jiuwenswarm/common/schema/__init__.py)

- 职责：schema 门面；L3-13 重导出 `AgentRequest/AgentResponse/AgentResponseChunk/PermissionContext/Message/Mode/ReqMethod/EventType`。无状态。

#### 47. [`jiuwenswarm/common/schema/agent.py`](../../../../../jiuwenswarm/common/schema/agent.py)

- 职责：进程内 Agent 请求/响应数据类。
- 类型：`PermissionContext`（L14，字段 L25-29；`scene()` L32、`owner_scope_key()` L41、`to_dict/from_dict` L45/56）；`AgentRequest`（L68，字段 L71-88）；`AgentResponse`（L92，L95-101）；`AgentResponseChunk`（L105，L108-113）。dataclass 默认容器均 factory；`agent_ref` 明确允许不可序列化进程内引用，由 wire 层隔离。

#### 48. [`jiuwenswarm/common/schema/ask_user.py`](../../../../../jiuwenswarm/common/schema/ask_user.py)

- 职责：AskUser 唯一响应契约、JSON schema、解析与可读文本。
- 类型/接口：`AskUserResponseError(ValueError)`（L16）；`AskUserAnswer`（L21，`to_dict/readable_value` L28/35）；`AskUserResponse`（L43，`to_dict` L50、`to_readable_text` L59）；`ask_user_response_schema() -> dict`（L70）；`normalize_ask_user_response(*, status, answers, original_request=None) -> AskUserResponse`（L155）；`parse_ask_user_response(value: Any) -> AskUserResponse`（L197）；`decode_user_input(value) -> Any`（L212）。
- 失败：只接受 answered/skipped 与结构化 answer；坏 JSON、缺字段、错误类型抛 `AskUserResponseError`；空选项/自定义输入按规则归一。

#### 49. [`jiuwenswarm/common/schema/chat_send.py`](../../../../../jiuwenswarm/common/schema/chat_send.py)

- 职责：`ChatSendParams(TypedDict, total=False)`（L11）静态契约，故类型层面所有字段均可缺省；语义主字段为 `content`（L24），`query` 是 deprecated 兼容字段（L27），其余含 skills/mode/attachments/files/trusted_dirs/project/workspace、交互回答、模型、request/session/source、plan/evolution/team/run/cron（L33-117）。运行时无校验/副作用。

#### 50. [`jiuwenswarm/common/schema/event_base.py`](../../../../../jiuwenswarm/common/schema/event_base.py)

- 职责：最小 hook event 基类，与 openjiuwen 新版事件命名对齐。
- 接口：`build_event_name(scope: str, event_name: str) -> str`（L15）；`parse_event_name(scoped_event: str) -> tuple[str,str]`（L19）；`HookEventBase`（L26，默认 scope，`__init_subclass__` L31、`get_event` L40）。子类定义时自动构造带 scope 名；非法分隔按实现回默认 scope。

#### 51. [`jiuwenswarm/common/schema/message.py`](../../../../../jiuwenswarm/common/schema/message.py)

- 职责：统一 Message 与请求/事件/模式枚举。
- 类型：`ReqMethod(Enum)`（L10，包含 chat/session/config/team/cron/sandbox/permission/skill 等方法）；`EventType(Enum)`（L243）；`Mode(Enum)`（L285，`from_raw(raw, default=None)` L296、`to_runtime_mode()` L321）；`Message` dataclass（L329，字段 L331-354）。
- 兼容：Mode 接受旧字符串/别名并映射运行时模式；未知值用 default 或 AGENT；`Message` 同时容纳 req/res/event 和 stream metadata，无自身验证。

#### 52. [`jiuwenswarm/common/schema/swarmflow_reply.py`](../../../../../jiuwenswarm/common/schema/swarmflow_reply.py)

- 职责：`SwarmflowReplyParams(TypedDict, total=False)`（L14）静态契约；类型层面 `session_id`、`team_name`、`run_id`、`correlation_id`、`answer` 全部可缺省（L17-30），实际 handler 再承担语义校验。无运行时逻辑。

#### 53. [`jiuwenswarm/common/secrets/__init__.py`](../../../../../jiuwenswarm/common/secrets/__init__.py)

- 职责：Secret 子系统公共入口；L3 导入并在 L5 仅导出 `SecretStore`。导入会加载默认 facade 类型，但不会创建 singleton。

#### 54. [`jiuwenswarm/common/secrets/envelope.py`](../../../../../jiuwenswarm/common/secrets/envelope.py)

- 职责：内建密文信封 `ENC:v1:<algorithm>:<wrap_b64>:<payload_b64>`。
- 接口：`parse_envelope(stored: str) -> tuple[str,str,str] | None`（L8，前缀/段数/algorithm/payload 非法即 `None`）；`build_envelope(algorithm: str, wrap_b64: str, payload_b64: str) -> str`（L21）。不做 base64/算法验证。

#### 55. [`jiuwenswarm/common/secrets/legacy.py`](../../../../../jiuwenswarm/common/secrets/legacy.py)

- 职责：`is_legacy_sensitive_key(name: str) -> bool`（L8）复用 `local_env_config.is_sensitive_env_name`，决定 legacy custom crypto 是否自动启用。纯函数。

#### 56. [`jiuwenswarm/common/secrets/persistence/__init__.py`](../../../../../jiuwenswarm/common/secrets/persistence/__init__.py)

- 职责：L4 persistence 门面；导出 `DefaultFileStorageBackend`、`EnvMediumAdapter`、`FileMediumAdapter`、`PersistenceGateway`（L3-13）。`DbMediumAdapter` 刻意未列入公共导出。

#### 57. [`jiuwenswarm/common/secrets/persistence/_dotted.py`](../../../../../jiuwenswarm/common/secrets/persistence/_dotted.py)

- 职责：结构化文件 dotted-path 操作。
- 接口：`get_dotted(data: Any, field: str) -> Any`（L8，miss 返回 `None`）；`set_dotted(data: dict, field: str, value: Any) -> None`（L17，非 dict 中间节点会被替换）；`delete_dotted(data: dict, field: str) -> None`（L29，miss no-op）。前置：field 非空，否则 `parts[-1]` 可失败；调用方 registry 已保证字段字符串但未明确禁止空白后归一为 None。

#### 58. [`jiuwenswarm/common/secrets/persistence/db.py`](../../../../../jiuwenswarm/common/secrets/persistence/db.py)

- 职责：企业 DB medium 预留桩 `DbMediumAdapter`（L8）。`read_raw(loc) -> str`（L9）、`write_raw(loc,raw) -> None`（L14）、`delete_raw(loc) -> None`（L19）全部无条件抛 `NotImplementedError`，错误含 target path。

#### 59. [`jiuwenswarm/common/secrets/persistence/default_file.py`](../../../../../jiuwenswarm/common/secrets/persistence/default_file.py)

- 职责：未在 registry 命中的逻辑 key 存储到 `secrets_store.json`。
- 类型：`DefaultFileStorageBackend(path: Path)`（L13）；`read(logical_key) -> str`（L17）、`write(logical_key,raw) -> None`（L24）、`delete(logical_key) -> None`（L32）。
- 持久化：空串等价删除；读取 OSError/坏 JSON warning 后当空库；写 `.tmp` 后 `os.replace` 原子替换，但无锁，多写者可能 last-writer-wins/丢更新。

#### 60. [`jiuwenswarm/common/secrets/persistence/env.py`](../../../../../jiuwenswarm/common/secrets/persistence/env.py)

- 职责：`.env` 单变量 medium，并同步当前 active tip 与 process baseline。
- 类型：`EnvMediumAdapter(env_path: Path)`（L15）；`read_raw`（L19）、`write_raw`（L24）、`delete_raw`（L37）。辅助 `_read_env_var`（L41）、`_persist_env_updates`（L65）。
- 失败/约束：loc.medium 非 env 抛 `ValueError`；I/O 失败只 warning；写入不是临时文件原子替换也无锁；空串写成 `KEY=` 并从 tip/baseline 删除。引用值仅做简单双引号/单引号解包。

#### 61. [`jiuwenswarm/common/secrets/persistence/file.py`](../../../../../jiuwenswarm/common/secrets/persistence/file.py)

- 职责：yaml/json/text 文件 medium，支持整个文件或 dotted field。
- 类型：`FileMediumAdapter(*, config_dir: Path, workspace_dir: Path)`（L17）；`read_raw`（L22）、`write_raw`（L41）、`delete_raw`（L61）；`_load_file(path,fmt)`（L65）、`_save_file`（L74）。
- 失败/约束：loc.medium 非 file 抛 `ValueError`；JSON/YAML parse 与 I/O 异常向上传播；field 值非字符串读时 JSON 序列化，写时始终保存 raw 字符串；直接覆写，无锁/无临时文件。

#### 62. [`jiuwenswarm/common/secrets/persistence/gateway.py`](../../../../../jiuwenswarm/common/secrets/persistence/gateway.py)

- 职责：按 `DefaultLocation/StorageLocation.medium` 路由到 default/env/file/db adapter。
- 类型：`PersistenceGateway(*, env_adapter, file_adapter, db_adapter=None, default_backend)`（L12）；`read(target)->str`（L26）、`write(target,raw)->None`（L31）、`delete(target)->None`（L37）。未知 medium 抛 `ValueError`；未传 DB adapter 自动构造未实现桩，故 DB 路径会抛 `NotImplementedError`。

#### 63. [`jiuwenswarm/common/secrets/providers/__init__.py`](../../../../../jiuwenswarm/common/secrets/providers/__init__.py)

- 职责：内建加密算法接口及实现。
- 类型：`BuiltinAlgorithm(ABC)`（L11，抽象 `encrypt(plaintext)->(wrap,payload)` L15、`decrypt` L19）；`Aes256GcmAlgorithm(master_key: bytes)`（L22，必须 32 bytes；`from_sources(...)->Aes256GcmAlgorithm` L31、encrypt/decrypt L53/61）；`DekAlgorithm(private_key_raw: bytes)`（L69；`from_private_key_b64` L76；encrypt/decrypt L80/106）。
- 密码学：AES 使用 12-byte nonce + AESGCM；DEK 模式随机 32-byte data key，用 RSA OAEP-SHA256 包装。缺 master key、错误 key 长度/base64/PEM、认证失败均抛异常；懒导入 `cryptography`，依赖缺失也向上传播。

#### 64. [`jiuwenswarm/common/secrets/registry.py`](../../../../../jiuwenswarm/common/secrets/registry.py)

- 职责：合并包内与用户 `secret_registry.yaml`，把逻辑 key 路由至存储位置。
- 类型/接口：`StorageLocation` frozen dataclass（L33：medium/path/field/format）；`DefaultLocation`（L41）；`bundled_registry_path(*, resources_dir=None)->Path`（L25）；`derive_legacy_name`（L48）；`resolve_file_path(path, *, config_dir, workspace_dir)->Path`（L54）；`SecretRegistry(... )`（L68，`resolve` L95、`reload` L101、`resolve_file_absolute` L106）；`infer_format_from_path(path)->Literal[...]`（L166）。
- 失败/安全：用户 registry 覆盖同名 bundled 项；非 mapping、非法 medium/format、缺 path、env 携 field/format、text 携 field 均抛 `ValueError`。路径支持绝对、`~`、`workspace/` 和 config-relative；这里只 resolve，未强制路径必须留在根内。

#### 65. [`jiuwenswarm/common/secrets/store.py`](../../../../../jiuwenswarm/common/secrets/store.py)

- 职责：L1 facade 与惰性进程 singleton。
- 接口：`SecretStore(*, registry, transform, gateway)`（L25）；`get_instance() -> SecretStore`（classmethod，L38）；`build_default(*, config_dir=None, workspace_dir=None) -> SecretStore`（L45）；`reset_for_tests`（L63）；`get(key)->str`（L67）、`set(key,value,*,algorithm=None)->None`（L73）、`delete(key)->None`（L81）；算法/legacy crypto 配置（L85-102）。
- 约束：singleton `_instance` 无锁，首次并发可构造多个等价实例；默认持久层为 `.env`、registry file 和 `config/secrets_store.json`。扩展 registry bridge 是 best-effort；读写/算法显式错误按下层传播。生产调用证据：[`gateway/a2a_manager/outbound/credentials.py:8`](../../../../../jiuwenswarm/gateway/a2a_manager/outbound/credentials.py#L8)。

#### 66. [`jiuwenswarm/common/secrets/transform.py`](../../../../../jiuwenswarm/common/secrets/transform.py)

- 职责：plaintext 与存储串转换，区分显式内建 envelope 和 legacy custom crypto。
- 类型：`SecretTransform()`（L19）；`register_custom_crypto`（L24）；`configure_aes256gcm`（L27）；`configure_dek`（L39）；`encode_for_store(logical_key, plaintext, *, algorithm, legacy_name) -> str`（L43）；`decode_from_store(logical_key, stored, *, legacy_name) -> str`（L67）。
- 失败/兼容：显式 algorithm 未配置抛 `ValueError`；custom encrypt 失败退明文；envelope 算法未配置或 builtin/custom decrypt 失败返回原始串并 warning，保证旧密文不被破坏但调用方可能得到密文而非明文。

#### 67. [`jiuwenswarm/common/security/__init__.py`](../../../../../jiuwenswarm/common/security/__init__.py)

- 空包标记，0 行；无导出与副作用。

#### 68. [`jiuwenswarm/common/security/base_crypto.py`](../../../../../jiuwenswarm/common/security/base_crypto.py)

- 职责：legacy 扩展 crypto 协议与进程默认 provider。
- 接口：`CryptoProvider(Protocol)`（L5，`encrypt/decrypt`）；`set_crypto_provider(provider: CryptoProvider) -> None`（L16）；`get_crypto_provider() -> Optional[CryptoProvider]`（L21）。`_default_provider` 注解为非 Optional 却初始化 `None`（L13），属于类型标注瑕疵；无锁全局状态。

#### 69. [`jiuwenswarm/common/security/ws_origin.py`](../../../../../jiuwenswarm/common/security/ws_origin.py)

- 职责：WebSocket Origin 校验及兼容 websockets 新旧 `process_request` 响应形状。
- 接口：`is_origin_check_enabled() -> bool`（L18，只有 env=`1`）；`get_allowed_origin_hosts() -> set[str]`（L23）；`is_allowed_browser_origin(origin: str | None) -> bool`（L31）；`extract_handshake_request(args) -> tuple[str,Any]`（L49）；`get_header_value`（L66）；`forbidden_origin_response(args) -> Any`（L79）。
- 行为：企业版全放行；非企业版 origin 缺失仅 allowlist 包含 `none` 才通过；仅比较 hostname，不比较 scheme/port。畸形 URL false。新 websockets 返回 `Response`，旧版返回 `(HTTPStatus,headers,body)`。

#### 70. [`jiuwenswarm/common/stage_timer.py`](../../../../../jiuwenswarm/common/stage_timer.py)

- 职责：线性热路径分段计时。
- 类型：`StageTimer()`（L22）；`mark(stage: str) -> None`（L35）；`total_ms() -> float`（L47）；`render(*, slowest_first=False) -> str`（L55）。基于 `time.perf_counter`，保存实例本地顺序；重复 stage 作为独立 mark，非线程安全但设计为单请求使用。

#### 71. [`jiuwenswarm/common/thinking/__init__.py`](../../../../../jiuwenswarm/common/thinking/__init__.py)

- 职责：thinking 公共门面；导出 `ThinkingProfile`、`normalize_thinking`、freeze/thaw/digest、`adapt_thinking`、`ThinkingInjectRail`（L7-21）。导入会加载 openjiuwen rail 基类，非纯数据入口。

#### 72. [`jiuwenswarm/common/thinking/adapter.py`](../../../../../jiuwenswarm/common/thinking/adapter.py)

- 职责：将 `default|off|on` 语义转换为冻结厂商 kwargs。
- 接口：`adapt_thinking(thinking: str | None, model: Any=None, *, model_name: str='') -> ThinkingProfile`（L38）；内部 `_resolve_model_name(model)`（L18）。
- 行为：`normalize_thinking` 判断无效输入并记录 degraded；default 不注入；off/on 根据 `vendor_map` 生成 profile；模型名缺失/未知厂商返回 degraded 空 profile。无模型对象修改。

#### 73. [`jiuwenswarm/common/thinking/rail.py`](../../../../../jiuwenswarm/common/thinking/rail.py)

- 职责：每次模型调用前把 profile kwargs 合并到 callback context。
- 类型：`ThinkingInjectRail(DeepAgentRail)`（L20）；构造 `__init__(profile, *, role_id='', agent_id='')`（L31）；`before_model_call(ctx: AgentCallbackContext) -> None`（async，L44）。
- 前置/兼容：profile 为空或 kwargs 空时 no-op；仅当 `ctx.extra` 是 dict 才生效，并以解冻后的 profile 副本覆盖 `extra['llm_call_kwargs']`；异常 warning 后跳过，不阻断 LLM 调用。

#### 74. [`jiuwenswarm/common/thinking/register_hook.py`](../../../../../jiuwenswarm/common/thinking/register_hook.py)

- 职责：向 openjiuwen `TaskTool` 注册 subagent thinking hook。
- 接口：`register_thinking_hook() -> None`（L57）；回调 `_on_subagent_thinking(subagent, *, thinking: str, model: Any=None) -> None`（L21）。只对显式 off/on 且成功适配者创建 `ThinkingInjectRail` 并挂到 subagent；第三方 hook API 不存在时兼容跳过；注册幂等由第三方/模块标志保障。实际启动调用 [`server/app_agentserver.py:187`](../../../../../jiuwenswarm/server/app_agentserver.py#L187)。

#### 75. [`jiuwenswarm/common/thinking/types.py`](../../../../../jiuwenswarm/common/thinking/types.py)

- 职责：thinking 不可变 profile 与规范化。
- 接口：`freeze_llm_call_kwargs(kwargs) -> Mapping[str,Any]`（L29）；`thaw_llm_call_kwargs(kwargs) -> dict`（L34）；`ThinkingProfile` frozen dataclass（L48，字段 L51-57，`empty(...)` L60）；`normalize_thinking(raw: Any) -> tuple[str,bool]`（L79）；`kwargs_digest(kwargs) -> str`（L106）。
- 约束：递归 mapping→`MappingProxyType`、list→tuple、set→frozenset；digest 供可观测性而不暴露完整参数。无效 semantic 降级 default 并返回标志，不抛。

#### 76. [`jiuwenswarm/common/thinking/vendor_map.py`](../../../../../jiuwenswarm/common/thinking/vendor_map.py)

- 职责：纯 model-name 厂商匹配表与 style 参数映射。
- 接口：`match_vendor_style(model_name: str) -> str | None`（L27）；`style_to_kwargs(style: str, *, enabled: bool) -> dict`（L38）。按正则匹配 DeepSeek/Qwen/OpenAI 等风格；未知 style 返回空字典，调用方据此降级。

#### 77. [`jiuwenswarm/common/tool_display.py`](../../../../../jiuwenswarm/common/tool_display.py)

- 职责：工具调用的人类可读显示名与 call-goal schema。
- 接口：`inject_call_goal_schema(parameters: Any) -> None`（L137，原地向 JSON schema 注入可选 `_call_goal`）；`extract_call_goal(arguments: Any) -> tuple[str,Any]`（L159）；`build_tool_display_name(name: str, arguments: Any) -> str`（L203）。
- 兼容：arguments 接受 mapping/JSON 字符串；坏 JSON/非对象降级空映射；对 read/write/search/exec/send 等提取路径、query、recipient，统一截断，不抛展示层异常。

#### 78. [`jiuwenswarm/common/tool_ownership.py`](../../../../../jiuwenswarm/common/tool_ownership.py)

- 职责：工具实例的 process-wide 注册、owner 限定 ID 与引用计数式生命周期。
- 接口：`mark_stateless(tools: list[Any]) -> list[Any]`（L43）；`qualify_tool_id(card: ToolCard, owner_id: str) -> str`（L65）；`ensure_tool_registered(tool: Any) -> Any`（L81）；`register_tool(tool: Any, owner_id: str | None) -> Any`（L115）；`unregister_tool(tool: Any) -> None`（L132）。
- 并发：模块 `threading.Lock` 保护弱/强登记状态；对同一实例幂等；owner 改写 card/tool id 避免跨 agent 冲突；第三方 runner register/unregister 失败有日志/回滚策略。前置为 tool 暴露可识别 card/id。

#### 79. [`jiuwenswarm/common/updater_restart_helper.py`](../../../../../jiuwenswarm/common/updater_restart_helper.py)

- 职责：桌面更新后的独立重启 helper。
- 接口：`main() -> None`（L34）；辅助 `_wait_for_port(host,port,timeout=30.0)`（L26）、`_wait_for_port_release(...,timeout=15.0)`（L30）。从命令行 JSON 读取旧 PID/端口/启动命令，等待旧进程和端口释放，后台启动新命令并等待服务端口；失败日志后退出非零。Windows 使用无窗口/background flags。

#### 80. [`jiuwenswarm/common/updater.py`](../../../../../jiuwenswarm/common/updater.py)

- 职责：更新状态机与版本源/执行器编排。
- 类型：`UpdateStatus` dataclass（L82，current/latest/state/asset/progress/error/installing/restart_command 等字段）；`UpdaterService`（L103）。公共方法 `get_status() -> dict`（L112）、`get_runtime_config() -> dict`（L119）、`check(manual: bool=False) -> dict`（L144）、`start_download() -> dict`（L169）、`start_upgrade() -> dict`（L215）。
- 并发/失败：内部锁保护状态，check/download/upgrade 以 daemon thread 执行；重复进行中的请求返回当前状态，不重复启动。`_create_version_source`（L262）按 github/gitcode/pypi；异常落 `state=error,error=...`。runtime config 的 token 掩码。Web handler 实际使用见 [`gateway/channel_manager/web/app_web_handlers.py:3175`](../../../../../jiuwenswarm/gateway/channel_manager/web/app_web_handlers.py#L3175)。

#### 81. [`jiuwenswarm/common/upgrade_executor.py`](../../../../../jiuwenswarm/common/upgrade_executor.py)

- 职责：具体安装策略。
- 类型：`UpgradeExecutor(ABC)`（L29，`install()` 抽象 L42、`upgrade()` L45）；`DesktopExecutor`（L82，`install` L93、流式 `_download_file` L123）；`PipExecutor`（L154，`install` L165、`upgrade` L330）；`create_executor(install_mode: str, config: dict, status_callback) -> UpgradeExecutor`（L418）。
- 约束/失败：下载写 updates dir 并回调进度；Desktop 校验 URL/资产后下载，升级通过 helper 重启；Pip 检测 editable 安装并拒绝，自适应 uv/pip/venv。subprocess/network/磁盘异常向上交给 UpdaterService 状态机；不做静默成功。

#### 82. [`jiuwenswarm/common/utils.py`](../../../../../jiuwenswarm/common/utils.py)

- 职责：工作区安装/迁移、路径单一来源、多租户目录、日志系统、敏感遮蔽、TCP/PID 等通用设施。
- 工作区 API：`prepare_workspace(overwrite=True, preferred_language=None, workspace_dir=None) -> CopyDiffResult`（L1210）；`init_user_workspace(...) -> Path | Literal['cancelled']`（L1509）；`set_user_home(path, initialized=False)`（L558）；`get_user_workspace_dir()`（L577）；`get_config_dir/get_workspace_dir/get_root_dir/get_agent_workspace_dir/get_env_file/get_config_file`（L1638/1654/1660/1666/2280/2290）。`CopyDiffResult`（L71）与 `TrackCopyDiff`（L78）记录新增/覆盖。
- 多租户/技能路径：`get_multi_tenant_user_workspace_dir`（L1832）、`get_tenant_agent_workspace_dir`（L1843）、shared skill 解析/合并（L1900-1972）、cron tenant scope（L2061）、evolution/session/log/tmp 路径（L2176-2273）。tenant/workspace key 必填/非法时抛 `ValueError`，路径 resolve 逻辑集中。
- 日志/运行：`mask_sensitive(text)->str`（L2454）、`setup_logger`（L2953）、`wait_for_tcp_port(..., target_state='connected')->bool`（L3107）、`wait_for_pid_exit(pid,timeout)->None`（L3160）、`update_log_levels`（L3208）、`reload_logging_levels`（async，L3288）、`fix_json_arguments(arguments)->str|dict`（L3463）。
- 并发/疑点：`SafeRotatingFileHandler` 用平台文件锁/容错 rollover；日志队列可 flush。`AsyncLRUCache` 在 L3331 与 L3541 重复定义，运行时只有后者生效；后者以 async lock、TTL、OrderedDict 管理并提供 `touch_if_same/snapshot_values_nowait`。模块级路径缓存受 `set_user_home/_resolve_paths` 状态影响。

#### 83. [`jiuwenswarm/common/version.py`](../../../../../jiuwenswarm/common/version.py)

- 职责：唯一常量 `__version__ = '0.2.3'`（L3）。无逻辑。

#### 84. [`jiuwenswarm/common/version_source.py`](../../../../../jiuwenswarm/common/version_source.py)

- 职责：从 GitHub、GitCode、PyPI 获取最新稳定版本与资产。
- 接口：`is_prerelease_version(version: str) -> bool`（L31）；`strip_prerelease_suffix`（L36）；`release_sort_key`（L53）。数据类 `ReleaseAsset`（L153）、`ReleaseInfo`（L160）。抽象 `VersionSource(name='', timeout_seconds=20)`（L169，`fetch_latest` L175、`fetch_assets` L178）；`GitHubReleasesSource`（L281）、`GitCodeReleasesSource`（L349）、`PyPIVersionSource`（L424）。
- 失败/兼容：跳过 draft/prerelease；latest endpoint 失败可遍历列表取最高版本；HTTP/URL/socket/JSON 错误转上层可记录异常。PyPI 优先 simple JSON，再 HTML；相对 asset URL resolve。排序是宽松数字/预发布解析，不等同完整 PEP 440。

#### 85. [`jiuwenswarm/common/work_mode.py`](../../../../../jiuwenswarm/common/work_mode.py)

- 职责：work/code 模式与默认 project id。
- 接口：`normalize_work_mode(raw: Any, *, default: str='work') -> str`（L25，支持 `agent` 等 legacy alias，非法回 default）；`is_default_project_id(project_id: str | None) -> bool`（L40）；`resolve_default_project_id(work_mode: str) -> str`（L47）。常量默认 Web=work、TUI=code（L14-21）；纯函数。

#### 86. [`jiuwenswarm/common/ws_diagnostics.py`](../../../../../jiuwenswarm/common/ws_diagnostics.py)

- 职责：WebSocket 异常与 peer 的 JSON-safe 诊断。
- 接口：`describe_ws_exception(exc: BaseException) -> dict[str,Any]`（L27）；`describe_ws_peer(ws: Any) -> dict[str,Any]`（L55）；`format_ws_diagnostics(*parts: Mapping | None, **fields) -> str`（L74）。兼容 close code/reason、remote/local address、enum/bytes/复杂对象；属性读取异常被压制，诊断本身不应再抛。

#### 87. [`jiuwenswarm/common/ws_limits.py`](../../../../../jiuwenswarm/common/ws_limits.py)

- 职责：共享 wire 大小常量：`AGENT_WS_MAX_MESSAGE_BYTES = 8 MiB`（L6）、`AGENT_WS_SEND_BUDGET_BYTES = 6 MiB`（L7）、`WEB_WS_MAX_MESSAGE_BYTES = 100 MiB`（L11）。无函数/状态。

### C. `jiuwenswarm/instance_manager`（6）

#### 88. [`jiuwenswarm/instance_manager/__init__.py`](../../../../../jiuwenswarm/instance_manager/__init__.py)

- 职责：向后兼容门面；从五个子模块重导出全部公共数据类、常量、名称/端口/YAML/锁/PID/状态/停止/bootstrap API（L28-99，`__all__` L101-156）。导入会加载全实例管理栈，但不立即读写 YAML。

#### 89. [`jiuwenswarm/instance_manager/bootstrap.py`](../../../../../jiuwenswarm/instance_manager/bootstrap.py)

- 职责：生成/加载实例 bootstrap `.env`。
- 接口：`create_bootstrap_env(config: InstanceConfig) -> Path`（L32）；`create_bootstrap_env_for_name(name: str, workspace: Path) -> Path`（L75）；`_create_basic_bootstrap_env(name, workspace, component_name) -> None`（L86，早期复制实现）；`load_instance_bootstrap_by_name(name: str) -> Path | None`（L140）。
- 持久化/失败：写数据根、实例名及五端口（正常路径含 HTTP）；不存在时创建目录。加载接口对非法名/未登记/工作区缺失记录错误返回 None，否则 `load_dotenv(..., override=True)`。早期 basic 路径吞 YAML 错误并用 index 1，且只写四个端口，和正式路径有轻微兼容差异。

#### 90. [`jiuwenswarm/instance_manager/config.py`](../../../../../jiuwenswarm/instance_manager/config.py)

- 职责：实例数据模型、名称校验、端口分配/探测与 fallback。
- 类型/常量：`InstancesYamlError`（L26，消息自动加 `[instances.yaml]`）；`InstanceConfig`（L58，name/workspace/ports，workspace 在 `__post_init__` resolve）；`InstanceStatus`（L84）。`BASE_PORTS` L42-48：agent_server 18092、web 19000、gateway 19001、frontend 5173、http_api 8766；`PORT_TYPES` L51。
- 接口：`validate_instance_name(name) -> Optional[str]`（L103，1-64、字母数字 `_ -`、保留名）；`compute_auto_port(port_type,index)->int`（L163）；`calculate_instance_ports(index)->dict`（L184）；`is_port_available(host,port)->bool`（L230）；`check_port_conflicts(...)->list[int]`（L276）；`collect_all_ports(exclude_name=None)->list[int]`（L304）；`find_available_ports(base_index=0,host='127.0.0.1',scan_range=10,exclude_ports=None)->Optional[tuple]`（L388）。
- 约束：bind+listen 而非 connect 探测，Windows 不设 SO_REUSEADDR，同时检查 IPv4/IPv6 localhost；env 可覆盖 base port，坏值忽略。端口计算不自行检查 >65535，最终 YAML/绑定阶段发现。collect YAML 失败 debug 后忽略。

#### 91. [`jiuwenswarm/instance_manager/lock.py`](../../../../../jiuwenswarm/instance_manager/lock.py)

- 职责：跨平台实例启动锁、PID 文件和进程存活。
- 类型/接口：`InstanceLock(config: InstanceConfig)`（L35，`acquire(timeout=5.0)->bool` L69、`release()` L87、context manager L184/189）；`write_pid_file(config,pid,started_at=None)->None`（L194）；`read_pid_file(config)->Optional[dict]`（L241）；`delete_pid_file(config)->bool`（L263）；`is_process_alive(pid)->bool`（L282）；`check_instance_running(workspace)->bool`（L320）。
- 并发/失败：Unix `fcntl.flock(LOCK_EX|LOCK_NB)`，Windows `msvcrt.locking`；轮询至 timeout。陈旧锁超时 30 秒可移除。PID JSON 先临时文件再 replace；坏/缺 PID 返回 None，删除 best-effort。进程存活 Windows 用 tasklist，POSIX `os.kill(pid,0)`；权限拒绝视为存活。

#### 92. [`jiuwenswarm/instance_manager/status.py`](../../../../../jiuwenswarm/instance_manager/status.py)

- 职责：组装实例状态、加载配置、格式化与停止进程。
- 接口：`get_instance_status(config) -> InstanceStatus`（L43）；`get_default_instance_status() -> InstanceStatus`（L82）；`list_all_instances(include_default=True) -> list[InstanceStatus]`（L199）；`format_status_line(status)->str`（L227）；`get_instance_config(name)->Optional[InstanceConfig]`（L254）；`load_all_instance_configs(path: Optional[Path]=None)->dict[str,InstanceConfig]`（L290）；`stop_process_by_pid(pid,timeout=10.0)->bool`（L338）；`stop_instance_process(config,timeout=10.0)->bool`（L392）。
- 行为/失败：PID 文件存在但进程死则删除；默认实例无 PID 时可按 gateway port 查 PID。Windows 使用 taskkill/tasklist/netstat，POSIX SIGTERM 后超时 SIGKILL。不存在视为已停止；无法终止返回 False。`load_all_instance_configs(path)` 的 path 参数允许测试/替代 YAML，校验错误传播为 `InstancesYamlError`。

#### 93. [`jiuwenswarm/instance_manager/yaml.py`](../../../../../jiuwenswarm/instance_manager/yaml.py)

- 职责：`~/.jiuwenswarm/instances.yaml` 路径、严格读取校验、原子保存和条目更新。
- 接口：`get_instances_yaml_path() -> Path`（L30）；`get_instances_dir() -> Path`（L35）；`get_instance_workspace_path(name) -> Path`（L40）；`load_instances_yaml() -> dict`（L193）；`save_instances_yaml(data) -> None`（L221）；`create_instances_yaml_template() -> Path`（L257）；`update_instances_yaml(name,workspace,ports=None)->None`（L294）；`get_instance_index(name)->int`（L329）。
- 失败/约束：读/parse/顶层/entry/workspace/ports/范围错误统一转带修复提示的 `InstancesYamlError`（内部校验 L45-190）；缺文件返回 `{'instances':{}}`。保存用同目录 tempfile + `os.replace`，异常清 tmp；没有全局文件锁，因此原子性只保证不半写，不保证并发 read-modify-write 不丢更新（源码 L224-231 明示）。声明顺序就是端口 index，重排 YAML 会改变自动端口。

## 三、关键接口语义速查

| 接口 | 参数/前置 | 返回 | 主要异常或兼容行为 |
|---|---|---|---|
| `config.get_config()` L270 | 先绑定 ns/overlay（可选）；调用方不得修改结果 | 合并、替换环境、标准化后的共享 dict | 20s TTL；YAML 错误最终抛；cache version 防写期旧结果回填 |
| `config.update_config(mutator, lock_timeout=10)` L583 | mutator 必须同步且不得嵌套调用配置写 API | mutator 返回值 | 进程锁 + portalocker；超时/写失败抛；临时文件原子替换 |
| `local_env_config.bind_task_env_overlay(overlay)` L683 | 必须保存并最终 reset token | `ContextVar.Token` | overlay 是 seal，miss 不回退；跨 task 自动隔离 |
| `gateway_normalize.message_to_e2a_or_fallback(msg)` L180 | `Message` 至少具备基本请求字段 | 始终为 `E2AEnvelope` | 正规转换失败改 `normalize_failed`；legacy JSON 上限 512000 bytes |
| `wire_codec.parse_agent_server_wire_unary(data)` L112 | dict 必须为 E2A 或支持的旧 wire | `AgentResponse` | 未识别形状 `ValueError`；支持 E2A legacy projection |
| `mcp_config.acquire_request_scoped_mcp_session(...)` L1105 | 可信且已校验的 stdio/remote params；event loop 存活 | 可 `call_tool` 的 worker/adapter | 安全校验 fail closed；外层 cancellation 传播；worker 失败排空队列 |
| `SecretStore.set(key,value,algorithm=None)` L73 | 显式 algorithm 先 configure；key 经 registry 路由 | `None` | 未配置算法 `ValueError`；下层 I/O 传播；legacy custom 加密失败可能退明文 |
| `adapt_thinking(thinking,model,model_name='')` L38 | thinking 语义，模型名可从 model 推断 | frozen `ThinkingProfile` | 未知输入/厂商降级，不抛；default 不注入 |
| `find_available_ports(...,scan_range=10)` L388 | 每个候选 index 计算完整五端口组 | `(ports_dict,index)` 或 `None` | `scan_range=0` 不扫描；同时排除已声明与实际占用端口 |
| `save_instances_yaml(data)` L221 | data 应已校验/构造 | `None` | 原子 replace；异常清临时文件；无全局事务锁 |

## 四、范围外调用方与测试证据

这些事实无法只从定义文件说明“何时/如何使用”，因此明确引用范围外路径：

- 补丁顺序：[`jiuwenswarm/server/app_agentserver.py:71`](../../../../../jiuwenswarm/server/app_agentserver.py#L71) 先引导日志，再应用 SSE/skip-tool/stream-timeout/rail compatibility，最后注册 thinking hook；MCP timeout 在 [`jiuwenswarm/server/runtime/agent_adapter/interface_deep.py:2016`](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L2016) 按运行时需要应用。
- E2A 生产链：[`jiuwenswarm/gateway/message_handler/message_handler.py:2676`](../../../../../jiuwenswarm/gateway/message_handler/message_handler.py#L2676) 将 Message 转 E2A；[`jiuwenswarm/server/ws_send.py:14`](../../../../../jiuwenswarm/server/ws_send.py#L14) 编码响应；[`jiuwenswarm/gateway/routing/agent_client.py:440`](../../../../../jiuwenswarm/gateway/routing/agent_client.py#L440) 解码 unary；[`jiuwenswarm/gateway/channel_manager/protocol/acp/acp_connect.py:347`](../../../../../jiuwenswarm/gateway/channel_manager/protocol/acp/acp_connect.py#L347) 构造 ACP update/请求信封。
- E2A 兼容矩阵：[`tests/transport/test_route_matrix.py:204`](../../../../../tests/transport/test_route_matrix.py#L204) 覆盖 agent bridge 与 wire encode；[`tests/transport/test_dispatch_matrix.py:11`](../../../../../tests/transport/test_dispatch_matrix.py#L11) 导入双解码器；[`tests/unit_tests/agentserver/test_session_prepare.py:7`](../../../../../tests/unit_tests/agentserver/test_session_prepare.py#L7) 验证 sink wire 可反解。
- SecretStore 的当前业务落点：[`jiuwenswarm/gateway/a2a_manager/outbound/credentials.py:8`](../../../../../jiuwenswarm/gateway/a2a_manager/outbound/credentials.py#L8) 默认取得 singleton，用 logical reference 保存出站凭据。
- Updater：[`jiuwenswarm/gateway/channel_manager/web/app_web_handlers.py:3175`](../../../../../jiuwenswarm/gateway/channel_manager/web/app_web_handlers.py#L3175) 将 status/check/download/upgrade 暴露为 Web RPC；install mode 测试见 [`tests/unit_tests/test_updater_install_mode.py:31`](../../../../../tests/unit_tests/test_updater_install_mode.py#L31)。
- 配置/环境是横切依赖：[`jiuwenswarm/agents/swarm/assembly.py:33`](../../../../../jiuwenswarm/agents/swarm/assembly.py#L33) 同时读取 config、MCP 与 skill path；[`jiuwenswarm/gateway/storage_assembly/setup.py:28`](../../../../../jiuwenswarm/gateway/storage_assembly/setup.py#L28) 在存储装配时清 cache/读配置；因此文档不能把 `config.py` 描述为仅 Web 设置模块。
- 实例管理实际编排：[`jiuwenswarm/start_services.py:223`](../../../../../jiuwenswarm/start_services.py#L223) 负责 fallback、stop、命名实例锁与启动；初始化入口在 [`jiuwenswarm/init_workspace.py:44`](../../../../../jiuwenswarm/init_workspace.py#L44)。

## 五、已确认疑点与文档提示

1. [`common/config.py`](../../../../../jiuwenswarm/common/config.py) 有三函数的重复定义（L1149-1223 与 L1232-1312）；Python 运行时后定义覆盖前定义。API 参考不要生成两个“都生效”的条目。
2. [`common/utils.py`](../../../../../jiuwenswarm/common/utils.py) 的 `AsyncLRUCache` 在 L3331 与 L3541 重复定义；公开名最终指向 L3541 版本，两版方法集不同。
3. `openjiuwen_log_patch.apply_openjiuwen_log_to_file_setting` 全仓未发现运行时调用；应标“可选补丁/当前未接线”，不能声称启动必定应用。
4. `instance_manager/bootstrap._create_basic_bootstrap_env` 的早期 fallback 只写四端口，正式 `create_bootstrap_env` 写五端口（含 `AGENT_HTTP_PORT`）；且早期失败固定 index 1。这是有意的低依赖降级还是遗漏，需维护者确认。
5. `save_instances_yaml` 保证文件不损坏，但不保证不同实例并发更新不丢失；每实例 workspace lock 无法串行化全局 YAML。
6. Secret file/env/default adapters 的并发保护不一致：default 使用原子 replace 但无锁，env/file 直接覆盖；不应在文档中承诺并发安全。
7. `security.base_crypto._default_provider` 声明为 `CryptoProvider` 却赋 `None`；运行时无碍，静态类型不严谨。
8. `SecretRegistry.resolve_file_path` 支持任意绝对/相对 resolve，未在该层做根目录 containment；安全边界应由 registry 配置可信性或上层部署保证。

## 六、核对方法与结果

- 文件集合：PowerShell 分别枚举根包直接 `*.py`、`common` 递归 `*.py`、`instance_manager` 递归 `*.py`，去重后 **93**；拆分为 **10 + 77 + 6**。
- AST：对 93 个文件执行 `ast.parse`，提取顶级函数/类/字段/签名/行号；全部可解析，无 syntax error。
- 逐文件覆盖：本节路径反引号与 manifest 做集合差集，目标 93 路径均有一条记录；空 `__init__.py` 已显式记录。
- 交叉检索：用 `rg -n` 检查 E2A、patch、thinking、SecretStore、UpdaterService、实例管理的生产调用方与测试调用方；代表证据列于第四节。
- 工作树保护：只新增本报告；未修改运行时代码或正式 `docs/`。
