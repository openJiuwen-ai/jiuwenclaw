# 冷启动首 Token（recv → 发模型，除去 MCP）2 秒方案

> 状态：方案，不改代码。基于 2026-08-27 当天全部 AgentServer 冷启动首条 `chat.send` 日志 + 当前代码路径。
> 范围：`officeclaw` 渠道、服务进程第一次起来后的第一条用户 query。
> 不包含：模型 TTFT、relay-claw 前端/路由、MCP 握手与 MCP 工具注册本身。

---

## 1. 目标与口径

### 1.1 指标

| 名称 | 定义 | 目标 |
| --- | --- | ---: |
| **冷启动预 LLM（除去 MCP）** | `recv(chat.send)` → `llm_send`，减去请求级 OfficeClaw MCP 区间 | **≤ 2000 ms** |

锚点（已有日志，不新增协议）：

| 锚点 | 日志 | 含义 |
| --- | --- | --- |
| `recv` | `[latency] stage=0 name=recv` / `[E2A][in] method=chat.send` | AgentServer 收到 query |
| `init` | `[latency] stage=1 name=init` | session child `create_instance` 主体完成 |
| `mcp` | `[latency] stage=2 name=mcp` / `OfficeClaw MCP registered` | 请求级 MCP 注册完成 |
| `llm_send` | `openjiuwen/llm.log`：`Before request chat model, LLM request params ready.` | 即将向模型服务发 HTTP。比 `stage=3 pre_llm` 更接近“发送给大模型” |

MCP 扣除：

```
T_excl_mcp = (t_llm_send - t_recv) - (t_mcp - t_init)
```

只扣除 **请求级 OfficeClaw MCP**（`list_office_claw_mcp_tools` + 注册）。配置型 `_register_mcp_servers_from_config` 若出现在 `create_instance` 内，同样从该区间剥离，不计入 2 秒预算。

不纳入本目标：`llm_send` → 首 token（模型 TTFT，样本 6.5–9 s，不在本方案控制范围）。

### 1.2 约束（硬性）

方案不得引入下列下降：

- **效果**：首轮可见工具集合、MCP 工具、技能清单、权限/HITL、system prompt 语义（可缓存、可去重，不可删能力、不可靠猜测截断）。
- **性能**：同一 session 第二轮及以后（热路径）不得变慢；预热不得抢占在途 chat 的 OpenJiuwen 全局注册表锁。
- **稳定性**：session / project / checkpoint / MCP env 不得串会话；预热失败必须回退到今天的同步创建路径；不新增外部服务或持久化格式。

### 1.3 与已有文档的关系

已有 `2026-08-27-first-token-performance-design.md` 把冷启动 `chat.send → pre_llm` 定为 2.5 s，且阶段 C 只做共享 root。本方案把目标收到 **2.0 s（除去 MCP）**，并证明 **只做共享 root 不够**：root 省约 0.27 s，真正贵的是 session child `create_instance`（~1.0–1.7 s）和 runner 首次组 prompt（~1.2–1.9 s）。共享 root 是本方案的一块积木，不是全部。

---

## 2. 样本基线（当天全部冷启动首条）

数据来源：

- `jiuwenswarm/logs/service_default/.logs/agent_server.log`
- `.../agent_server_20260827_124017.log`
- `.../openjiuwen/server.log`（`create_instance_ms` / `prepare_ms`）
- `.../openjiuwen/llm.log`

七次都是：**进程起来后该 PID 的第一条 `chat.send`**，query 均为「你好」，模型 GLM-5.2，`channel=officeclaw`。预热日志一律 `pending=0 cancelled=0`。

| PID | 距进程启动 | recv→pre_llm | MCP (init→mcp) | **除去 MCP** | recv→llm_send* | invoke→model | create_instance | 系统 prompt |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 29444 | 70 s | 4200 | 482 | **3718** | ~4716 | — | 938 | — |
| 7036 | 88 s | 5106 | 775 | **4331** | ~5623 | — | 1062 | — |
| 9736 | 29 s | 6729 | 999 | **5730** | ~7299 | 1945 | 1656 | 44083 字 |
| 28212 | 760 s | 4447 | 637 | **3810** | ~4914 | 1195 | 968 | 44083 字 |
| 5660 | 22 s | 4464 | 797 | **3667** | **4942** | 1229 | 985 | 44083 字 |
| 5700 | 28 s | 5871 | 968 | **4903** | ~6475 | 1621 | 1485 | 44083 字 |
| 34860 | 215 s | 4530 | 862 | **3668** | ~5051 | — | 1000 | — |

\* `llm_send` 仅 5660 用 llm.log 精确到毫秒（21:03:25.680 → 21:03:30.622）。其余用 `pre_llm` 与该样本 `llm_send - pre_llm ≈ 478 ms` 估计，数量级可靠。

**代表样本（5660，结构最完整）除去 MCP 后约 3.7 s（到 pre_llm）/ 4.1 s（到 llm_send）。** 要到 2.0 s，需再砍 **1.7–2.2 s**。进程已空转 22–760 s 也从未命中预热，说明不是“用户打得太快”，是预热对 OfficeClaw **结构上无效**。

首轮模型侧：`input_tokens ≈ 32300–33200`，`cache_hit 34–41%`，工具 21 个（67 个里 eager 21 / deferred 46）。模型 TTFT 6.5–9 s 不在本指标内，但同一套 prompt 变小/更稳，对 TTFT 和缓存命中是顺带收益，不是本方案的验收项。

---

## 3. 代码路径（冷启动首条）

```
pipeline.dispatch_parsed_request          stage=0 recv
  → ExtensionRegistry BEFORE_CHAT_REQUEST
  → handlers._default._handle_stream
       begin_foreground_chat()            暂停后台预热（当天全部 pending=0）
  → AgentManager.process_message_stream
       wait_for_session_prewarm(session_id)   OfficeClaw 自带 session_id，几乎永不命中
       get_agent(channel, mode, project_dir)  cache_key 含「每线程唯一 workspace」
            miss → _create_agent              root.create_instance(skip DeepAgent)
  → JiuWenSwarm.process_message_stream    写 history、起 consumer
  → DeepAdapter.process_message_stream_impl
       _get_or_create_session_adapter     当天全部 miss
            child.create_instance         ~1.0–1.7 s  ← 最大可搬离项
            child.start_interaction       ~50–110 ms
       register_request_scoped_office_claw_mcp   扣除
       child.process_message_stream_impl
            _update_runtime_config        rails_for_mode 46–109 ms
            send_input → runner
            rails.before_invoke
            rails.before_model_call       组 44KB system prompt  ← 第二大项
            Model.stream 包装             stage=3 pre_llm
            提供方 HTTP                   llm.log llm_send
```

关键实现事实：

1. Root 已 `skip_own_instance_build()`，chat 路径不建第二份 DeepAgent。但 root `create_instance` 仍做 `set_checkpoint` + dotenv + 配置合并，首条仍 **190–320 ms**。
2. 真正的 DeepAgent 在 **session child**。`openjiuwen/server.log`：`create_instance_ms=938–1656`，rails 构建仅 47–93 ms，贵的是 checkpointer / 模型客户端 / tool cards / `create_deep_agent` / `ensure_initialized` / packages。
3. 请求级 MCP 在 child 创建之后、runtime config 之前，**串行**。
4. `AgentWarmPool` 全局最多 1 个 READY 槽；key 含 `project_dir`；`session.create` 才能 claim。OfficeClaw **日志中无任何 `session.create`**，session_id 为客户端 `officeclaw_<hex>`，与预热分配的 `<channel>_<ts>_<uuid>` 不是一套 ID。
5. 当天 cache_key 形如 `agent::...\relay-claw\workspace\2026082721031...`——每个新对话一个新目录，root 缓存和 WarmKey **必然 miss**。
6. `_schedule_runtime_state_write` 已是后台线程，不阻塞；但首次 `before_model_call` 仍要拼完整 system prompt。`ProgressiveToolRail` 自身仅 3–16 ms，**invoke→model 的 1.2–1.9 s 是其余 rails + OpenJiuwen 首次组包**。

---

## 4. 代表样本分段（PID 5660）

`request_id=64dfaff0-...`，`t0=21:03:25.680`。

```
recv 0
  ├─ 0–192     钩子 / begin_foreground / dispatch          192
  ├─ 192–461   root create（skip DeepAgent + checkpointer） 269
  ├─ 461–661   流式 consumer 启动                          200
  ├─ 661–1629  session create_instance                     968   ← 搬到启动后预热
  ├─ 1629–2426 请求级 MCP                                  797   ← 从指标扣除；可与后续重叠降墙钟
  ├─ 2426–2929 runtime config + attachments + send_input   503
  ├─ 2929–4158 runner 首次 before_invoke→before_model     1229  ← 静态前缀缓存
  ├─ 4158–4464 A2UI + 最终 messages + pre_llm              306
  └─ 4464–4942 pre_llm → llm.log 发送                      478  ← 大包序列化；DEBUG 下更明显
```

除去 MCP 后约 **4145 ms** 到 `llm_send`。热路径（同 session 第二轮）没有 `create_instance`，不在本方案改动范围内变慢。

---

## 5. 根因（按杠杆排序）

### R1. 预热对 OfficeClaw 结构失效（~1.0–1.7 s 白白放在首条上）

三件套叠在一起：

| 机制 | 现状 | 后果 |
| --- | --- | --- |
| 分配入口 | 只有 `session.create` + `create_token` 会 `claim()` | OfficeClaw 首条直接 `chat.send`，从不 claim |
| 缓存键 | root / WarmKey 都含 `project_dir` | 每线程 timestamp workspace → 永远 miss |
| 配额 | 全局 1 个 READY，优先级偏向 `web` | 即便 officeclaw 在 enabled_channels 里，槽也很难留给它 |

所以 `pending=0` 不是预热没跑完，是 **跑了也接不上这条请求**。

### R2. session child 创建在首条临界路径上（~1.0–1.7 s）

已有阶段 C「共享无项目 root」只省 root 那 0.27 s。child 的 `create_instance()` 才是大头。预热代码其实已经会 `prepare_session`（child + `configure_session_runtime` + `start_interaction`），只是 claim 对不上。

### R3. 首次模型调用前组 44KB system prompt（~1.2–1.9 s）

`latency.prompt`：`system=44083` 字符、`tools=21`、三轮 messages。内容每条首请求几乎一样（同一安装、同一「你好」）。27 条 rail 在 `before_model_call` 里现场 `add_section` / 读文件 / 滤工具。这是 **CPU + 字符串拼接 + 工具 schema 展开**，不是模型。

不可靠砍技能清单或 eager 工具来换时间——效果会掉。正确做法是 **进程内编译一次静态前缀，首条只填易变段**。

### R4. MCP 之后的工作仍串行（指标口径下仍可省）

MCP 虽扣除，但 MCP 期间后面的 runtime config / 附件 / 静态 prompt 刷新完全空转。重叠后 `t_llm_send - t_recv` 变小，MCP 时长不变，**`T_excl_mcp` 下降**，用户墙钟也下降。join 点必须在 `before_invoke` 列出工具之前。

### R5. 观测缺口让 1.2 s 组 prompt 像“黑盒”

`create_instance` 只有总量；除 `ProgressiveToolRail` 外，其它 rail 的 `before_model_call` 没有耗时；prompt 没有按 section 的字符数（规范里写了，当天日志没有）。不补齐就无法守住 2 s，也无法防止以后回潮。

---

## 6. 方案原则

1. **搬离，不阉割**：能在进程启动后、首条 query 前做完的，全部做完。首条只做「认领 + 覆盖 project_dir + 发请求」。
2. **一条模板，请求级覆盖**：进程内最多一个 OfficeClaw 未认领 child（已有 WarmPool 配额）。认领后覆盖 `session_id` / `project_dir`。禁止两个用户/两个 session 共用一个 DeepAgent。
3. **静态前缀只建一次**：身份、技能协议、turbo 指南、eager 工具导航、平台差异等与「本条消息」无关的 section，在 `create_instance` 末尾编好。`before_model_call` 只更新 time / cwd / git / runtime.setting。
4. **失败回退到今天**：预热未就绪、指纹变了、认领冲突 → 走现有同步 `create_instance`，行为与现在一致，只是慢，不能错。
5. **热路径只减不增**：mode 未变则跳过无操作的 `rails_for_mode` 重注册；IO 追踪不得在发 HTTP 前同步 `json.dumps` 整包。
6. **不加新服务**：不引入 Redis、不改 wire 协议、不强制 OfficeClaw 先 `session.create`（可后续优化，但不作为 2 s 的前提）。

---

## 7. 目标架构

```
                    进程启动（无用户）
                            │
                            ▼
              ┌─────────────────────────┐
              │ 一次性：checkpointer     │
              │ config / dotenv 合并     │
              │ OfficeClaw 无项目 root   │  cache_key = (officeclaw, agent, "")
              └────────────┬────────────┘
                           ▼
              ┌─────────────────────────┐
              │ WarmSlot × 1            │  WarmKey = (officeclaw, agent, project="")
              │ session child:          │
              │   create_instance       │
              │   configure_runtime     │  含 rails_for_mode、prompt 静态前缀
              │   不 start_interaction  │  避免 throwaway session 绑死 checkpointer
              └────────────┬────────────┘
                           │
              首条 chat.send (客户端 session_id + 本线程 project_dir)
                           ▼
              adopt: 槽 → _session_adapters[client_sid]
                   overlay project_dir（拒绝之后改绑到别的非空目录）
                   start_interaction(client_sid)     ~60 ms
                   与 MCP 并行：附件 / git 快照 / 易变 prompt 段
                           ▼
              join MCP → before_invoke → 只拼易变段 → llm_send
                           │
                           ▼
              后台再补一个空槽（被 begin_foreground 推迟到本轮结束后）
```

隔离规则（沿用并收紧已有阶段 C）：

- root 不持有 MCP、callback token、checkpoint、用户消息、`project_dir`。
- child 深拷贝 root 的 **config 模板**，不是拷贝 live DeepAgent。
- 未认领槽的 throwaway `session_id` 不得出现在用户可见 session 列表（已有 `.prewarm` marker 机制）。
- 认领后若发现槽已有非空 history（不应发生）→ 丢弃槽，走同步创建。

---

## 8. 分阶段设计与时间预算

以下预算以代表样本 **4145 ms（recv→llm_send 除去 MCP）** 为起点。数字是设计预算，验收以实测 P50/P95 为准。

### 阶段 0 — 观测（不改行为，约 0 ms）

补齐才能证明后面每一刀。

| 日志 | 内容（禁止 query / prompt 正文 / token / MCP env） |
| --- | --- |
| `create_instance` 子阶段 | checkpoint / model / tool_cards / rails / deep_agent / ensure_initialized / packages / skill_turbo |
| 每个 rail `before_invoke` / `before_model_call` | 名称 + ms（已有 ProgressiveToolRail 模式，扩到全量） |
| `latency.prompt` | section 名 + 字符数 + tools 数（规范已写，落地） |
| `latency.adopt` | hit / miss / stale / fallback，不含路径 |
| 指标行 | `recv_to_llm_send_ms`、`mcp_ms`、`excl_mcp_ms` |

同步把 `scripts/analyze_chat_ttft.py` 加上 `excl_mcp` 列。没有阶段 0，禁止靠“感觉”砍 prompt。

### 阶段 1 — 把实例构建搬离首条（预算节省 1.2–1.6 s）

这是达成 2 s 的主杠杆，**不改变工具与 prompt 语义**。

**1a. 进程启动预热 checkpointer + 共享 root**

- AgentServer listen 之后、不阻塞端口：`ensure_persistent_checkpointer()` + 创建 `cache_key=(officeclaw, agent, "")` 的 root。
- 开关：仅 `channel_id=officeclaw` 且 mode=agent。其它渠道保持现有 `(mode, sub_mode, project_dir)` 键。
- 效果：首条不再付 190–320 ms root 税；后续不同 workspace 的对话共用 root 路由器。

**1b. OfficeClaw 专用 WarmKey，不含 project_dir**

- `WarmKey(channel=officeclaw, project_id=default_work, project_dir="", work_mode=work)`。
- `get_agent` 对 officeclaw agent 模式同样忽略 project_dir。
- 全局仍只保留 1 个未认领槽（稳定、不扩内存）；officeclaw 桌面场景下该槽就应该是它（enabled_channels 已是 officeclaw 时自然如此；若与 web 同进程，officeclaw 与 web 分时复用同一配额，用「最近渠道」或显式优先 officeclaw 桌面，避免再出现 web 空槽喂 officeclaw 首条）。

**1c. 预热停在 `create_instance + configure_session_runtime`，不 `start_interaction`**

- 避免 throwaway session_id 写入 checkpointer。
- `configure_session_runtime` 已把 `rails_for_mode` 做完，首条不必再付 46–109 ms 重注册（mode 未变则 no-op）。
- 在 child 就绪时编译 **PromptPrefix**（阶段 2 的输入）。

**1d. 首条 `chat.send` 认领（不改客户端协议）**

现有 claim 绑的是预热自己的 session_id，OfficeClaw 用不上。新增 **adopt**：

```
if channel==officeclaw and session adapter 不存在:
    slot = warm_pool.adopt()   # pop READY，失败则 None
    if slot:
        将 child 挂到 client session_id
        overlay project_dir（已有 child 非空且冲突则拒绝，回退同步创建）
        await start_interaction(client_session_id)
    else:
        现有 _get_or_create_session_adapter() 路径
```

`wait_for_session_prewarm(client_sid)` 对 OfficeClaw 仍基本是空操作，不依赖它。

**1e. 回退与补槽**

- 配置指纹变化、槽损坏、`create_instance` 预热失败：首条走今天的路径，打 `latency.adopt status=fallback`。
- 认领后 `begin_foreground` 结束再补槽，沿用现有「chat 期间不与预热抢 OpenJiuwen 注册表锁」。

阶段 1 单独落地后，预期除去 MCP 约 **2.5–2.9 s**（仍可能略超 2 s）。必须立刻做阶段 2，而不是再加更重的架构。

### 阶段 2 — 首次组 prompt 变成“填空”（预算节省 0.7–1.2 s）

**2a. PromptPrefix（静态）+ Overlay（易变）**

在 child `create_instance` 结束时，把与请求无关的 section 建成只读前缀（字符串或已 add 的 PromptSection 列表）：

- 身份 / 技能协议 / skill turbo 指南 / ProgressiveTool 导航 / 平台与 shell 差异 / 语言输出约束（按当前 preferred_language **只编一种**，与已有阶段 B 一致）

`before_model_call` 只覆盖：

- time、runtime.setting、cwd/project、git_status（会话级缓存，HEAD 文件变化才重跑 git）
- 本请求 `system_prompt`、trusted_dirs

**禁止**：按 token 硬截断、减少 eager 工具、少暴露技能名。前缀内容必须与今天最终发给模型的静态部分 **字节级等价**（允许 section 顺序稳定化，这有利于前缀缓存，不改语义）。

**2b. 工具 schema 只展开一次**

21 个 eager 工具的 JSON schema 在 `create_instance` 后缓存。`before_model_call` 的 filter 只做名单过滤，不再重建 card。MCP 工具在 MCP join 之后 **增量** 并入本轮工具表，不使静态 21 个失效。

**2c. 把诊断 IO 移出发送前**

当前 `Model.stream` 包装里先 `log_stream_input`（DEBUG 下整包 `json.dumps`）再打 `pre_llm`。本机 full.log 已是 DEBUG，代表样本 `pre_llm → llm_send` 有 **478 ms**。

规则：

- 先发起提供方调用（或至少先完成参数对象），再异步写 trace。
- INFO 的 `latency.prompt` 只记计数，同步也可以，必须 O(section 数) 而不是拷贝 44KB 正文。

热路径同样受益，是性能正收益。

**2d. `rails_for_mode` 幂等快路径**

`_last_mode == mode` 且 rail 已挂上 → 直接 return。今天冷启动刚 `configure_session_runtime` 过再走一遍 46–109 ms，属于浪费。热路径 mode 不变时同样变快。

### 阶段 3 — 与 MCP 重叠（预算再省 0.3–0.5 s 的 T_excl_mcp）

MCP 仍必须在 `before_invoke` 前 join（工具列表正确性）。可与 MCP **并行** 且不读 MCP 工具的：

- `start_interaction`（若阶段 1 放在 adopt 后，可与 MCP 并行）
- prompt 附件 sync
- git 快照（会话级）
- 易变 overlay 中不依赖 MCP 的部分

实现：`asyncio.gather(mcp_task, local_prep_task)`，然后 `bind_active_office_claw_mcp_tools`。

取消或异常：沿用现有 `cleanup_request_scoped_office_claw_mcp`，局部 prep 失败不得留下半注册 MCP。

### 预算汇总（代表样本）

| 项 | 现在 (ms) | 之后 (ms) | 手段 |
| --- | ---: | ---: | --- |
| recv → 进入 child | 661 | 150–220 | 无 root 创建；checkpointer 已就绪 |
| child create_instance | 985 | 0（adopt hit） | 阶段 1 |
| start_interaction | 62 | 60（可与 MCP 重叠） | 阶段 1d / 3 |
| MCP | 797 | 797（扣除；可重叠） | 阶段 3 |
| runtime + 附件 + send | 503 | 80–150 | 1c/2d + 重叠 |
| 组 prompt invoke→model | 1229 | 250–450 | 阶段 2a/2b |
| pre_llm → llm_send | 478 | 80–150 | 阶段 2c |
| **除去 MCP 合计** | **~4145** | **~700–1200** | 留出余量给机器抖动 |

miss 回退：与现在同阶（3.7–5.7 s），允许，但 P95 应多数 hit。进程起来后预热通常 < 2 s（当天 child 就是 1–1.7 s），用户 22 s 后才发首条，hit 应是常态。

---

## 9. 明确不做（防回潮、防效果回退）

| 不做 | 原因 |
| --- | --- |
| 缩小 eager 工具集 / 隐藏技能 | 效果回退 |
| 硬 token budget 截断 prompt | 先有 section 字符数，再单独立项 |
| 两个 session 共用一个 DeepAgent | 工具注册表、checkpoint、MCP 串扰 |
| deepcopy 正在跑的 agent | 不稳定 |
| 预热时 `start_interaction(throwaway_id)` | checkpointer 脏绑定，认领复杂 |
| 关掉 `JIUWENSWARM_AGENT_PREWARM` 当“优化” | 现有能力要用对，不是关掉 |
| 绕过 OpenJiuwen 初始化锁并发建两个 DeepAgent | 已有注释：全局 resource_mgr 会打坏 |
| 非 AscendAffinity 开 KV affinity | 已有 fail-closed，保持 |
| 把 MCP 算进 2 s 或假造 MCP 超时 | 指标作弊 |
| 改 OfficeClaw 必须先 `session.create` 才能聊 | 能 adopt 就不必改协议；协议改可后续做，非本方案门槛 |
| 在热路径加锁等预热 | 第二轮不得变慢 |

---

## 10. 验收

### 10.1 性能

固定场景：干净进程、officeclaw、新 session、query「你好」、预热完成后再发（日志 `adopt hit`）。

| 项 | 标准 |
| --- | --- |
| `T_excl_mcp` P50 | ≤ 2000 ms |
| `T_excl_mcp` P95 | ≤ 2500 ms（同机抖动；不得再回到 3.5 s+） |
| adopt miss 回退 | 功能正确；不计入 P50 达标，但要有告警日志 |
| 同 session 第二轮 `recv→llm_send` 除去 MCP | ≤ 改前（允许持平，不允许稳定变差） |

测 5 次冷启动。不以模型 TTFT 判断本方案。

### 10.2 效果与隔离

- 首轮工具名集合与改前一致（21 eager + 本轮 MCP 工具）。
- 静态 prompt 前缀与改前对应 section 文本一致（测试比对规范化后的 section 字典，不比无关键顺序的无关空白）。
- 两个 project_dir 的请求：一个 root、两个 child；MCP invocation / checkpoint / `_project_dir` 不共享。
- 预热槽认领失败不影响正确性。

### 10.3 稳定性

- 已有 agent manager / warm pool / OfficeClaw MCP / ProgressiveToolRail 单测继续过。
- 新增：adopt hit/miss/conflict、共享 root 开关关闭时键行为不变、PromptPrefix 与 overlay 合并结果。

---

## 11. 实施顺序（逻辑依赖）

```
阶段 0 观测
    → 阶段 1a 共享 root + 启动 checkpointer
    → 阶段 1b/1c 无项目 WarmKey + 预热 child（不 start_interaction）
    → 阶段 1d adopt
    → 阶段 2a/2b PromptPrefix（预热结束时编译，首条才能用上）
    → 阶段 2c/2d IO 与 rails 快路径
    → 阶段 3 MCP 并行
```

1d 没有 1c 会认领到半初始化槽，禁止颠倒。2a 没有 1 仍然值得做（miss 回退也会快一点），但单靠 2 到不了 2 s。

---

## 12. 风险与回退

| 风险 | 缓解 |
| --- | --- |
| adopt 时 project_dir overlay 漏改 workspace/cwd | overlay 集中在现有 `configure_session_runtime`；单测两个目录 |
| 静态前缀漏掉某 rail，效果变差 | 阶段 0 section 清单金样；CI 对比 section 名与字符数 |
| DEBUG 环境看起来“没变快” | 2c 后即使 DEBUG 也不挡发送；验收同时看 INFO 指标行 |
| 预热占用 CPU 影响启动动画 | 预热在 listen 之后后台跑；不阻塞端口；失败忽略 |
| 与已有 web 预热抢唯一槽 | 桌面 officeclaw 进程通常单渠道；同进程则槽优先最近启用渠道，文档化 |

总回退：环境开关关闭共享 root / adopt 后，路径与今天一致。

---

## 13. 结论

冷启动预 LLM 到不了 2 s，不是因为模型，也不是因为 MCP（已扣除仍有 3.7–5.7 s），而是：

1. OfficeClaw **绕开了** 按 `session.create` + `project_dir` 设计的预热；
2. 每条新对话一个 **唯一 workspace**，把 root 缓存和 WarmKey 全部打 miss；
3. 首条在临界路径上现场 `create_instance`（~1 s）并现场拼接 **44KB** 几乎不变的 system prompt（~1.2 s）。

综合最优且不降效果的做法只有一条主线：**启动后准备好一个无项目 child 模板，首条认领并覆盖目录；prompt 静态前缀只编一次；MCP 与本地准备重叠。** 不引入新系统、不砍工具、不截断 prompt、热路径只更快。按阶段 1+2+3 的预算，除去 MCP 后 **700–1200 ms** 是合理设计点，2.0 s 目标带抖动余量。
