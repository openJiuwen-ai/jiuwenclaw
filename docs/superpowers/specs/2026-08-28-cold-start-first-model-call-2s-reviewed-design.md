# 冷启动首个模型调用（排除 MCP）2 秒可行性方案

> 文档状态：设计评审稿，不修改代码。  
> 分析日期：2026-08-28。  
> 适用范围：服务进程启动后，`officeclaw` 渠道的第一个用户 `chat.send/query`，从 AgentServer 接收请求到进入底层模型 `stream/invoke` 调用。  
> 核心结论：目标可达，但不能只做一种优化。推荐采用“精确会话主动准备 + 不可变蓝图缓存 + 请求增量装配”的组合方案。不得复用跨会话的 live DeepAgent，不缩减提示词、工具、技能、权限或记忆能力。

---

## 1. 执行摘要

日志中最早且链路完整的冷启动请求为：

- `request_id=549df18e-0177-4b49-9ce2-8f221d4a904e`
- `session_id=officeclaw_2466d06d28ef8db1723f98ae`
- `channel=officeclaw`
- `mode=agent.plan`
- `model=glm-5.2`

关键时间点：

```text
15:20:36.250  stage=0 recv，AgentServer 已接收 chat.send/query
15:20:37.840  stage=1 init，会话级 DeepAgent 主初始化完成
15:20:37.916  start_interaction 完成
15:20:38.309  请求级 OfficeClaw MCP 注册完成
15:20:38.322  stage=2 mcp
15:20:38.793  MemoryRail 因缺 embedding 配置连续失败两次
15:20:39.048  ProgressiveToolRail：67 个工具，22 eager，46 deferred
15:20:40.185  模型可见工具过滤为 21 个
15:20:40.450  stage=3 pre_llm，立即进入底层 Model.stream/invoke
15:20:46.747  第一段模型输出到达
```

由此得到：

- `recv → pre_llm = 4.200 s`。
- 历史日志没有 MCP 精确开始点。即使把 `start_interaction` 完成后的全部 `37.916 → 38.322 = 0.406 s` 都视为 MCP 并扣除，本地非 MCP 仍至少为 `4.200 - 0.406 = 3.794 s`。
- 因此 2 秒目标至少需要稳定节省 `1.794 s`。考虑机器抖动和配置差异，工程设计值不能正好卡 2 秒，建议把优化后内部预算设为 `≤1.6 s`，留 0.4 秒保护余量。
- `pre_llm → 第一段模型输出 = 6.297 s`，提供方上报 `ttft_ms=6406`。这属于模型侧 TTFT，不纳入本方案的 2 秒目标，但应继续独立观测。

同日多个进程冷启动首请求的粗粒度数据也表明这不是偶发问题。使用现有 `stage0→stage3 - stage1→stage2` 作为“过度扣除 MCP”的乐观代理值，7 个样本分别约为 `3.718、4.330、5.728、3.810、3.667、4.903、3.668 s`，中位数约 `3.810 s`，平均约 `4.261 s`。真实非 MCP 值只会相同或更高，不能以单次最好值验收。

综合代码与日志，根因不是 A2UI，也不是模型调用本身，而是两段串行冷工作：

1. 首条请求临界路径上创建精确 session child/DeepAgent，约占 `1.5–2.6 s` 的 `recv→init` 区间中的主体。
2. MCP 后首次执行 runtime rails、工具目录检查、工具过滤及长提示词/工具 schema 装配，首样本为 `2.128 s`。

最优方案不是“共享一个 Agent”或“删工具”，而是：

1. 上游一旦确定 `session_id + mode + project_dir`，异步触发精确会话准备，把会话级初始化搬到用户 query 之前。
2. 在进程内构建版本化、只读的 `AgentBlueprint/FirstTurnBlueprint`，缓存完全静态且可校验的配置、提示词片段和基础工具 schema；会话与请求只绑定增量数据。
3. 对工具目录使用 generation/version 判断代替每轮全量重扫；请求级 MCP 以 delta overlay 合并，保持最终工具集合、schema、顺序和权限完全一致。
4. 对缺失 embedding 的合法配置直接快速跳过 MemoryRail；补齐精确分段观测、模型请求摘要校验和自动回退。

这套设计不增加外部服务，不改变持久化格式，不缩减模型上下文，不共享会话可变状态；预热或缓存任何异常均回退现有路径。

---

## 2. 指标定义与边界

### 2.1 唯一主指标

```text
T_cold_local_non_mcp
  = T_model_call_boundary - T_query_received - Σ(T_mcp_critical_path_exact)
```

定义如下：

- `T_query_received`：请求完成解析并进入 `dispatch_parsed_request` 的单调时钟时间；现有对应日志为 `[latency] stage=0 name=recv`。
- `T_model_call_boundary`：调用底层模型客户端 `original_stream/original_invoke` 之前的单调时钟时间；现有 `[latency] stage=3 name=pre_llm` 位于该边界。
- `T_mcp_critical_path_exact`：只统计当前请求关键路径上的 MCP discover/list/register/bind 等精确 span。不能再用 `stage1→stage2` 整段代替，因为其中包含 `start_interaction`、调度和日志开销。

本指标明确不包括：

- MCP 本身的耗时，但仍必须单独报告；
- 模型服务排队、网络、prefill 和模型首 token；
- relay-claw 在 AgentServer 收到 query 之前的前端、路由或本地准备耗时。

### 2.2 达标定义

不能只用一次测试或平均值宣称达标。建议验收门槛为：

- 参考环境完整进程重启 50 次，首个真实 query 的 `T_cold_local_non_mcp`：`P95 ≤ 2.0 s`。
- 工程目标：`P95 ≤ 1.6 s`，使版本、磁盘和调度抖动后仍满足 2 秒外部要求。
- 50 次中不得出现 `>2.0 s` 的稳定性离群由优化新增；若业务把 2 秒定义为硬上限，则发布门槛应同时要求 `max ≤2.0 s`，并固定参考机器与负载。
- 预热 miss/失败必须功能正确并走旧路径，但需要单列 miss 率；生产目标应使“服务已 ready 且用户正常创建线程后发首条消息”的精确准备命中率 `≥99%`。

### 2.3 效果与稳定性不下降的定义

以下是与性能指标同等优先级的硬约束：

- 发给模型的 messages、system prompt 各 section、可见工具名称、工具 schema、工具顺序和 MCP 增量在基线与优化路径上等价；能做到字节级一致的内容必须字节级一致。
- 不减少 eager 工具，不隐藏技能，不截断 prompt，不关闭 rails，不降低记忆、权限、HITL、checkpoint 或恢复能力。
- session、project_dir、checkpoint、MCP env、callback token、请求 metadata 和用户消息不得跨会话共享。
- 同 session 第二轮和并发吞吐不得变慢；服务 listen/ready 时间不得被预热阻塞。
- 优化路径异常必须 fail-open 到现有正确路径，而不是向用户返回初始化失败。

---

## 3. 日志事实与首请求分段

### 3.1 首个完整样本

以 `15:20:36.250` 为 0 点：

```text
0 ms       AgentServer recv
127 ms     前台请求到来，后台预热记录 foreground=1、pending=0、cancelled=0
200 ms     AgentManager 开始创建 officeclaw root
466 ms     root facade 创建完成
598 ms     stream consumer 启动
684 ms     run_stream_task 启动
790 ms     session child 开始密集初始化
1,590 ms   stage=1 init
1,666 ms   start_interaction 完成
2,059 ms   OfficeClaw MCP 注册完成
2,072 ms   stage=2 mcp
2,543 ms   MemoryRail 无配置失败
2,798 ms   request runtime/附件/rails 准备基本完成
2,798 ms   到 4,200 ms 之间进入 runner、遍历/过滤工具、拼装模型输入
4,200 ms   stage=3 pre_llm
```

有三个需要特别避免的误判：

1. `stage1→stage2=482 ms` 不等于 MCP。日志可见其中至少 `76 ms` 是 `start_interaction`，所以用 482 ms 全扣会虚假降低非 MCP 基线。
2. `39.048→40.185=1.137 s` 不能全部归因给 `ProgressiveToolRail`。这两个日志只是包围了 OpenJiuwen runner 中的一段工作，期间可能包含多个 rail、上下文和模型参数装配。需要细化 span 后再决定具体代码落点。
3. A2UI 在 `officeclaw` 上明确 `enabled=False, injected=False`，且从 A2UI 决策到 `pre_llm` 仅 224 ms，不是主瓶颈。

### 3.2 模型请求规模

首样本模型输入为 33,233 tokens，缓存 token 13,312，约 40.1%。工具目录日志显示：

- 总工具 67；
- eager 22；
- deferred 46；
- 最终模型可见 21。

这说明首轮装配的数据量不小，静态内容复用有明显价值；但不能把“减少工具或 prompt”当成本方案手段，因为那会改变模型能力和效果。

### 3.3 启动窗口存在但没有准备精确会话

服务在 `15:20:31` 左右已经完成 tenant/agent manager 外壳与 skill index warmup，skill index 本身约 47.5 ms。第一条 query 到 `15:20:36.250` 才到达，存在约 4 秒以上的可用窗口，但日志显示没有与该 `officeclaw session_id + project_dir` 对应的 pending/ready 预热。

因此问题并非单纯“用户发得太快”，而是当前预热键与 OfficeClaw 首请求身份无法可靠衔接。

---

## 4. 代码链路与根因

### 4.1 请求入口与 AgentManager

关键路径为：

```text
server/pipeline.py::dispatch_parsed_request
  → server/handlers/_default.py::_handle_stream_impl
  → server/runtime/tenant_agent_pool.py::process_message_stream
  → server/runtime/agent_manager.py::process_message_stream
  → AgentManager.get_agent(mode, sub_mode, project_dir)
  → JiuWenSwarm facade / DeepAdapter root
```

代码事实：

- `pipeline.py` 在请求解析后记录 stage 0。
- `AgentManager.get_agent` 的缓存 key 包含 `project_dir`。OfficeClaw 每个线程使用不同 workspace 时，新线程必然得到不同 key。
- root adapter 已有正确的轻量化方向：root 仅作为 router/template，不创建自己的 live DeepAgent；真正可运行实例属于 session child。

结论：仅优化 TenantAgentPool 或复用 AgentManager 外壳收益有限；大头在精确 session child 以及首轮模型输入装配。

### 4.2 会话级 DeepAgent 冷创建

`interface_deep.py::_get_or_create_session_adapter` 已按 session 做锁和 single-flight，并为新 session 创建 child、复制配置、调用 `create_instance`、执行 `start_interaction`，最后缓存 child。

session child 的 `create_instance` 串行包含：

- checkpoint/config/env 绑定；
- model client 与 A2X 初始化；
- tool cards、skill toolkits、rails；
- `create_deep_agent` 与 `ensure_initialized`；
- cron/tool/resource 注册；
- active packages 和 session runtime 准备。

这些工作大多与本次 query 文本无关，却在第一个 query 到达后才发生。首样本 `recv→stage1` 为 1.590 秒，其他冷启动样本可到 2.609 秒，是第一主因。

### 4.3 现有 WarmPool 为什么没有解决 OfficeClaw 首条

`agent_warm_pool.py` 已提供有价值的安全基础：

- `WarmKey` 包含 `channel_id/project_id/project_dir/work_mode`；
- 全局初始化有互斥保护，因为 OpenJiuwen resource manager/registry 是进程级可变状态；
- pool 默认并发和 ready slot 都受限；
- 前台 chat 到来会暂停或取消无关后台预热；
- `_prepare` 最终可调用 `prepare_session` 创建 session child。

但当前 OfficeClaw 样本直接发送带外部 session_id 的 `chat.send`，AgentServer 日志中在 query 前没有对应 `session.create`。现有 claim 机制以预热自身生成或已登记的 session/key 为中心，且 key 包含每线程唯一 `project_dir`，所以通用槽不能安全地自动变成这个精确 session。

这里不能采用“先做一个任意 session 的 live DeepAgent，首条再改 session_id/project_dir”的方式。那会引入 checkpoint、interaction、cwd、rail 状态和资源注册重绑定风险，违反隔离和稳定性要求。

### 4.4 请求级 MCP 之后的本地工作

root 路径先创建/获取 session child，再注册请求级 OfficeClaw MCP，然后 child 执行 `_update_runtime_config` 并进入 runner。

`_apply_runtime_config_stages` 会依次处理 cwd、rail setters、runtime state、mode rails、用户交互、工具、request metadata 等。随后 OpenJiuwen 执行 rails、构建系统提示词和工具请求。

首样本 `stage2→stage3=2.128 s`，这是第二主因。当前日志不足以把它精确分配到某一个 rail，因此第一阶段必须先补全观测，后续优化必须针对测到的稳定大项。

### 4.5 两个已确认的局部问题

1. MemoryRail 在 embedding 的 `api_key/base_url/model` 不完整时先记录“无可用配置”，随后仍构造 `EmbeddingConfig`，触发第二次 Pydantic warning。正确的快速失败应在缺失必需项后直接返回 `None`。有效 embedding 配置路径完全不变。
2. ProgressiveToolRail 在 `before_invoke` 和 `before_model_call` 都需要检查工具目录是否变化，并通过名称/id/数量判断 stale。请求级 MCP 又会改变 live catalog。缺少 catalog generation 时，全量读取、比较和重建容易落在首次关键路径。

这两项值得修复，但单独不足以节省 1.794 秒，不能作为唯一方案。

### 4.6 relay-claw 已有前置条件

relay-claw 已能在真正发送 `chat.send` 前确定：

- session_id：优先使用已有 session，否则可由 `userId + agentId + threadId` 稳定计算；
- mode/team/target agent；
- `workingDirectory`，最终作为 `params.project_dir` 发送；
- model_name 和请求级 MCP 字段。

代码还已有 thread-create/catalog prewarm，但它只同步共享 catalog，不会创建 AgentServer 内的精确 session child。正常单 Agent 路径也不强制先调用 `session.create`；该方法主要出现在新 team session 创建流程。

这说明最简洁的架构切入点是新增“精确 session prepare 意图”，而不是猜测或重绑一个通用 live Agent。

---

## 5. 推荐目标架构

### 5.1 总体结构

```text
relay-claw：线程/工作区/会话身份已确定
        │
        ├─ fire-and-forget session.prepare（不带 query，不带请求级 MCP）
        │          │
        │          ▼
        │   AgentServer PrepareCoordinator
        │     key=(tenant, agent, session, mode, normalized_project_dir, config_fingerprint)
        │          │
        │          ├─ 取得/创建 root router
        │          ├─ 从只读 AgentBlueprint 实例化精确 SessionRuntime
        │          ├─ prepare_session + start_interaction
        │          └─ READY / FAILED / STALE，可回收
        │
用户 chat.send 到达
        │
        ├─ 同 key READY：直接取得精确 child
        ├─ 同 key WARMING：提升优先级并等待同一个 single-flight
        └─ MISS/FAILED/STALE：走现有同步路径
                   │
                   ▼
          请求级 MCP delta overlay（单独计时并从 SLO 扣除）
                   │
                   ▼
          RequestOverlay + cached static prompt/tool schemas
                   │
                   ▼
          payload parity check / Model.stream|invoke
```

架构分成三层状态：

1. `AgentBlueprint`：进程内、版本化、不可变、可跨会话共享。
2. `SessionRuntime`：精确绑定 session_id/project_dir/checkpoint/cwd，绝不跨会话共享。
3. `RequestOverlay`：只活到单次请求结束，承载 query、附件、请求 metadata、MCP invocation/env 和本轮 prompt override。

这种分层与当前“root router + session child”的方向一致，不需要引入新的远程缓存或重写整体 Agent 架构。

### 5.2 方案 A：精确会话主动准备

这是最大收益、最低语义风险的主方案。

#### 触发时机

优先在 relay-claw 完成线程 workspace 创建、且已经能得到稳定 session_id 后立即触发。若某条路径直到 invoke 才生成 session_id，也应在 `resolveRelayClawSessionId` 与 `workingDirectory` 就绪后立刻触发，并与 sidecar connect、catalog sync、MCP 字段读取并行。

准备请求只包含必要身份：

- tenant/service/agent；
- session_id；
- mode/submode/team/target agent；
- normalized project_dir；
- model/config revision；
- 可选 create_token，用于网络重试幂等。

禁止携带 query、用户 prompt 正文、请求级 MCP env/callback token。

#### 协议选择

推荐新增语义明确、幂等的 `session.prepare` 控制请求，而不是偷偷改变 `session.create`：

- `session.create` 表示由 AgentServer 分配/登记会话，现有 team 流程依赖其返回 session_id；
- OfficeClaw 已有外部稳定 session_id；
- `session.prepare` 只承诺 best-effort 提前构建，不改变业务会话所有权和持久化语义。

若必须减少协议面，也可扩展现有 session control handler 接受外部 session_id，但仍应保持单独的 prepare 语义和指标。

#### 幂等与并发

`PrepareKey` 必须包含：

```text
tenant/service/agent
session_id
mode/submode/team/target_agent
normalized_project_dir
model/provider
config_fingerprint
skill_catalog_generation
permission_policy_generation
```

同 key 只允许一个 task；并发 prepare/chat 不创建两份 child。状态机建议为：

```text
ABSENT → WARMING → READY → CLAIMED
                  ↘ FAILED
READY/FAILED → STALE（配置、目录或权限代际变化）
```

chat 到达时：

- READY：O(1) claim；
- WARMING：前台提升优先级，等待同一个 task，不取消后重建；
- STALE/FAILED/MISS：直接执行现有 `_get_or_create_session_adapter`；
- prepare 超时或 relay 断连：不影响 chat 正确性。

#### 资源约束

- 每 tenant/user 最多一个未使用 READY 和一个 WARMING；全局仍遵守 OpenJiuwen registry 初始化锁。
- TTL 到期只回收尚未 claim、没有 history、没有在途 task 的精确 child。
- foreground chat 只取消无关 key 的预热；相同 key 必须“提升并等待”，否则会把已经做过的工作浪费掉。
- listen/health/ready 不能等待 prepare；所有准备均在服务可用后后台执行。

#### 预期收益

首样本在 query 前存在 4 秒以上窗口，session child 初始化约 1.5 秒量级，正常情况下可以完全搬离请求关键路径。保守预算把 `recv→可用精确 child` 压到 `≤250 ms`。

### 5.3 方案 B：不可变 Agent/FirstTurn Blueprint

精确预热解决 `recv→init`，但不能单独保证 2 秒，因为 `stage2→pre_llm` 仍有 2.128 秒。需要在不改变最终模型输入的前提下消除重复构建。

#### 允许缓存的内容

- 解析和校验后的静态 agent config；
- 静态 system prompt section 的原始顺序、文本与序列化片段；
- 基础工具名称、描述、JSON schema、能力卡的不可变表示；
- rail 构造元数据与静态语言模板；
- skill catalog 的只读索引和 generation；
- 模型/provider 的不可变客户端配置；只有在线程安全经过验证后才共享实际 client。

#### 绝对不能缓存或跨会话共享的内容

- live DeepAgent、rail 实例、ability manager 的可变实例；
- session_id、project_dir/cwd、checkpoint、history、interaction；
- 用户 query、附件、动态 system prompt override；
- MCP 工具实例、MCP env、invocation_id、callback token；
- permission/HITL 状态、request metadata、trace/span；
- 任何可变的 cron/session task 状态。

#### Blueprint key 与失效

key 至少包含：

```text
tenant/service/agent/mode/submode/team/target_agent
model/provider/language/platform
agent config version
skill/tool catalog generation
permission policy generation
prompt template version
相关 secret 只使用不可逆 fingerprint，不记录明文
```

配置 reload 采用版本化原子切换：在途请求继续使用旧的只读 snapshot，新请求使用新 generation；旧 snapshot 在引用归零后回收。不能原地修改共享 blueprint。

#### 请求装配

最终模型输入由以下两部分合成：

```text
immutable FirstTurnBlueprint
+ SessionOverlay(session_id, project_dir, checkpoint-derived state, cwd/git state)
+ RequestOverlay(query, attachments, metadata, prompt override, MCP delta)
```

静态 section 和基础 21 个可见工具 schema 可预序列化。动态 section 按原顺序插入原位置，不做重排、删减或摘要替换。MCP 工具在请求级 register 完成后以 delta 追加，再经过与基线相同的权限和可见性过滤。

#### 等价性门禁

优化开关上线前必须双跑装配器：同一输入分别产生 legacy 与 optimized payload，记录不含正文的：

- messages 结构 hash；
- 每个 section 的 name/length/hash/order；
- visible tool name/schema hash/order；
- MCP delta name/schema hash；
- token count。

任一不一致立即使用 legacy payload，并计数告警。只有黄金场景和 canary 长期一致后才能取消 shadow 双算。

### 5.4 方案 C：版本化工具目录与请求 delta

当前 ProgressiveToolRail 为防止 catalog 变更会读取 live tools 并比较数量、名称和 id。推荐在资源注册层维护单调递增 `catalog_generation`：

- 基础工具、skill、cron、权限导致目录变化时 generation 增加；
- session child 记录上次 generation；
- generation 未变时直接复用已验证的基础目录和序列化 schema；
- 请求级 MCP 不修改基础 generation，而是形成 `RequestToolDelta`；请求结束后原有 cleanup 语义不变；
- generation 变化时走一次完整重建和 parity 校验。

这样消除每轮为“确认没变化”而全量列举/比较 67 个工具的工作，同时不会漏掉动态工具。

不能通过把 deferred 工具彻底不初始化来直接达标。若未来要做 deferred live materialization，必须证明首个工具调用时延、错误率和能力一致；它不是第一阶段必要项。

### 5.5 方案 D：局部安全快路径

#### MemoryRail 缺配置快速返回

当 embedding 必需三元组不完整时：

- 记录一次结构化 `memory_rail=disabled_missing_config`；
- 在构造 Pydantic `EmbeddingConfig` 前返回 `None`；
- 有效配置路径、记忆召回和效果完全不变。

这主要改善稳定性、日志噪声和少量异常构造开销，不把它夸大为秒级收益。

#### 已初始化 runtime 的幂等 setter

对 `mode/language/project/config generation` 均未变化的 setter，允许经过显式状态版本检查后跳过重复注册；一旦任一 generation 变化必须走完整路径。不得只凭对象存在就跳过。

#### 诊断日志不阻塞模型调用

同步关键路径只写计数、耗时、hash 和长度；不得序列化/打印 33k token 请求正文、MCP env 或凭证。详细 trace 使用有界队列异步写入，队列满时丢弃诊断事件而不是阻塞模型调用。安全审计日志不得异步丢失，此类日志应保留原同步语义并确保内容很小。

### 5.6 MCP 的处理

MCP 不计入 2 秒，但必须严谨处理：

- 增加 `mcp.discover/list/register/bind/cleanup` 精确单调时钟 span；
- 指标只扣实际落在关键路径上的 span union，不能重复扣除并行部分；
- 若 relay 在 prepare 时已知道静态 MCP server 描述，可预取只读描述，但请求级 auth/env/invocation/tool instance 仍必须在请求内创建；
- MCP 失败、超时、清理和权限语义保持不变；
- 本地 SessionOverlay 准备可与 MCP 并行，但在最终 visible tools 计算前必须 join。

---

## 6. 目标时延预算

以优化后命中精确准备且 blueprint generation 有效为正常首请求路径，建议预算如下：

```text
请求接收、扩展钩子、tenant/manager/root 查找          ≤ 120 ms
精确 prepare claim / READY 校验                       ≤ 180 ms
session/request overlay、runtime 幂等更新              ≤ 250 ms
静态 prompt/tool schema 复用 + 动态 payload 合成       ≤ 600 ms
模型客户端边界前校验、追踪与调度                      ≤ 180 ms
不可分配抖动保护                                      ≤ 270 ms
--------------------------------------------------------------
内部工程预算                                         ≤ 1,600 ms
外部验收上限                                         ≤ 2,000 ms
```

与首样本对比，收益来源应为：

- 精确会话准备搬离 `约 1.3–1.8 s`；
- 静态 prompt/tool/cross-rail 装配复用节省 `约 0.7–1.2 s`；
- generation 快路径、MemoryRail 和小型日志优化节省 `约 0.1–0.3 s`。

这些是设计预算，不是已实测承诺。阶段 0 观测会把 `stage2→pre_llm` 的真实大项拆开；如果 blueprint 复用实测不足，应继续优化测到的组装大项，而不是牺牲工具或 prompt。

---

## 7. 可观测性设计

### 7.1 统一 trace

每个阶段使用 `request_id + session_hash + prepare_key_hash + config_generation` 关联，禁止记录原始 query、prompt、凭证和 MCP env。

新增单调时钟 span：

```text
ingress.parse / extension_hooks / tenant_resolve / manager_get
root.get_or_create
session_prepare.lookup / wait / create / start_interaction / claim
session_child.create_instance 的 checkpoint/model/a2x/tools/rails/deep_agent/packages 子阶段
mcp.discover / list / register / bind / cleanup
runtime_config 每个 stage
每个 rail.before_invoke / rail.before_model_call
tool_catalog.snapshot / stale_check / filter / schema_serialize
prompt.static / prompt.dynamic / prompt.merge / payload.serialize
model.call_boundary
```

### 7.2 必报指标

- `cold_local_non_mcp_ms`、`recv_to_model_call_ms`、`mcp_critical_path_ms`；
- prepare hit/warming-wait/miss/stale/failure/cancel；
- blueprint hit/miss/rebuild/parity_fallback；
- tool/prompt parity mismatch；
- first model request token、section、tool 数量与 hash；
- RSS、CPU、event-loop lag、进程 ready 时间、第二轮时延、并发吞吐；
- checkpoint/MCP/permission 错误率和首个工具调用时延。

### 7.3 当前源码与运行日志版本差异

当前 checkout 中部分路径已有更细的计时或行号变化，而样本日志仍显示旧行号。实施和验收前必须记录 build commit/version，确认运行二进制确实包含预期 instrumentation；否则源码推断与日志不可直接一一对应。

---

## 8. 分阶段落地计划

### 阶段 0：只补观测与基准，不改变行为

交付：

- 精确 MCP span；
- session child、runtime stages、每个 rail、prompt/tool/payload 的分段；
- 统一分析脚本，以单调时钟计算；
- 50 次完整重启基线和 5 类黄金请求 payload 快照。

退出条件：`recv→pre_llm` 的 95% 时间能归属到明确 span，span 重叠/空洞均可解释；历史粗扣 MCP 只保留为兼容字段，不用于验收。

### 阶段 1：精确 session.prepare

交付：

- relay-claw 发起幂等精确准备；
- AgentServer PrepareCoordinator 与现有 `_get_or_create_session_adapter/prepare_session` single-flight 对接；
- READY/WARMING/MISS/STALE 回退；
- TTL、配额、前台优先级和 shutdown 清理。

退出条件：

- 50 次正常首请求 prepare hit ≥99%；
- `recv→session ready` P95 ≤250 ms；
- 两个并发 session/project 无状态串扰；
- miss/failure 路径与当前功能一致。

### 阶段 2：Blueprint 与 payload parity

交付：

- 不可变 AgentBlueprint/FirstTurnBlueprint；
- SessionOverlay/RequestOverlay；
- 静态 prompt section 与基础工具 schema 预序列化；
- legacy/optimized shadow 双算和 hash parity 自动回退。

退出条件：

- 黄金场景和 canary 的 payload parity 100%；
- `stage2→model call` P95 满足分配预算；
- 质量、首工具调用、第二轮、吞吐无退化。

### 阶段 3：catalog generation 与局部快路径

交付：

- 基础 catalog generation + request MCP delta；
- MemoryRail 缺配置快速返回；
- versioned runtime setter no-op；
- 非审计诊断异步小消息。

只在阶段 0 证明这些路径是稳定大项后实施，避免为几十毫秒引入不必要复杂度。

### 阶段 4：灰度与收敛

按 `1% → 5% → 25% → 100%` 灰度。每阶段至少覆盖完整重启、配置 reload、两个并发项目和 MCP 故障。任何 payload parity、隔离、权限、checkpoint 或错误率异常自动关闭对应优化开关，回退 legacy。

---

## 9. 验证矩阵

### 9.1 性能基准

固定参考环境和数据：

- 每次真正重启 AgentServer，清除仅进程内缓存，不删除用户持久数据；
- 使用固定 `officeclaw` 新 session、新 workspace、相同 mode/model；
- 至少包含一个约 33k input tokens、67/22/46 工具规模的代表请求；
- 使用立即记录到达时间的 mock model 测本地边界，避免提供方波动污染；再用真实模型单独报告端到端；
- MCP disabled 场景与固定延时 MCP 场景分别测试，验证精确扣除和并行 span union；
- 50 次完整冷启动，报告 P50/P95/P99/max，不只报告平均值。

### 9.2 效果与等价

- 中文/英文、agent/plan/team、target_agent、带/不带 prompt override；
- embedding 有效、缺失、配置 reload；
- MCP 0/1/N server、动态工具变化、权限隐藏；
- 附件、git workspace、cron、skill catalog 变化；
- 对每种场景比较 section 顺序/hash、messages、tool schema/order、token count；
- 使用现有质量集比较任务成功率、工具选择准确率、回答质量，不允许统计显著下降。

### 9.3 隔离与稳定性

- 两个用户、两个 tenant、两个 session、两个 project_dir 并发 prepare/chat；
- 相同 session 不同 project_dir 必须拒绝旧 runtime 复用并走安全重建；
- prepare 中途取消、超时、进程 shutdown；
- config/permission/skill generation 在 prepare 中途变化；
- MCP register 后失败及 cleanup；
- checkpoint 恢复、interrupt/resume、team reset；
- OpenJiuwen 全局 registry 锁下不出现并发破坏；
- RSS 增量受一个精确 session runtime 上限约束，TTL 后可回收且无泄漏。

建议非回归门槛：

- 第二轮 `recv→model call` P95 不劣于基线；
- 吞吐、CPU、event-loop lag、错误率不出现统计显著下降；
- 首个工具调用 P95 不劣于基线；
- 服务 ready 时间不增加；
- 质量与工具可用集合 100% 等价，权限/隔离测试 100% 通过。

---

## 10. 风险与控制

### 风险 1：预热准备的是错误配置或错误 workspace

控制：PrepareKey 包含 normalized project_dir 和所有配置 generation；chat claim 时再次校验。任何不一致视为 STALE，绝不覆盖 live child 的目录。

### 风险 2：跨 session 共享可变 Agent 导致串数据

控制：只共享 immutable blueprint；SessionRuntime 从创建起就绑定最终 session_id/project_dir。禁止“通用 live child 改名认领”。

### 风险 3：静态缓存遗漏配置变化，效果悄然下降

控制：版本化 key、原子 snapshot、shadow parity、自动 legacy fallback。配置来源都必须进入 fingerprint 或 generation。

### 风险 4：后台预热抢 CPU/全局 registry，反而拖慢请求

控制：listen 后异步、严格配额、遵守现有全局初始化锁、前台优先、同 key 提升/复用、无关 key 可取消。监控 event-loop lag 和前台 P95。

### 风险 5：为达标缩短 prompt 或减少工具

控制：这类方案明确禁止。达标依靠工作搬移、只读复用和增量计算；payload parity 是发布硬门禁。

### 风险 6：指标通过粗扣 MCP 被“优化”

控制：用精确单调 span 的 critical-path union，只扣真正 MCP 时间；同步同时报告未扣除总值。

---

## 11. 不推荐方案

1. **只扩大现有 WarmPool**：session/project key 对不上仍会 miss，而且不解决 MCP 后 2.128 秒装配。
2. **共享一个 live DeepAgent 给多个 session**：虽快，但 checkpoint、cwd、MCP、权限和 rail 状态会串，稳定性不可接受。
3. **准备通用 live child 后改 session_id/project_dir**：本质仍是可变实例重绑定，难以证明 interaction/checkpoint/resource 注册完全干净。
4. **删除工具、减少技能、截断 33k prompt、关闭 MemoryRail**：直接改变效果或能力，不满足约束。缺配置时跳过与“关闭有效 MemoryRail”是两回事。
5. **只优化模型 KV cache/provider TTFT**：对端到端有价值，但不减少本文定义的 `recv→model call` 本地时段。
6. **引入 Redis/新微服务缓存**：此问题是单进程初始化与组装，远程缓存增加序列化、网络和故障面，没有必要。
7. **绕过 OpenJiuwen 全局注册锁并发初始化**：已有代码注释说明 registry 是进程级可变资源，强行并发会换来不稳定。

---

## 12. 最终决策建议

建议批准以下组合，而不是批准某个孤立补丁：

1. 先建立精确、可审计的性能口径和 payload parity 基线。
2. 以 `session.prepare` 把“最终 session_id + 最终 project_dir”的 SessionRuntime 在 query 前准备好；这是安全搬离约 1.5 秒冷初始化的关键。
3. 以 immutable blueprint + session/request overlay 消除首轮重复 prompt/tool 装配；这是把余下 2.1 秒压进预算的关键。
4. 用 catalog generation、MemoryRail 快速返回和小型日志作为低风险补充。
5. 全程保留 legacy 回退，按 payload 等价、隔离、质量、第二轮和吞吐共同验收。

按首样本和同日分布看，单靠局部 if/缓存无法可靠达到 2 秒；上述组合在不改变模型输入和工具能力的前提下，可把正常命中路径设计到 `≤1.6 s`，从而使 `P95≤2.0 s` 具备可行性。最终是否达标必须以阶段 0 后的 50 次完整冷启动数据确认，不能以预算估算代替实测。

---

## 13. 证据索引

日志：

- `logs/service_default/.logs/agent_server_20260827_124017.log:263`：stage 0 recv。
- 同文件 `:330`：stage 1 init。
- 同文件 `:337-339`：start_interaction、MCP 注册完成、stage 2。
- 同文件 `:340-347`：MemoryRail 缺配置后仍构造 EmbeddingConfig。
- 同文件 `:357-364`：工具目录/过滤、A2UI、stage 3 pre_llm。
- 同文件 `:365-366`：第一段模型输出。
- 同文件 `:409`：模型 usage，含 33,233 input tokens、13,312 cache tokens、6,406 ms provider TTFT。

jiuwenswarm 代码：

- `jiuwenswarm/server/pipeline.py:61-89`：请求 dispatch 与 stage 0。
- `jiuwenswarm/server/handlers/_default.py:642-679`：流式 chat 入口。
- `jiuwenswarm/server/runtime/agent_manager.py:808-863`：包含 project_dir 的 root cache key。
- `jiuwenswarm/server/runtime/agent_adapter/interface_deep.py:2509-2561`：session child single-flight 创建。
- 同文件 `:7762-7777`：root adapter 只作为 router/template。
- 同文件 `:7811-8005`：session child create_instance 与 stage 1。
- 同文件 `:3671-3743`：请求级 OfficeClaw MCP 注册与 stage 2。
- 同文件 `:9250-9360`：runtime config stages。
- 同文件 `:6732-6760`：MemoryRail embedding 配置构造。
- 同文件 `:14403-14439`：root 先取 session child，再注册请求级 MCP。
- 同文件 `:534-542`、`:773`、`:823`：stage 3 位于底层模型 invoke/stream 边界。
- `jiuwenswarm/agents/harness/common/rails/progressive_tool_rail.py:453-548`：工具读取、过滤与导航构建。
- `jiuwenswarm/server/runtime/agent_warm_pool.py:75-163`、`:461-540`、`:629-687`：WarmKey、并发保护、prepare/claim/wait。

relay-claw 代码：

- `packages/api/src/domains/agents/services/agents/providers/RelayClawAgentService.ts:570-583`：稳定 session_id 解析。
- 同文件 `:968-1017`：invoke 前已确定 session/mode，并并行连接与 MCP 字段读取；team 路径才显式 session.create。
- 同文件 `:1125-1163`：构造并发送 chat.send。
- 同文件 `:3446-3473`：chat.send 携带 mode/model/project_dir/MCP 等最终参数。
- `packages/api/src/index.ts:1027-1045`、`:2405-2414`：现有 catalog prewarm，只覆盖共享目录同步。

