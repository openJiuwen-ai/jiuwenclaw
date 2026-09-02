# JiuwenSwarm 项目架构深度解析

> 分析对象：`D:\hcj_data\code\jiuwenswarm`（分支 `xiaoyi_0.2.4.beta3`）
> 文档日期：2026-09-02

---

## 一、总体结论：有 Gateway 层，也有 AgentServer 层

JiuwenSwarm 是**双进程分层架构**，两层都是独立进程，通过 E2A 协议通信：

```
┌─────────────────────────────────────────────────────────────────────┐
│                          客户端层                                    │
│  浏览器 Web UI   TUI/CLI   IDE(ACP)   小艺/飞书/钉钉/…(IM)   A2A      │
└──────┬──────────────┬──────────┬──────────────┬────────────┬────────┘
       │ ws:19000/ws  │ ws:19001/tui │ ws:19001/acp │ 各 IM 协议 │ :19100/a2a
       ▼              ▼            ▼              ▼            ▼
┌─────────────────────────────────────────────────────────────────────┐
│  Gateway 层（jiuwenswarm-gateway，gateway/app_gateway.py）           │
│  渠道接入 ChannelManager · 消息路由 MessageHandler · cron · 心跳     │
└──────────────────────────────┬──────────────────────────────────────┘
                               │ E2A 协议（WS / 命名管道 / stdio）
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│  AgentServer 层（jiuwenswarm-agentserver，server/app_agentserver.py）│
│  AgentWebSocketServer · dispatch · AgentManager · JiuWenSwarm 门面   │
│  → DeepAgent（openjiuwen agent-core）· Team/Swarm · Skill · Memory   │
└─────────────────────────────────────────────────────────────────────┘
```

- **Gateway** 是"渠道网关"：负责接入各种外部渠道（Web/TUI/IM/ACP/A2A），做消息归一化、会话路由、定时任务调度、渠道配置热更。
- **AgentServer** 是"Agent 运行时"：负责 Agent 装配与执行（DeepAgent/ReAct、Swarm 团队、技能、记忆、沙箱），是唯一真正调用 LLM 的进程。

两层可以同机部署（默认），桌面形态下甚至不监听任何 TCP 端口（全部走 stdio + 命名管道）。

---

## 二、进程与入口

`pyproject.toml [project.scripts]` 声明的入口：

| 命令 | 入口 | 说明 |
|---|---|---|
| `jiuwenswarm` | `jiuwenswarm.cli.main:main` | 根 CLI，`chat` 子命令连接 Gateway 19001 `/tui` |
| `jiuwenswarm-app` | `jiuwenswarm.app:main` | 双进程编排器：Popen 拉起 agentserver + gateway |
| `jiuwenswarm-agentserver` | `jiuwenswarm.server.app_agentserver:main` | 独立启动 AgentServer |
| `jiuwenswarm-gateway` | `jiuwenswarm.gateway.app_gateway:main` | 独立启动 Gateway |
| `jiuwenswarm-web` | `jiuwenswarm.channels.web.app_web:main` | 静态前端服务 + 反向代理 |
| `jiuwenswarm-start` | `jiuwenswarm.start_services:main` | 多实例编排（all/app/web/dev 模式 + 实例管理） |
| `jiuwenswarm-init` | `jiuwenswarm.init_workspace:main` | 初始化工作空间 |
| `jiuwenswarm-desktop` | `jiuwenswarm.channels.desktop.desktop_app:main` | pywebview 桌面壳 |
| `jiuwenswarm-acp` | `...protocol.acp.acp_connect:main` | ACP 网关桥 |
| `jiuwenbox` / `jiuwenbox-server` | jiuwenbox 子项目 | 沙箱服务 |

### 默认端口（`instance_manager/config.py:42-47` `BASE_PORTS`）

| 端口 | 进程 | 用途 |
|---|---|---|
| **18092** | AgentServer | E2A WebSocket 服务（`AGENT_SERVER_PORT`，`AGENT_PORT` 为旧别名） |
| **19000** | Gateway | WebChannel（浏览器端 WS，`WEB_PORT`，路径 `/ws`） |
| **19001** | Gateway | GatewayServer 内部 WS（`GATEWAY_PORT`，路由 `/tui`、`/acp`、`/channel-config`） |
| **19100** | Gateway | A2A 协议服务（路径 `/a2a`） |
| **5173** | Web 前端 | Vite/静态前端（`FRONTEND_PORT`） |

多实例：`--name <instance>` 端口组按 `base + index*1000` 递推（`compute_auto_port`），`GatewayLock` 防同 workspace 双 Gateway。桌面形态端口整体偏移到 18592/18591/18590 族（且 stdio/管道形态下不再监听）。

### 启动编排

- **`start_services.py`**（`jiuwenswarm-start`）：模式 `all/app/web/dev`；端口冲突时自动扫描回退端口组并持久化到 `.env`/`instances.yaml`（`_resolve_ports_with_fallback`）；启动后探测端口就绪并打印访问 banner。
- **`app.py`**（`jiuwenswarm-app`）：Popen 两个子进程，frozen 形态传 `--desktop-run-agent`/`--desktop-run-gateway` flag，源码形态用 `-m` 模块方式。
- **`scripts/jiuwenswarm_exe_entry.py`**：PyInstaller frozen 总入口。按 flag 分发：`--desktop-run-app` / `--desktop-run-web` / `--desktop-run-agent` / `--desktop-run-gateway` / `--desktop-run-jiuwenbox` / `--desktop-run-win-setup` / `--desktop-install-update`；支持 `--desktop-secrets-stdin`（stdin 首帧读密钥包）、单实例锁 `~/.jiuwenswarm/.desktop.lock`、UTF-8/CREATE_NO_WINDOW 补丁。

---

## 三、Gateway 层详解（`jiuwenswarm/gateway/`）

装配入口 `app_gateway.py::_run()`（:1455 起），`main()` 在 :3148。

### 3.1 核心组件

| 组件 | 位置 | 职责 |
|---|---|---|
| **ChannelManager** | `channel_manager/channel_manager.py:35` | 渠道注册/启停；`start_dispatch` 启动 `_dispatch_robot_messages`，支持 fan_out_targets 多目标分发 |
| **MessageHandler** | `message_handler/message_handler.py:192` | 消息总线：`_user_messages` 入队 → `_forward_loop` → E2A 归一化 → 发往 AgentServer；响应入 `_robot_messages` 回渠道 |
| **WebSocketAgentServerClient** | `routing/agent_client.py:210` | Gateway→AgentServer 的 E2A 客户端；桌面形态自动切命名管道（`resolve_agent_e2a_pipe_path` :102 读密钥包 `pipes.agentE2a`） |
| **GatewayServer** | `app_gateway.py:464` | 多路由 WS 宿主，帧协议 `{type:req\|res\|event, id, method, params}`，握手先回 `connection.ack`；`RouteConfig` 按路径配 forward_methods/local_handlers/拦截器 |
| **CronSchedulerService** | `cron/scheduler.py:303` | 定时任务调度（`CronJobStore` 落 cron_jobs.json），唤醒 agent 并推送渠道 |
| **GatewayHeartbeatService** | `heartbeat/` | 心跳，默认 60s |
| im_pipeline / reverse_rpc / gateway_push / gui_rpc / hooks | 各子目录 | 数字分身 IM 管道、小艺设备反向 RPC 等 |

### 3.2 渠道清单（register_spec 在 `app_gateway.py:1778-1837`）

- **内置渠道**：
  - `web`（`WebChannel`，`web_connect.py:159`）— 浏览器端，监听 19000 `/ws`；桌面形态改起命名管道 server（`_PipeClientAdapter`，同一协议内核）
  - `tui`（`TuiChannel` + GatewayServer `/tui` 路由）
  - `acp`（`AcpGatewayBridge`，`protocol/acp/acp_connect.py:122`）— IDE 集成，ACP JSON-RPC `session/prompt` ↔ 内部 `chat.send`
  - `a2a`（`A2AChannel`，19100 `/a2a`）
- **IM 平台**（`channel_manager/im_platforms/`）：xiaoyi（小艺）、feishu（含企业飞书多 bot）、dingtalk、telegram、discord、slack、whatsapp、wecom、wechat、qq、weibo；另有 protocol/ssh。
- 每个 channel 用 `ChannelSpec` 声明 config 模型、必填字段、能力 `ChannelCapabilities`、healthcheck；动态启停在 `_apply_channel_config`（:2354），配置热更经 `channel.configure` → 重建 channel + `agent.reload_config` 通知 AgentServer。

### 3.3 WebChannel（Web 前端渠道）

- 监听 `ws://0.0.0.0:19000/ws`，事件类型白名单 `_WEB_FULL_PAYLOAD_EVENT_TYPES`（web_connect.py:57-91，含 `chat.ask_user_question`、`chat.tool_call`、`chat.delta` 等）。
- Web RPC（`config.get/set`、`session.list` 等）由 `app_web_handlers.py::_register_web_handlers` 批量注册。
- **注意**：桌面形态下 Gateway 的 WS server 整体不监听（`app_gateway.py:2028 gateway_ws_enabled = not is_desktop_runtime()`），WebChannel 改走命名管道。

---

## 四、AgentServer 层详解（`jiuwenswarm/server/`）

入口 `app_agentserver.py:324 main()`。启动期：安装 shell 安全钩子 + LLM SSE/命名管道补丁（:126-148）→ 加载扩展 → `AgentWebSocketServer.get_instance()` → `start_desktop_e2a_channels()`（:206）→ `server.start(listen_tcp=...)` → proactive engine → teammate bootstrap daemon。

### 4.1 E2A 协议（Everything-to-Agent）

- 定义在 `common/e2a/`（文档 `docs/zh/E2A-protocol.md`）：`E2AEnvelope`（models.py:87）/ `E2AResponse`（:164）；线编解码 `wire_codec.py`；ACP/A2A 与 E2A 互转在 `adapters.py`。
- **三种传输形态**（`server/e2a_transports.py`），共用 `run_connection` 内核（agent_ws_server.py:1623）：
  1. `WsMessageTransport` — TCP WS（默认，127.0.0.1:18092）
  2. `StdioMessageTransport` — 桌面形态与主进程 stdio 直连（长度前缀帧，首帧 auth token）
  3. `PipeMessageTransport` — 桌面形态与 Gateway 兄弟进程的命名管道（`pipes.agentE2a`，`serve_pipe` + 镜像白名单校验）

### 4.2 dispatch 机制

`AgentWebSocketServer`（`agent_ws_server.py:1002`，单例）：

- `_handle_message`（:1735）解析 E2AEnvelope → `e2a_to_agent_request` → 大表分发（:1835-2164）。
- `session.create` → `_handle_session_create`（:2935，服务端分配 session id + create_token 去重）。
- `chat.send` / `chat.resume` / `chat.user_answer`（ask_user_question 应答）→ 流式走 `_handle_stream_impl`（:3103）→ `agent.process_message_stream(request)`；非流式走 `_handle_unary_impl`（:2919）。
- ReqMethod 全量枚举在 `common/schema/message.py:10-210`：`chat.*`、`session.*`（create/switch/delete/rename/fork/rewind）、`history.get`、`team.*`、`skills.*`、`plugins.*`、`extensions.*`、`agents.*`、`schedule.*`、`permissions.*`、`sandbox.*`、`symphony.*`、`agent.reload_config`、`agent.prewarm.sync` 等。
- `AgentManager`（`runtime/agent_manager.py:106`）：按 channel 缓存/借用/退役 JiuWenSwarm 实例；断连时 `cancel_all_inflight_work`。

### 4.3 核心运行链路

```
chat.send → dispatch → JiuWenSwarm.process_message_stream   （统一门面，runtime/agent_adapter/interface.py:850）
  → _ensure_adapter（按 work_mode 选 Code/Deep adapter）
  → JiuWenSwarmDeepAdapter.process_message_stream_impl      （interface_deep.py:9380）
    → openjiuwen DeepAgent（create_deep_agent，openjiuwen.harness.factory）
      → rails（_build_agent_rails :4927）、技能 rail（_build_skill_rail :4244）
      → 子 agent（_build_configured_subagents :2363 / _load_custom_subagents :12391）
      → LLM 调用（openai 兼容，react 配置段）
```

- **Swarm/Team 模式**：`agents/swarm/`（`enrich_team_spec_for_swarm`，leader/teammate 角色富化）+ `agents/harness/team/team_manager.py:354 TeamManager`；分布式 team 走 `team/distributed_runtime.py`（ZMQ transport）；远端成员由 `remote_member_bootstrap` daemon 拉起。
- **task_tool 子 agent**：本体在 openjiuwen 包内；本仓做 debug 追踪补丁（`runtime/debug_trace/task_tool_patch.py`）+ config 白名单（config.yaml `task_tool`，默认可调 code_agent/research_agent 等）。
- **Skill 系统**：`runtime/skill/skill_manager.py:415 SkillManager`（marketplace/skillnet/clawhub/teamskillshub/retrieval/evolution）；技能开发 `skilldev/`；运行时挂载经 SkillUseRail。
- **记忆系统**：`agents/harness/common/memory/`（sqlite-vec 向量索引 MemoryIndexManager、CeliaMcpClient 远程记忆、dreaming 梦境整理、auto_memory）。
- **Cron 桥**：agent 侧工具桥 `agents/harness/common/tools/cron/cron_runtime.py CronRuntimeBridge`（调度本体在 Gateway 侧）。

---

## 五、Gateway ↔ AgentServer 消息流

```
用户/IM 消息
  → Channel（web/tui/xiaoyi/…）
  → ChannelManager._on_channel_message
  → MessageHandler.handle_message（_user_messages 队）
  → _forward_loop：渠道控制命令拦截（/new_session、/mode）、session 分配、GodView 注册
  → e2a_from_agent_fields（E2A 归一化，common/e2a/gateway_normalize.py）
  → WebSocketAgentServerClient.send_request_stream
  ══════════════ E2A over WS :18092 / 命名管道 ══════════════
  → AgentWebSocketServer._handle_message → dispatch
  → JiuWenSwarm.process_message_stream → DeepAgent
  ══════════════ chunk 经 E2AResponse 回流 ══════════════
  → gateway client 收包 → MessageHandler.publish_robot_messages（:2356）
  → ChannelManager._dispatch_robot_messages（按 channel_id/RoutingKey）
  → Channel → 用户
```

反向控制：Gateway 经同一连接发 `agent.reload_config`（配置热更）、`agent.prewarm.sync`、`browser.runtime_restart`；AgentServer 反向推送走 `send_push` + reverse_rpc。

---

## 六、common/ 共享基础设施

| 模块 | 要点 |
|---|---|
| `common/config.py` | `get_config` 读 config.yaml，`${VAR:-default}` 环境变量替换 |
| `common/np_transport.py` | **Windows 命名管道全栈**：`FrameDecoder`（4 字节小端长度前缀帧）、`PipeStream`（overlapped IO）、`open_pipe`/`serve_pipe`、SDDL 安全描述符（`default_pipe_sddl` :461）、`make_image_verifier`（PID→镜像白名单）、`named_pipe_transport_for`（给 httpx/openai 用，支撑 `API_BASE=np://` 的 LLM 调用，配合顶层 `llm_np_patch`） |
| `common/secrets_bootstrap.py` | 桌面密钥包引导：`bootstrap_secrets_from_stdin`（stdin 首帧）、`get_secret("pipes.agentE2a")` 点路径取值；`secrets_loaded` 是桌面形态判据 |
| `common/e2a/` | E2A 协议模型/编解码/归一化 |
| `common/schema/message.py` | ReqMethod/EventType/Message 枚举 |
| `common/permission_profile.py` | IM 渠道权限档位（normalize/patch/trusted_dirs/workspace 指令） |
| `common/invocation_context/billing_trace.py` | 小艺计费 trace 标记（begin/end/failed 虚拟调用） |
| `common/security/ws_origin.py` | WS Origin 校验、X-User-Id 提取 |
| `common/updater.py` / `upgrade_executor.py` | 自更新 |

---

## 七、其他子系统

### 7.1 instance_manager（多实例）
`config.py`（BASE_PORTS/端口分配）、`lock.py`（InstanceLock / GatewayLock）、`yaml.py`（instances.yaml）。端口冲突自动回退并持久化。

### 7.2 symphony（技能演化）
`build.py`、`evolution/`、`experience/`、`graph/`、`orchestration/` — 技能自演化/评测图谱/经验沉淀。

### 7.3 extensions
`ExtensionRegistry` / `ExtensionManager`、yuanrong_frontend_client、agentos 扩展。

### 7.4 jiuwenbox（沙箱）
顶层 `jiuwenbox/` 是独立子项目（自带 pyproject），经 `package-dir` 打进同一发行包。FastAPI 沙箱管理服务（默认 127.0.0.1:8321）：Linux 用 bubblewrap+Landlock+seccomp+cgroup+网络命名空间；Windows 用 ACL/Job Object/WFP 防火墙；另有推理隐私代理 `proxy/`。AgentServer 内 `JiuwenBoxRunner` 按 `sandbox.enabled + startup_mode=internal` 拉起 box-server 子进程（frozen 形态经 `--desktop-run-jiuwenbox`）。

### 7.5 部署
- `docker/`：Dockerfile.claw / Dockerfile.claw.base / Dockerfile.yr.rt.mgr
- `deploy/`：observability（otel + Langfuse）、yuanrong 分布式部署
- `scripts/`：build-exe.ps1（PyInstaller onedir）、installer.iss（Inno Setup）、HarmonyOS 打包等

---

## 八、关键路径速查

| 场景 | 链路 |
|---|---|
| 浏览器对话 | 浏览器 → `ws://127.0.0.1:19000/ws`（WebChannel）→ MessageHandler → `ws://127.0.0.1:18092`（E2A）→ AgentWebSocketServer → JiuWenSwarm → DeepAgent |
| TUI/CLI 对话 | `jiuwenswarm chat` → `ws://127.0.0.1:19001/tui`（GatewayServer + TuiChannel）→ 同上 |
| IDE/ACP | `ws://127.0.0.1:19001/acp`（AcpGatewayBridge）→ 同上 |
| IM（小艺等） | IM 平台 ← → xiaoyi channel → MessageHandler → 同上 |
| 渠道配置热更 | `channel.configure` → ChannelManager.set_conf → `_apply_channel_config` 重建 channel + `agent.reload_config` 通知 AgentServer |
| 桌面形态 | 全部 TCP 关闭：E2A 走 stdio（主进程）+ 命名管道（gateway 兄弟进程），密钥走 stdin 首帧密钥包 |

---

*下一篇：[claw_desktop 集成 JiuwenSwarm 分析](./claw-desktop-jiuwenswarm-integration.md)*
