# 小艺Work（claw_desktop）集成 JiuwenSwarm 深度分析

> 分析对象：`D:\hcj_data\code\claw_desktop_jiuwen`（Electron + React + TS 桌面客户端）× `D:\hcj_data\code\jiuwenswarm`（Python 后端）
> 文档日期：2026-09-02
> 上一篇：[JiuwenSwarm 项目架构深度解析](./jiuwenswarm-architecture-analysis.md)

---

## 〇、集成总览

小艺Work 装一个 exe = **Electron 应用 + 内置 JiuwenSwarm（PyInstaller onedir）+ 预置 Python/Node 运行时**。

```
┌──────────────────────── 小艺Work.exe（Electron 单进程组）────────────────────────┐
│  渲染进程（React UI）                                                           │
│    └─ api facade → preload（ipcRenderer）                                       │
│  主进程                                                                         │
│    ├─ IPC handlers（claw:chat:send …）                                          │
│    ├─ FrameworkService / frameworkRegistry（本地=jiuwenswarm / 云端=cloud）      │
│    ├─ JiuwenSwarmFramework（框架适配层）→ JiuwenSwarmClient（E2A 客户端）        │
│    ├─ StdioTransport / PipeTransport / WebSocketTransport（传输层）              │
│    ├─ JiuwenProcessRuntime（子进程生命周期：spawn/stop/restart/孤儿清扫）        │
│    └─ 管道服务端×5：claw-model / claw-upload / claw-expert-repo / claw-relay / claw-skill │
└───────┬───────────────────────────────────────────────┬───────────────────────┘
        │ stdio 长度前缀帧（E2A，主通道）                │ 命名管道 \\.\pipe\claw-agent-e2a（兄弟通道）
        ▼                                                ▼
┌─────────────────────────┐                  ┌─────────────────────────┐
│ 子进程 1：AgentServer    │ ◄── E2A ──────── │ 子进程 2：Gateway        │
│ --desktop-run-agent      │  claw-agent-e2a  │ --desktop-run-gateway    │
│ --desktop-secrets-stdin  │                  │ --desktop-secrets-stdin  │
│ 对话/技能/专家/会话      │                  │ cron WebChannel 管道     │
│ （不监听 TCP）           │                  │ (\\.\pipe\claw-cron)     │
└───────┬─────────────────┘                  │ + xiaoyi 渠道宿主        │
        │ np://claw-model（LLM 调用代理）     │ （经 claw-relay 上云）   │
        │ np://claw-upload（文件回传）        └──────────┬──────────────┘
        │ np://claw-expert-repo（专家仓库）              │ np://claw-relay
        │ 孙进程 → \\.\pipe\claw-skill（skill 云 API）   │ np://claw-upload
        ▼                                                ▼
   回主进程管道服务端 ────────────────────────────────────┘
```

关键设计取向：**桌面形态零 TCP loopback 监听**（2026-08-25 命名管道迁移落地）——18592/18591/18590/19691/19692 全部停开，唯一保留的 TCP 监听是 relay 本地中转 `ws://127.0.0.1:19690`（invoke 插件 skill 只会讲 WS）。密钥**不经 env/命令行**下发，走 stdin 首帧密钥包。

---

## 一、问题（1）：本地对话直连 AgentServer 还是 Gateway 的 WebChannel？

### 1.1 结论

**本地对话直连 AgentServer（E2A over stdio 长度前缀帧），完全不经 Gateway，也不用 WebChannel。**

对话流量与 Gateway 的唯一关系是：cron 定时任务的 RPC（`cron.job.*`）走 Gateway 内 WebChannel 的管道形态（`\\.\pipe\claw-cron`），且 cron 调度器本体跑在 Gateway 进程内。**对话流量永远不打 Gateway。**

### 1.2 代码证据链

**桌面侧（claw_desktop）**：

1. `src/main/services/runtime-service.ts:345-374` `createE2aTransport()`：默认返回 `StdioTransport`（childProvider → `jiuwen.getAgentChild()`，即 **AgentServer 子进程**）；仅 `CLAW_E2A_TRANSPORT=ws` 时回退 `WebSocketTransport(ws://127.0.0.1:18592)`（迁移期联调留的逃生舱）。形态判定唯一事实源 `src/core/runtime/e2a-mode.ts:14-16`。
2. 框架构造注入该 factory：`src/main/services/framework-service.ts:793-798`（stdio 形态 ack 超时放宽到 120s，覆盖 PyInstaller 冷启动）。
3. `GatewayWebClient`（`src/core/framework/jiuwenswarm/gateway-web-client.ts`，type=req/res，**非 E2A**）只被 `gateway-cron-rpc.ts:56-75` 用于 `cron.job.*`。

**jiuwen 侧**：

4. `server/app_agentserver.py:206-210`：桌面形态 `desktop_channels = await start_desktop_e2a_channels(...)`，`server.start(listen_tcp=desktop_channels is None)`——**AgentServer 不监听 18592**。
5. `gateway/app_gateway.py:2028`：`gateway_ws_enabled = not is_desktop_runtime()`——**Gateway 的 18591（/acp /tui）停开**。
6. `gateway/channel_manager/web/web_connect.py:598-606`：WebChannel 桌面形态不监听 TCP（18590），仅命名管道 server。

### 1.3 完整调用链（本地对话）

```
渲染层 appStore.ts:1670  api.chat.send(convId, text, options)
  → preload（ipcRenderer.invoke('claw:chat:send')，通道名只在 src/shared/ipc-channels.ts）
  → 主进程 IPC handler（src/main/ipc/index.ts:391-421）
      · resolveProjectDir 解析工作目录
      · modelProxy.setTraceId(conversationId)（x-hag-trace-id 追踪）
      · billing.startInteraction()（计费 NEW；余额不足抛 InsufficientBalanceError 拦截）
      · 生成 interactionId（UUID）
  → frameworkRegistry.active().chat.send(...)（本地默认 = jiuwenswarm 框架）
  → JiuwenSwarmFramework.chat.send（jiuwenswarm-framework.ts:1030-1120）
      · 注入 workMode / model_name / project_dir / trusted_dirs
      · 连接器说明走 wire 顶层 metadata.interaction_context（不进 query、不落历史）
      · interactionId 同时作信封 request_id 和 metadata.interaction_id（计费 trace 对齐）
  → JiuwenSwarmClient.requestStream('chat.send', params)（jiuwenswarm-client.ts）
      · buildEnvelope：protocol_version:'1.0'、channel:'desktop'、session_id 顶层、is_stream:true
  → StdioTransport.send → encodeFrame → child.stdin.write
  ═══════════════ stdio 长度前缀帧（4 字节小端 + UTF-8 JSON，≤8MiB）═══════════════
  → AgentServer 子进程 stdin → StdioMessageTransport（e2a_transports.py:212-312，
     守护读线程 + asyncio 队列）
  → AgentWebSocketServer.run_connection 公共连接内核 → dispatch（chat.send）
  → JiuWenSwarm.process_message_stream → DeepAgent → LLM（经 np://claw-model 代理）

回程：E2AResponse 帧由 AgentServer os.write(1) 直写 stdout
  → StdioTransport FrameDecoder → JiuwenSwarmClient.handleMessage（request_id 关联）
  → JiuwenSwarmFramework.handleStreamEvent（event_type → ChatStreamEvent）
  → FrameworkService.taggedEvents → IPC setEventSink
  → isForwardableChatStreamEvent 白名单过滤 → broadcast('claw:chat:stream')
  → preload chat.onStream → appStore streamEventBatcher → chat-run-state（run 状态机）
```

### 1.4 与"走 Gateway WebChannel"的区别

| 维度 | 直连 AgentServer（桌面实际方案） | 走 Gateway WebChannel（Web 形态方案） |
|---|---|---|
| 进程路径 | 主进程 → AgentServer，**一跳** | 主进程 → Gateway（MessageHandler 路由）→ AgentServer，**两跳** |
| 协议 | E2A 信封（`chat.send` 流式） | Gateway 自有帧协议 `{type:req\|res\|event}` + Web 渠道事件白名单 |
| 事件保真 | 全量 E2A 事件直达（`chat.delta/thinking/tool_call/ask_user_question/…`） | 经 `_WEB_FULL_PAYLOAD_EVENT_TYPES` 白名单过滤，部分事件形态被裁剪 |
| 会话管理 | `session.create` 直连 AgentServer（服务端分配 id + create_token 去重） | 经 Gateway session 绑定表（`_session_to_client`）中转 |
| 配置面 | `agent.reload_config` 直发 | webchannel 额外承载 `config.get/set` 等 Web RPC |
| 依赖 | 只需 AgentServer 一个子进程健康 | 依赖 Gateway 进程 + 渠道装配，故障面更大 |
| 桌面实际用途 | **全部对话/技能/专家/会话流量** | 仅 `cron.job.*`（且走管道形态而非 WS） |

一句话：**Gateway 是"多渠道接入网关"，桌面端自己就是唯一渠道，没有必要再过一层网关**；直连省一跳、事件无损、故障面小。Gateway 在桌面形态被保留只是因为 cron 调度器和 xiaoyi 渠道恰好都长在 Gateway 进程里。

### 1.5 Gateway 子进程何时拉起（重要勘误）

CLAUDE.md 旧表述「凭据齐全才拉起 gateway」**已过时**。当前实现（`jiuwen-runtime.ts:562-565` `reconcileGateway()`）：**Agent 运行中 Gateway 常驻拉起**——因为 `cron.job.*` RPC 只注册在 Gateway 的 WebChannel 上，桌面 cron 页依赖它。`xiaoyiReady`（凭据齐全与否）只决定 config.yaml 里 xiaoyi 渠道的 `enabled` 真假；配置实际变化（`xiaoyiConfigFingerprint` 比对）才 `restartGateway`。

桌面形态两进程分工：

| | AgentServer（`--desktop-run-agent --port 18592 --desktop-secrets-stdin`） | Gateway（`--desktop-run-gateway --desktop-secrets-stdin`） |
|---|---|---|
| 角色 | 对话/技能/专家/会话全部 E2A 流量 | ① cron WebChannel 管道 server + cron 调度器本体；② xiaoyi 渠道宿主 |
| 通信面 | stdio（主进程）+ `\\.\pipe\claw-agent-e2a` server（Gateway 兄弟进程连入） | `\\.\pipe\claw-cron` server（主进程连入）；经 `claw-agent-e2a` 反向连 AgentServer；经 `claw-relay` 连主进程上云 |
| TCP | 不监听 | 不监听 |

---

## 二、问题（2）：命名管道的建立、维护与通信

### 2.1 管道全景（7 条）

管道名规范：`\\.\pipe\claw-<name>`，全局固定不按 uid 分作用域（`src/core/net/pipe-path.ts` → `pipe-paths.ts:15-45`）；jiuwen 侧按 `np://claw-<name>` URL 分流，authority 段即管道名。

| 管道 | 服务端 | 客户端 | 用途 | 应用层鉴权 |
|---|---|---|---|---|
| `claw-model` | 主进程 `ModelProxy`（model-proxy.ts:228） | AgentServer 全部 LLM 调用（`llm_np_patch.py` monkey-patch OpenAI 客户端） | 模型请求代理：注入 businessCredential/用户 apiKey/x-uid/x-device-id/x-hag-trace-id 转发上游，SSE 透传，上游 401 重建重试一次 | `Authorization: Bearer <proxyKey>`（时序安全比较） |
| `claw-upload` | 主进程 `FileUploadProxy`（file-upload-proxy.ts:134） | Gateway send_file_to_user + AgentServer 工具（send_html_card/image_reading 等） | OSMS 文件回传代理 | `Bearer <uploadToken>` |
| `claw-expert-repo` | 主进程 `startExpertRepoServer`（expert-repo/server.ts:207） | AgentServer `expert_store.py`（np:// 分流 httpx transport） | 内置专家包仓库（云端能力画廊适配器），`/api/v1/packages` | **无令牌**（安全审计已列待补） |
| `claw-relay` | 主进程 `CloudWsRelay.startLocalPipeServer`（cloud-ws-relay.ts:897） | Gateway xiaoyi 渠道（`xiaoyi_connect.py::_connect_pipe`） | xiaoyi 渠道中转：Gateway ↔ 主进程 ↔ 云端 ws/link | 首帧 `{type:'auth', agentId, ak, ts, sign}`，`sign=base64(HMAC-SHA256(sk, ts))`，±5 分钟窗 |
| `claw-skill` | 主进程 `SkillApiProxy`（skill-api-proxy.ts:191） | skill 孙进程（jiuwen shell spawn 的 python/node；Python 客户端 `claw_pipe_http.py` stdlib-only） | skill 云 API 代理（path 前缀白名单），skill 侧零业务凭证 | `Bearer <skillToken>`（fail-closed；经 `CLAW_SKILL_TOKEN` env 下发——孙进程无 stdin 通道，env 是唯一载体，属有意例外） |
| `claw-agent-e2a` | **AgentServer**（`server/e2a_desktop.py:146-180`） | Gateway 兄弟进程（`routing/agent_client.py`） | Gateway → AgentServer 的 E2A 兄弟通道（stdio 连不了兄弟进程） | e2aToken 首帧 + PID→镜像白名单 |
| `claw-cron` | **Gateway** WebChannel 管道形态（`web_connect.py:1274-1326`） | 主进程 `PipeTransport`（`GatewayWebClient`/`gateway-cron-rpc.ts`） | `cron.job.list/get/meta/create/update/delete/toggle/preview/run_now` + cron `chat.final` 推送 | 首帧 `{type:'auth', token: e2aToken}`（hmac.compare_digest） |

**方向总结**：主进程是 model/upload/expert-repo/relay/skill 五条的**服务端**；claw-agent-e2a 与 claw-cron 的服务端在 **jiuwen 子进程侧**（AgentServer 与 Gateway 各自 listen，主进程/Gateway 作为 client 连入）。

### 2.2 建立：服务端如何创建

**桌面侧（Node/libuv，5 条）**：
- HTTP 语义的三条半用 `http.createServer(handler).listen(pipePath)`（model/upload/skill/expert-repo）；relay 用 `net.createServer().listen(pipePath)`（逐帧双向）。
- **幂等监听**：各 `start()` 里 `if (this.pipePath && !this.pipeServer)` 守卫——已在监听不重绑；relay 注释明确「重绑会切断已连入渠道连接」（cloud-ws-relay.ts:830-833）。

**jiuwen 侧（pywin32，2 条）**：`common/np_transport.py:517-679 PipeServer`——
- `CreateNamedPipe`：`PIPE_ACCESS_DUPLEX | FILE_FLAG_OVERLAPPED`、`PIPE_TYPE_BYTE`、`PIPE_UNLIMITED_INSTANCES`、64KB 缓冲；
- accept 线程跑 `ConnectNamedPipe` overlapped（stop event 可取消），每连接一个管道实例；
- 已建连接 `call_soon_threadsafe` 交回 asyncio 循环。

### 2.3 安全防护（实测口径，修正文档旧表述）

**三重防护只在 jiuwen 侧 2 条管道上全配**（安全审计 2026-08-29 实测）：

1. **SDDL 内核级 ACL**：`default_pipe_sddl()`（np_transport.py:461-467）= `D:P(A;;GA;;;<当前用户SID>)(A;;GA;;;SY)`——仅本人 + SYSTEM，跨用户被内核直接拒绝。
2. **对端 PID→镜像白名单**：`GetNamedPipeClientProcessId` + `make_image_verifier`（np_transport.py:489）。claw-agent-e2a 白名单 = `{sys.executable, sys._base_executable}`（gateway 与 agent 同一个 jiuwenswarm.exe）；claw-cron 白名单 = `{desktopExe, sys.executable, ...}`（desktopExe 由密钥包下发 = `process.execPath`）。
3. **首帧令牌握手**：e2aToken / HMAC 首帧，`hmac.compare_digest` 常量时间比对，**失败即断开/退出进程**。

**桌面侧 5 条**是 libuv 默认 ACL（未做 SDDL 收紧、无 PID 白名单），防护 = 应用层令牌（Bearer/HMAC 首帧）+ relay 的「鉴权前 10s 空闲超时断管」。这是已识别的 P1 整改项。

### 2.4 通信模型与帧协议

- **帧契约**（双仓同步）：4 字节小端无符号长度前缀 + UTF-8 JSON，单帧上限 8MiB。`src/core/net/length-prefix.ts:24-70` ↔ `jiuwenswarm/common/np_transport.py:86-134`。半包/粘包由解码器内部缓冲处理；超长/零长/非法 JSON 抛 FrameCodecError，管道服务端遇协议错误断管自保。
- **连接模型**：
  - HTTP 语义的管道（model/upload/skill/expert-repo）：**每请求一连接**（`NamedPipeTransport`，httpx transport，`Connection: close`，无复用歧义）。
  - relay/cron/agent-e2a：**长连接逐帧双向**。
- **断线重连**：
  - 桌面客户端 `PipeTransport`：指数退避自动重连（base 1s→max 30s；cron 通道 800ms→8s），端点列表 pipe 优先、WS 兜底逐端点重试 3 次。
  - jiuwen 客户端 `open_pipe()`：WaitNamedPipe 语义重试（ERROR_PIPE_BUSY/FILE_NOT_FOUND 轮询至 timeout）。
  - xiaoyi 管道断连由 `_reconnect_loop` 5s 退避重连。
- **E2A stdio 与管道的关系**：stdio 是主进程↔AgentServer 的主通道（fd 继承，天然私有）；`claw-agent-e2a` 是 Gateway 兄弟进程↔AgentServer 的旁路（兄弟进程拿不到对方的 stdio，必须用命名管道）。两者共用 `run_connection` 连接内核与同一套帧。

### 2.5 密钥下发：stdin 首帧密钥包（为什么不用 env）

- **桌面侧**：`src/core/runtime/secrets-frame.ts`（`buildSecretsFrame`/`writeSecretsFrame`）；spawn 后立即写入 stdin 首帧，按角色最小子集组包（runtime-service.ts:414-453）：
  - agent 包：`{e2aToken, proxyKey, uploadToken, businessCredential, uid, desktopExe, e2aTransport, pipes}`（pipes = 六条管道完整路径表）
  - gateway 包：`{e2aToken, uploadToken, localAuth{ak,sk,agentId}, uid, apiKey, desktopExe, e2aTransport, pipes}`
  - 令牌每启动周期随机、仅内存（`e2a_${hex}`/`upk_${hex}`/`skt_${hex}`）。
- **jiuwen 侧**：`common/secrets_bootstrap.py`——入口脚本在任何子命令分发前 `bootstrap_secrets_from_stdin()`（exe_entry.py:422-431），读进进程内内存 vault（`_SECRETS` dict，`get_secret('localAuth.sk')` 点路径取值），**不回注 os.environ、不落盘、不进日志**；复用进程内唯一 stdin 二进制 reader（防 BufferedReader 预读丢字节，E2A stdio 共用）；15s 超时回退 env 兼容形态。
- **为什么不用 env/命令行**：同用户其他进程可枚举 PEB/WMI 读到环境变量与命令行；stdin 是匿名管道句柄继承，第三方进程无法连接或枚举。唯一有意例外是 `CLAW_SKILL_TOKEN`（孙进程没有 stdin 通道）。
- **防窃取配套**：发布形态 spawn 前对 jiuwenswarm.exe 做 sha256 旁车验签（`exe-integrity.ts`），防替换 exe 窃取 stdin 密钥包。
- config.yaml 里 `${CLAW_XIAOYI_*}` 占位符由 `common/config.py:32-90` 映射到密钥包点路径（localAuth.ak/sk/agentId、uid、apiKey），配置文件本身零秘密。

### 2.6 jiuwen 侧如何消费 np:// URL

- `np_transport.py:52-79`：`is_named_pipe_url` / `pipe_path_from_url`（authority 段即管道名）。
- **模型调用**：`.env API_BASE = np://claw-model/v1` → `llm_np_patch.apply_openai_np_patch()`（app_agentserver.py:146-148 启动早期）monkey-patch openjiuwen `OpenAIModelClient._build_async_openai_client` 与 `openai.OpenAI.__init__`：base_url 为 np:// 时注入 `httpx.AsyncClient(transport=NamedPipeTransport(\\.\pipe\claw-model), trust_env=False)`，api_key 优先取密钥包 proxyKey。`trust_env=False` 保证 np 流量绝不被 HTTP_PROXY 劫持。
- 其余消费点（expert_store、file_upload_helpers、xiaoyi_connect relay、image_reading_tool）同按 scheme 分流；`local_proxy_auth.py with_local_proxy_bearer` 只对 np:// 或 loopback URL 补 Bearer，绝不随外网 URL 出站。
- skill 孙进程用 `claw_pipe_http.py`（stdlib-only HTTP/1.1 over 管道，`os.open` 直连无需 pywin32，支持 SSE iter_lines）。

### 2.7 子进程生命周期（管道的宿主管理）

`src/core/runtime/jiuwen-runtime.ts`（`JiuwenProcessRuntime`）：

- **双形态启动**：发布 = `resources/jiuwenswarm/jiuwenswarm.exe`（PyInstaller onedir，spawn 前验签）；开发 = 同级源码仓 `../jiuwenswarm`，spawn `.venv/Scripts/python.exe scripts/jiuwenswarm_exe_entry.py`，改代码即生效。
- **串行锁**：`enqueueAgentOp`/`enqueueGatewayOp`（:589-606）——spawn/stop/restart/崩溃重拉全部排队（修复一次拉起 2 agent + 4 gateway 的事故）。
- **崩溃重拉**：gateway 意外退出 5s 重拉（agent 还活着才拉；主动停止取消定时器）；agent 退出 → probe 更新健康。
- **孤儿清扫**：`sweepOrphanProcessesOnce`（:622-650）每启动周期一次，spawn 前按本实例 exe 路径/源码入口匹配枚举残留进程，`killTree` 整树清理——**避免旧进程占着管道名/数据目录**。
- **登录前置**：未登录不启动子进程；多用户切换由 `UserSessionCoordinator` 停框架/子进程 → 翻数据根 → 重建框架实例。
- **隔离**：`JIUWENSWARM_DATA_DIR/HOME=<用户数据根>/jiuwenswarm`；env 白名单制（剔除继承的 `JIUWENSWARM_*`）；预置 Python/Node + 连接器 CLI 注入 PATH；PYTHONUTF8/LC_ALL=C.UTF-8 编码钉死。

### 2.8 维护要点速查

| 关注点 | 机制 |
|---|---|
| 管道名冲突 | 固定名 + 幂等监听守卫；孤儿进程清扫防上一代占用 |
| 断管恢复 | 客户端指数退避重连；HTTP 类每请求一连接天然无状态 |
| 跨用户窃听 | jiuwen 侧 SDDL（内核拒）；桌面侧待整改（现靠令牌） |
| 伪造对端 | jiuwen 侧 PID→镜像白名单；全链路首帧令牌/HMAC |
| 密钥泄露面 | stdin 密钥包（不落盘、不进 env/日志）；exe 验签；config 全占位符 |
| 半包/粘包/巨帧 | 长度前缀帧解码器缓冲；8MiB 上限；协议错误断管自保 |

---

## 三、补充：Gateway ↔ AgentServer 子进程间通信（手机端消息场景）

桌面形态下唯一一条"子进程 ↔ 子进程"链路。AgentServer 与主进程靠 stdio 句柄继承通信，但 Gateway 是 AgentServer 的**兄弟进程**（stdio 是父子私有通道），所以 AgentServer 额外起命名管道 server 专供 Gateway（`server/e2a_desktop.py:16-20`："stdio 无法连兄弟进程"）。

### 3.1 手机端消息完整链路

```
手机端 App → 云端 ws/link
  → 主进程 CloudWsRelay（wss 外连）
  → ③ 管道 \\.\pipe\claw-relay（首帧 HMAC 签名鉴权）
  → Gateway 子进程 xiaoyi channel（xiaoyi_connect._connect_pipe）
  → ChannelManager._on_channel_message
  → MessageHandler._forward_loop（会话分配/命令拦截/E2A 归一化 e2a_from_agent_fields）
  → WebSocketAgentServerClient.send_request_stream
      connect() 时 resolve_agent_e2a_pipe_path()（agent_client.py:102-122）
      命中密钥包 pipes.agentE2a → open_pipe(\\.\pipe\claw-agent-e2a)
      → 发 auth 首帧 {"type":"auth","token":e2aToken}（agent_client.py:335-348）
  ═══ 命名管道：4 字节小端长度前缀 + UTF-8 JSON 帧 ═══
  → AgentServer e2a_desktop._handle_pipe_connection（e2a_desktop.py:182-206）
      三重校验：SDDL 仅本人+SYSTEM → GetNamedPipeClientProcessId 镜像白名单
      （{sys.executable, sys._base_executable}——gateway 与 agent 同一个
      jiuwenswarm.exe；_base_executable 兼容 uv trampoline .venv）→
      e2aToken 首帧 hmac.compare_digest（10s 超时，失败仅断该连接）
  → run_connection 公共连接内核 → 回 connection.ack → dispatch(chat.send)
  → JiuWenSwarm.process_message_stream → DeepAgent → LLM

回程：chunk 经同一管道连接流回
  → Gateway 侧 _PipeWsAdapter.recv（agent_client.py:125-153，管道包装成
    recv/send/close 的 ws 鸭子形态，复用 WS 接收循环）
  → _message_receiver_loop 按 request_id 分发 → publish_robot_messages
  → ChannelManager 派发回 xiaoyi channel → claw-relay → CloudWsRelay 上云 → 手机
```

### 3.2 关键设计点

1. **协议零分叉**：管道只是传输层替换。E2A 信封、request_id 关联、connection.ack、server push（反向 RPC 同走此连接）与 WS 形态完全一致；两条通道（stdio 主进程 + 管道 Gateway）进同一个 `run_connection` 内核。
2. **单条长连接 + 请求关联**：Gateway 对 AgentServer 维持一条管道长连接，所有渠道（xiaoyi、cron 唤醒等）复用，靠 `request_id` 分发响应。
3. **形态判定跟随密钥包 `e2aTransport` 字段**（字段判定而非密钥包存在）：`stdio` 才走管道并停开 18592；否则回退 `AGENT_SERVER_URL` 的 `ws://127.0.0.1:18092`——同一套 Gateway 代码在服务器形态下就是 E2A over WebSocket。
4. **故障隔离**：管道鉴权失败只断该连接、不影响 stdio 主通道；`open_pipe` 自带 WaitNamedPipe 等待重试，AgentServer 未就绪时 Gateway 等待而非报错；AgentServer 管道 server 启动失败不波及 stdio（e2a_desktop.py:177-179）。

---

## 四、两个问题的直接回答（TL;DR）

**Q1：本地对话对接 AgentServer 还是 Gateway 的 WebChannel？**

> **直连 AgentServer**，走 stdio 长度前缀帧承载的 E2A 协议（`channel:'desktop'`）。Gateway 的 WebChannel 在桌面形态不监听 TCP、不承载对话，只以 `\\.\pipe\claw-cron` 管道形态提供 `cron.job.*` RPC。区别在于：直连少一跳、事件全量无损（不经 WebChannel 白名单裁剪）、故障面小；Gateway 在桌面形态的存在意义是 cron 调度器本体 + xiaoyi 渠道宿主，且它是**常驻拉起**的（不是旧文档说的"凭据齐全才拉起"）。

**Q2：子进程经主进程建通信链路时，命名管道如何维护与通信？**

> 七条固定名管道 `\\.\pipe\claw-{model|upload|expert-repo|relay|skill|agent-e2a|cron}`，**主进程是前五条的服务端**（Node `http/net.createServer().listen(pipePath)`，幂等不重绑），**jiuwen 子进程是后两条的服务端**（pywin32 `CreateNamedPipe` overlapped + accept 线程）。帧协议统一为 4 字节小端长度前缀 + UTF-8 JSON（≤8MiB，双仓同构 codec）。安全分两层：jiuwen 侧三重防护（SDDL 仅本人+SYSTEM、对端 PID→镜像白名单、首帧令牌 HMAC 握手）；桌面侧靠应用层 Bearer/HMAC 令牌（SDDL 收紧是已识别的待整改项）。密钥不经 env/命令行，spawn 后经 **stdin 首帧密钥包**下发（含 pipes 路径表与各令牌），jiuwen 侧读进内存 vault。连接模型：HTTP 语义管道每请求一连接；relay/cron/agent-e2a 长连接逐帧双向，断线指数退避重连；孤儿进程按 exe 路径每启动周期清扫，防管道名被旧进程占用。
