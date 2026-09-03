# Server Runtime、Session 与 Agent Adapter 模块设计说明书

> 本分册来自 149 个 `server/runtime/**/*.py` 文件的逐文件源码取证。全部类、函数、方法、字段与准确签名见[Runtime Core Python API](../interfaces/03-runtime-core-api.md)和[Skill Runtime Python API](../interfaces/04-skill-runtime-api.md)。

## 1. 结论摘要

`server/runtime` 不是单一“运行时对象”，而是一组按不同寿命和隔离维度分层的状态所有者：

1. `AgentWebSocketServer` 是进程/传输层宿主，直接持有单租户 `_agent_manager`，企业请求则通过进程单例 `TenantAgentPool` 路由；它还持有连接任务、stateless fallback agent、scheduler 与推送回调（[`jiuwenswarm/server/agent_ws_server.py:325`](../../../../../jiuwenswarm/server/agent_ws_server.py#L325)）。
2. `TenantAgentPool` 按 `(agent_id, service_id, workspace_key)` 缓存 `AgentManager`，配置目录由 `workspace_key` 决定，配置/env 的真实命名空间仍由请求侧 `(service_id, agent_id)` 决定（[`tenant_agent_pool.py:63`](../../../../../jiuwenswarm/server/runtime/tenant_agent_pool.py#L63)）。
3. `AgentManager` 按 `channel_id -> cache_key(mode, sub_mode, project_dir) -> JiuWenSwarm` 两级缓存根 facade；它拥有租户级唯一 `SkillManager`、创建锁、reload 锁、借用者/pin、预热池和最近配置快照（[`agent_manager.py:122`](../../../../../jiuwenswarm/server/runtime/agent_manager.py#L122)）。
4. `JiuWenSwarm` 是 SDK 无关 facade：请求协议、历史、A2UI、普通 Skill RPC、插件、Team/interrupt 分流在这里收口；真正运行由 `AgentAdapter` 实现（[`agent_adapter/interface.py:1027`](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface.py#L1027)、[`agent_adapter/agent_adapters.py:26`](../../../../../jiuwenswarm/server/runtime/agent_adapter/agent_adapters.py#L26)）。
5. `JiuWenSwarmDeepAdapter` 持有根 DeepAgent、session-scoped adapter 缓存、session 锁/活跃集合、工具/rail/checkpointer/浏览器/A2X 等资源；`SessionManager` 则只拥有同一 session 的排队、当前 Task、processor、Future 和关闭代际（[`interface_deep.py:1989`](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L1989)、[`session_manager.py:23`](../../../../../jiuwenswarm/server/runtime/session/session_manager.py#L23)）。
6. `RuntimeScopeKey(service_id, agent_id, workspace_key, session_id)` 是 process-level pool 的统一隔离键；前三维隔离租户资源，第四维隔离会话资源（[`runtime_scope.py:21`](../../../../../jiuwenswarm/server/runtime/runtime_scope.py#L21)）。

因此，完整所有权可概括为：

```text
AgentWebSocketServer / handler
  ├─ single tenant: AgentManager
  └─ enterprise: TenantAgentPool[(agent_id, service_id, workspace_key)]
       └─ AgentManager
            ├─ SkillManager（每租户 workspace 一个）
            ├─ AgentWarmPool（每 manager 一个）
            └─ agents[channel][mode|sub_mode|project_dir] -> JiuWenSwarm
                 └─ AgentAdapter（通常 DeepAdapter）
                      ├─ root DeepAgent + rails/tools/checkpointer
                      ├─ session adapter cache / locks / active state
                      └─ SessionManager[session_id] -> queue/processor/current task
```

## 2. 创建、复用与清理顺序

### 2.1 冷启动与租户创建

- WebSocket server 构造时创建默认 `AgentManager`，但 agent 本身延迟创建；企业路径通过 `TenantAgentPool.get_instance()` 取得单例（[`agent_ws_server.py:325`](../../../../../jiuwenswarm/server/agent_ws_server.py#L325)、[`tenant_agent_pool.py:94`](../../../../../jiuwenswarm/server/runtime/tenant_agent_pool.py#L94)）。
- `TenantAgentPool._ensure_agent_manager()` 先规范化三元 cache key，读取 `TenantCatalogRegistry` 的冷启动配置；若 catalog 无配置，则只读该 `(service_id, agent_id)` 的 env tip bag。之后在每 key 的 `asyncio.Lock` 内二次查缓存，创建 tenant workspace 和 `AgentManager`，最后写入 `AsyncLRUCache`（[`tenant_agent_pool.py:446`](../../../../../jiuwenswarm/server/runtime/tenant_agent_pool.py#L446)）。默认缓存不限制容量/TTL，避免长时间 HITL 被误淘汰（[`tenant_agent_pool.py:79`](../../../../../jiuwenswarm/server/runtime/tenant_agent_pool.py#L79)）。
- `TenantCatalogRegistry` 是线程锁保护的进程内 catalog，保存 `TenantAgentSpec` 的 config/env/runtime/revision；`sync_agents_configs` 先校验并物化 payload，再 upsert catalog，因此“先同步、后首次请求”也能冷创建正确 manager（[`tenant_catalog_registry.py:23`](../../../../../jiuwenswarm/server/runtime/tenant_catalog_registry.py#L23)、[`sync_agents_configs.py:293`](../../../../../jiuwenswarm/server/runtime/sync_agents_configs.py#L293)）。
- `AgentManager._get_or_create_skill_manager()` 在独立锁内创建每 tenant workspace 唯一的 `SkillManager`；企业版 workspace 未解析时直接抛 `SkillWorkspaceUnavailable`，不回退到全局路径，防止越租户（[`agent_manager.py:237`](../../../../../jiuwenswarm/server/runtime/agent_manager.py#L237)）。

### 2.2 根 agent 创建与复用

- `AgentManager.get_agent()` 将 channel、mode、sub_mode、project_dir 规范化为稳定 cache key；创建受 `(channel, cache_key)` 弱引用锁串行化。命中后返回同一 `JiuWenSwarm` 并登记当前 task 为 borrower；未命中调用 `_create_agent()`（[`agent_manager.py:225`](../../../../../jiuwenswarm/server/runtime/agent_manager.py#L225)）。
- `_create_agent()` 绑定当前 tenant env overlay，构造 `JiuWenSwarm(skill_manager=tenant singleton)`，写入 tenant/workspace 属性，调用 facade `create_instance()` 选择并构造 adapter，成功后才发布到两级缓存并保存重建参数；finally 恢复 env ContextVar（[`agent_manager.py:526`](../../../../../jiuwenswarm/server/runtime/agent_manager.py#L526)）。
- cache key 包含 project_dir，避免 code 模式不同工程共用根运行时；channel 是第一层，mode/sub_mode/project 是第二层（[`agent_manager.py:66`](../../../../../jiuwenswarm/server/runtime/agent_manager.py#L66)）。
- borrower 解决“根 agent 已返回但 session processor/子 adapter 尚未建立”的竞态；warm pool 则用引用计数 pin 表示持久后台所有者。TUI 根仅在无 borrower、无 pin、`has_session_runtime()==False` 时退休；退休前先从缓存 detach，cleanup 失败则恢复原缓存（[`agent_manager.py:280`](../../../../../jiuwenswarm/server/runtime/agent_manager.py#L280)）。

### 2.3 session 创建、准备和请求执行

- `create_session()` 可接收显式 session_id；无显式 id 时生成 channel 前缀 UUID。预热命中时 `claim_prewarmed_session()` 返回预制 session_id，调用方再 `wait_for_session_prewarm()` 等待其准备完成（[`agent_manager.py:752`](../../../../../jiuwenswarm/server/runtime/agent_manager.py#L752)）。
- facade `prepare_session()` 负责创建/配置具体 adapter session；DeepAdapter 的 `_get_or_create_session_adapter()` 以 session key 和锁保证同一会话只生成一个 session-scoped adapter，并在 root reload 后按 stale 标记惰性刷新（[`interface.py:1385`](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface.py#L1385)、[`interface_deep.py:2301`](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L2301)）。
- 非流式调用进入 `SessionManager.submit_and_wait()`；同一 session 的任务进入 `PriorityQueue`，priority 每次减一，故后入队任务优先（LIFO），不同 session 各有 processor 可并行。提交方的 `contextvars.copy_context()` 被显式传给实际 task，避免 processor 的旧 cwd/workspace 上下文泄漏（[`session_manager.py:233`](../../../../../jiuwenswarm/server/runtime/session/session_manager.py#L233)）。
- 流式任务部分由 adapter/外层 server 直接管理，`SessionManager.observe_external_task()` 只观测 telemetry 而不接管所有权；WebSocket server 的 `_session_stream_tasks` 仅用于连接/interrupt 清理，不决定响应所有权（[`session_manager.py:450`](../../../../../jiuwenswarm/server/runtime/session/session_manager.py#L450)、[`agent_ws_server.py:328`](../../../../../jiuwenswarm/server/agent_ws_server.py#L328)）。
- heartbeat/cron session id 永不复用；`submit_and_wait()` 完成后投递高正优先级 sentinel，使 processor 在所有已排任务之后退出，避免永久阻塞泄漏（[`session_manager.py:55`](../../../../../jiuwenswarm/server/runtime/session/session_manager.py#L55)）。

### 2.4 warm pool

- `AgentWarmPool` 的 key 是 `(channel_id, project_id, normalized project_dir, work_mode, is_swarm)`；swarm 和显式排除 channel 不预热。revision 包含 boot id、序列号及 config/env fingerprint（[`agent_warm_pool.py:84`](../../../../../jiuwenswarm/server/runtime/agent_warm_pool.py#L84)）。
- `sync()` 在线程中发现 project/metadata，再在池锁内将目标集合与不可变 revision 对齐：取消旧指纹 warming task、异步 dispose stale slot、为缺口排队。失败表在配置变化时清空，同配置只保留仍属目标的失败（[`agent_warm_pool.py:305`](../../../../../jiuwenswarm/server/runtime/agent_warm_pool.py#L305)）。
- `_pump_background_locked()` 同时受后台并发、ready slot 上限和前台计数限制；真实聊天 `begin_foreground()` 取消投机任务并让出事件循环，`end_foreground()` 在最后一个前台退出后延迟恢复后台泵（[`agent_warm_pool.py:430`](../../../../../jiuwenswarm/server/runtime/agent_warm_pool.py#L430)）。
- `_prepare()` 在初始化总锁内复用 manager 根 agent，并为最终 session_id 执行 `agent.prepare_session()`；成功 slot 会 pin 根 agent。claim 命中 ready slot 直接取走；若同 key 正 warming，则把原任务“提升”为真实会话，避免并发构造第二个 DeepAgent；都没有才建立 foreground prepare（[`agent_warm_pool.py:502`](../../../../../jiuwenswarm/server/runtime/agent_warm_pool.py#L502)）。
- 等待完成后释放 claim pin；异常/取消统一 `cleanup_session_runtime()`、unpin、清 marker。marker 位于 sessions `.prewarm`，启动时清理旧 boot 残留（[`agent_warm_pool.py:220`](../../../../../jiuwenswarm/server/runtime/agent_warm_pool.py#L220)）。

### 2.5 清理顺序

1. 单 session 清理从 [`server/handlers/chat.py`](../../../../../jiuwenswarm/server/handlers/chat.py) 调 `AgentManager.cleanup_session_runtime()`。
2. manager 先找所有相关 facade，调用其 `cleanup_session_runtime(session_id)`；即使内部抛错也继续做 `has_session_runtime()` 后置核验。若 TUI 根已空闲，则进入 borrower/pin 安全退休；后置仍有状态时聚合为失败而非假报成功（[`agent_manager.py:652`](../../../../../jiuwenswarm/server/runtime/agent_manager.py#L652)）。
3. facade 转给 adapter；DeepAdapter 取消 session agent tasks、清 A2X/runtime context、关闭 session adapter 与 SessionManager 状态（[`interface.py:3737`](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface.py#L3737)、[`interface_deep.py:2490`](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L2490)）。
4. manager 全量 `cleanup()` 先 `warm_pool.close()`，再取消 retirement tasks、逐根 agent cleanup，最后清缓存/锁/borrower/pin（[`agent_manager.py:1693`](../../../../../jiuwenswarm/server/runtime/agent_manager.py#L1693)）。
5. tenant eviction 先 manager cleanup，再删除 LRU entry/locks，并 best-effort 清 `RailManagerPool`、`DeepResearchTaskManagerPool`、`AgentCronRegistry`；tenant pool shutdown 遍历所有 manager 后清 cache（[`tenant_agent_pool.py:183`](../../../../../jiuwenswarm/server/runtime/tenant_agent_pool.py#L183)）。
6. WebSocket 断开先取消连接 task，再要求 manager 取消在途 session/DeepAgent 工作、停止 scheduler、取消 team streams，最后等待连接 task；这是“先生产者、后等待宿主任务”的顺序（[`agent_ws_server.py:915`](../../../../../jiuwenswarm/server/agent_ws_server.py#L915)）。

## 3. Session、Project 与运行时持久化

- [`project_store.py`](../../../../../jiuwenswarm/server/runtime/session/project_store.py) 以 agent root 下 `projects.json` 为事实源；进程锁加跨进程文件锁保护 read-modify-write，临时文件 + `os.replace` + 文件/目录 fsync 提供原子落盘。它处理创建/恢复/隐藏/重命名/置顶、按 dir+work_mode 解析，以及 cron project binding；目录名和模式均在入口规范化（[`project_store.py:92`](../../../../../jiuwenswarm/server/runtime/session/project_store.py#L92)）。
- [`session_metadata.py`](../../../../../jiuwenswarm/server/runtime/session/session_metadata.py) 每 session 写 `metadata.json`，带内存 cache、后台写队列和 pin 字段合并；同步路径用于必须立即可见的更新。它也保存 team template snapshot、delivery context，并提供全量枚举和旧 work_mode 迁移（[`session_metadata.py:152`](../../../../../jiuwenswarm/server/runtime/session/session_metadata.py#L152)）。
- [`session_history.py`](../../../../../jiuwenswarm/server/runtime/session/session_history.py) 支持 JSONL 与 legacy JSON array 读取；写入先在内存按 request/event 合并，再由 daemon writer/flush thread 批量追加。队列满或缓冲逻辑异常时降级同步直写；读取遇截断窗口会重试。`flush_session_history()` 会阻塞等待指定 session 的写队列清空，是显式 durability barrier；`shutdown()` 只等待后台队列至多 5 秒，并捕获、记录末次落盘异常，属于进程退出时的 best-effort 收尾，不能承诺完全排空（[`session_history.py:1008`](../../../../../jiuwenswarm/server/runtime/session/session_history.py#L1008)）。
- [`permission_response_ledger.py`](../../../../../jiuwenswarm/server/runtime/session/permission_response_ledger.py) 按 `(session_id, tool_call_id)` 记录已消费审批响应，避免断线/重放重复应用；它是内存 ledger，不是长期审计库。
- [`git_diff_status.py`](../../../../../jiuwenswarm/server/runtime/session/git_diff_status.py) 负责生成 summary/files/detail；[`git_diff_watcher.py`](../../../../../jiuwenswarm/server/runtime/session/git_diff_watcher.py) 按 project 聚合计算、按 watch 保存三类 fingerprint，创建与初始快照播种原子化，推送连续失败达阈值回收孤儿 watcher（[`git_diff_watcher.py:187`](../../../../../jiuwenswarm/server/runtime/session/git_diff_watcher.py#L187)）。
- KV cache 生命周期只在配置启用、provider 为 Ascend 且模型声明支持时执行；prefetch/offload/evict 都有 timeout/result 包装，后台 dispatch 表可统一取消。产品 hook 在 session 切换时根据历史存在性和 team/plan 模式发信号，失败通常记录并继续（[`kv_cache_affinity_lifecycle.py:46`](../../../../../jiuwenswarm/server/runtime/session/kv_cache_affinity_lifecycle.py#L46)、[`kv_cache_product_hooks.py:27`](../../../../../jiuwenswarm/server/runtime/session/kv_cache_product_hooks.py#L27)）。

## 4. Agent Adapter、A2UI、Debug Trace 与企业配置

### 4.1 Adapter 与协议转换

- `AgentAdapter` 定义统一协议：`create_instance`、`reload_agent_config`、非流式/流式消息、interrupt/user answer/swarmflow reply/heartbeat、工作状态；`resolve_sdk_choice()` 和 `create_adapter()` 决定具体实现（[`agent_adapters.py:26`](../../../../../jiuwenswarm/server/runtime/agent_adapter/agent_adapters.py#L26)）。
- `JiuWenSwarm` 在 facade 层把 E2A `AgentRequest` 转成 adapter inputs：解析 mode/sub_mode、恢复 session metadata 的 project/mode、绑定 tenant/workspace ContextVar、构造用户 prompt（文件/图片/A2UI client event/系统提示），再把 adapter 输出标准化为 `AgentResponse`/`AgentResponseChunk`。Skill/plugin/control RPC 在此提前短路，不进入模型（[`interface.py:905`](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface.py#L905)）。
- 流式输出会规范化嵌套 chunk、去重 full-body delta、处理 A2UI 探针和 completion 顺序；Team、interrupt、permission、SkillTurbo HITL 都在 facade/DeepAdapter 两层转换成统一事件。
- `JiuwenSwarmCodeAdapter` 构造 code 模式 rails、filesystem/LSP/worktree/coding memory、subagent 和工具卡；`JiuWenSwarmDeepAdapter` 负责 DeepAgent 的模型、checkpointer、MCP、sandbox、permission、skill、task、memory、A2A/A2X、browser 与 multimodal 等完整装配（[`interface_code.py:360`](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_code.py#L360)、[`interface_deep.py:1989`](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L1989)）。
- reload 是可延迟协议：adapter 工作中时保存 pending reload，空闲后 `apply_pending_reload_if_idle()`；manager reload lock 串行化配置变化，并基于 fingerprint 跳过同拓扑同配置重放。模型连接只关闭已删除或身份变化的 client（[`agent_manager.py:1075`](../../../../../jiuwenswarm/server/runtime/agent_manager.py#L1075)、[`interface_deep.py:2755`](../../../../../jiuwenswarm/server/runtime/agent_adapter/interface_deep.py#L2755)）。

### 4.2 Rail/提示注入

- code 模式按运行配置组装 filesystem、planning、ask user、confirm interrupt、LSP、memory、worktree、SkillUse、SkillEvolution、CodeAgent 等 rails；Deep 模式另外组装 runtime prompt、progressive tool、disabled tools、skill retrieval、permission、task execution、context overflow、stream event 等 rails。
- A2UI prompt 仅对 `web` channel 且 runtime `a2ui.enabled` 生效；`build_user_prompt_if_a2ui_event()` 把 `a2ui.client_event` 转为面向模型的续交互 prompt。协议 spec 缓存 v0.8 catalog/examples，并可按语言/浏览器 workflow 注入约束（[`a2ui/integration.py:31`](../../../../../jiuwenswarm/server/runtime/a2ui/integration.py#L31)、[`a2ui/protocol.py:39`](../../../../../jiuwenswarm/server/runtime/a2ui/protocol.py#L39)）。
- `SkillTurboPromptRail.before_model_call()` 注入 `skill_acceleration_exec` 使用/排除规则；executor 内部 rails 再围绕每次模型/工具调用执行 before/after/exception hook（[`skill_turbo/rails/skill_prompt_rail.py:51`](../../../../../jiuwenswarm/server/runtime/skill_turbo/rails/skill_prompt_rail.py#L51)、[`skill_turbo/executor.py:956`](../../../../../jiuwenswarm/server/runtime/skill_turbo/executor.py#L956)）。

### 4.3 A2UI 解析、流式保护和响应终结

1. parser 接受 tagged `<a2ui-json>`、raw JSON list 和 JSONL，保留 text/A2UI part 顺序；validator 先做 schema，再做 component 引用、data binding/template/image 等运行时语义校验（[`a2ui/parser.py:19`](../../../../../jiuwenswarm/server/runtime/a2ui/parser.py#L19)、[`a2ui/validator.py:14`](../../../../../jiuwenswarm/server/runtime/a2ui/validator.py#L14)）。
2. `A2UIStreamGuard` 在流中保留可能被 chunk 切开的 open tag；完整 block 只有校验通过才原样释放，否则格式化成普通文本；流结束仍未闭合也降级文本（[`a2ui/stream_guard.py:13`](../../../../../jiuwenswarm/server/runtime/a2ui/stream_guard.py#L13)）。
3. 完整响应只在发现协议 marker 时进入 finalizer。可解析 tagged block 先在线程里做 5 秒 schema fast path；超时直接格式化为文本。普通路径总限时 45 秒，最多两次 repair；repair 仍失败时可重跑一次“无 A2UI prompt”的请求（[`a2ui/runtime/response_finalization.py:28`](../../../../../jiuwenswarm/server/runtime/a2ui/runtime/response_finalization.py#L28)、[`a2ui/runtime/finalizer.py:67`](../../../../../jiuwenswarm/server/runtime/a2ui/runtime/finalizer.py#L67)）。
4. 失败策略是安全降级：配置读取失败跳过终结；fast path/总终结超时转可读文本；finalizer 未预期异常返回原 content；repair_failed 且无无-A2UI重试结果时返回格式化文本或固定失败文案。非 web 现在直接旁路，`apply_non_web_text_fallback_to_payload()` 保留兼容入口但不修改 payload（[`a2ui/integration.py:92`](../../../../../jiuwenswarm/server/runtime/a2ui/integration.py#L92)）。

### 4.4 Debug trace

- 开关为 `request /debug OR debug_trace.<agent|code>.enabled`；dump 还要求 `dump_enabled != false`，OTel 仅在 debug enabled 且 mode 配置显式 `otel_enabled` 时强开。配置读取任何异常都回退到 request-level-only（[`debug_trace/config.py:1`](../../../../../jiuwenswarm/server/runtime/debug_trace/config.py#L1)）。
- slash parser 能穿过 leading `<system-reminder>` 提取 `/debug`；路径按 mode/session 做安全片段，分别写 agent/code trace 文件（[`debug_trace/directives.py:29`](../../../../../jiuwenswarm/server/runtime/debug_trace/directives.py#L29)、[`debug_trace/paths.py:21`](../../../../../jiuwenswarm/server/runtime/debug_trace/paths.py#L21)）。
- `DebugTraceLogger` 每 run 记录 input、reasoning、tool call/result、usage、final/error；始终按 secret-like key 脱敏，另受 include/redaction/字符上限控制。写盘错误只 warning，不反向破坏主请求（[`debug_trace/stream_logger.py:72`](../../../../../jiuwenswarm/server/runtime/debug_trace/stream_logger.py#L72)）。
- 活跃 logger 同时放 ContextVar 和 `session_id -> logger` 注册表：前者覆盖同 task/派生 task，后者补偿早于请求 ContextVar 创建的 DeepAgent supervisor。TaskTool monkeypatch 与 `AgentTool` 调度将 subagent stream 降维后写入同一 trace；`include_subagent_flow=False` 时退回普通 invoke（[`debug_trace/context.py:1`](../../../../../jiuwenswarm/server/runtime/debug_trace/context.py#L1)、[`debug_trace/subagent_capture.py:30`](../../../../../jiuwenswarm/server/runtime/debug_trace/subagent_capture.py#L30)、[`debug_trace/task_tool_patch.py:40`](../../../../../jiuwenswarm/server/runtime/debug_trace/task_tool_patch.py#L40)）。

### 4.5 Enterprise config

- `routing_context_from_request()` 从标准化 web routing identity 取得 group/bot/user；loader 仅在 enterprise 生效，并要求非空 slots 与 bot_id/resource_id（[`enterprise_config/loader.py:23`](../../../../../jiuwenswarm/server/runtime/enterprise_config/loader.py#L23)）。
- 当前实际装载顺序是 `instance_agent_resource(resource_id)` → enabled `agent_template(ref_template_id)` → `normalize_template_ref()` → 过滤请求 slot → **只接受字面 template_id** → 按 slot 查询实体 → 填充 model/embedding/skill_whitelist/extension_config/permissions。任一关键行或所有目标实体缺失时 warning + `None`，不构造半有效配置。
- [`expressions.py`](../../../../../jiuwenswarm/server/runtime/enterprise_config/expressions.py) 另实现 `${user::key}`/`${group::key}` 映射、`or` 回退，以及安全 AST 子集的 `==`/`!=`/`and`/`or`；不支持的节点抛/返回 false，不执行 Python `eval`（[`enterprise_config/expressions.py:17`](../../../../../jiuwenswarm/server/runtime/enterprise_config/expressions.py#L17)）。但 [`loader.py`](../../../../../jiuwenswarm/server/runtime/enterprise_config/loader.py) 当前调用 `_literal_slot_template_id_map()`，未接入异步映射解析，这是文末疑点。
- `apply_enterprise_models_to_config()` 将生效实体投影到普通 config snapshot，不原地改调用者；缺失/非法字段按模板转换函数的默认策略跳过。Gateway DB facade 只代理官方表查询，异常向上传给 DeepAdapter，由 `_load_enterprise_config()` 记录并使用本地配置继续。

## 8. 失败、并发与一致性总览

| 区域 | 并发控制 | 持久化/一致性 | 主要失败策略 |
|---|---|---|---|
| Tenant/Agent | per-key asyncio lock、reload lock、borrower/pin | catalog + env tip；agent cache 仅内存 | 创建失败不发布；reload 聚合 applied/deferred/failed；cleanup best-effort 后置核验 |
| Session | per-session PriorityQueue/processor、代际检查 | metadata/history/project 文件 | cancel 有 timeout；关闭先 detach 映射防重连误删；oneshot sentinel 回收 |
| Project/history | threading/file lock、writer/flush thread | atomic replace/fsync；JSONL append | queue 满同步直写；截断读重试；显式 session flush 阻塞排空，shutdown 仅做最多 5 秒的 best-effort drain |
| A2UI | schema validation to_thread + wait_for | 无独立持久状态 | 5s fast path/45s 总限时、2 次 repair、无 A2UI 重试、文本降级 |
| Debug trace | ContextVar + session registry | 追加 trace 文件 | 写入/格式化失败不应反向破坏主链；按内建及配置的键名/正则做 best-effort 脱敏；写失败 warning |
| Skill | skills_dir 锁、后台 SkillNet job | skills_state.json + skill directories | staging/安全解压/checksum/HMAC；单项失败结构化；部分同步继续 |
| Skill Turbo | ContextVar、LLM semaphore、task snapshots | 独立 checkpointer key 下 artifacts/resume_ctx | HITL 不降级；节点 fallback 有次数/契约；终态失败让外层改走标准 Skill |

## 9. 已确认疑点与文档边界

1. `enterprise_config.expressions.resolve_slot_template_id_map()` 已实现 user/group 映射与 `or`，但 `loader.load_effective_enterprise_config()` 当前只调用 `_literal_slot_template_id_map()`；表达式能力在本装载主链上未接通。
2. `SkillDevService._handle_cancel()` 当前仅返回“取消请求已接收（实现待完善）”，没有取消 pipeline task；不能把它文档化为已实现取消。
3. `A2UI.apply_non_web_text_fallback_to_payload()` 当前是 no-op；旧名称会让人误以为非 Web 自动文本化，实际非 Web 直接旁路 A2UI。
4. SkillTurbo 工具层临时禁止自定义模板路径/已有 PPT 选区编辑，但 PPT page generator 内仍保留模板 pack/fill 能力；“底层有能力”不等于当前主入口可达。
5. `debug_trace.context._LOGGERS_BY_SESSION` 是无锁进程字典，依赖同一事件循环与成对 unregister；异常退出路径应持续关注残留风险。
6. 普通 Skill 安装的“dependency”只表现为 allowed_tools/rails/skilldev 依赖注入，没有通用 requirements 自动安装器；文档不应承诺自动解析第三方依赖。
