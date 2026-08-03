# Heartbeat 接口设计问题审计与修订决策

> 基准文档：`jiuwenswarm心跳任务重构-接口设计方案.md`
>
> 基准实现提交：`634f5b7e feat(heartbeat): complete session-bound job lifecycle`
> 审计目标：识别原方案自身的歧义、矛盾、缺口和不可实现约定，并给出可编码、可测试的修订口径。

## 1. 结论摘要

原方案对“Heartbeat 是绑定原会话的续跑任务，而不是独立 Cron”这一产品边界定义正确，但在授权主体、Agent Tool 返回链路、运行与调度状态分层、取消真实性、崩溃恢复、配置迁移和兼容窗口方面缺少闭环。若仅按原文逐字段实现，会出现“接口看似成功、模型实际拿不到结果”“会话临时不可读就永久停任务”“Gateway 重启后任务假运行 24 小时”“运行中停用被完成回调重新启用”等问题。

本轮采用以下原则：

1. Gateway 是 Heartbeat job 的唯一业务权威；AgentServer 不直接写 `heartbeat_jobs.json`。
2. Agent Tool 必须得到 Gateway 的真实结果或明确超时，不能把单向 `forwarded` 当成功。
3. 所有修改必须以当前会话为授权边界；客户端字段不能覆盖可信上下文。
4. job 调度状态和 run 状态虽然暂时保留现有兼容结构，但任何运行回调都不能覆盖运行期间发生的用户修改。
5. 恢复、取消、资源限流必须返回和持久化真实结果，不能“尽力而为后仍报告成功”。
6. 兼容迁移必须有明确读写策略：新名称优先、旧名称只读兼容、写入只写新名称。

## 2. 问题清单与决策

### HB-D01（P0）Agent Tool 只有单向 push，没有权威返回值

原方案假设 `LocalFunction` 可以直接复用 Gateway Controller，但实际 Agent Tool 在 AgentServer 进程，Controller 在 Gateway 进程。当前单向 server-push 只能返回 `{status: forwarded}`，真实 job、job id 和错误被 Gateway 发到 Channel，调用工具的模型拿不到。

影响：

- create 后模型不知道 job id，无法可靠 update/cancel；
- list/get 的真实数据不进入工具结果；
- Gateway 的 `FORBIDDEN/BAD_REQUEST` 不会使工具调用失败；
- “任务完成后必须实际停止”的核心闭环不可成立。

修订：增加带 `operation_id` 的关联请求/响应。AgentServer 注册一次性 Future，向 Gateway push 操作；Gateway 执行 Controller 后，通过内部 `heartbeat.tool_response` 请求回送结果；Agent Tool 等待真实结果并原样返回。超时和 Gateway 错误必须作为工具错误暴露。无 `operation_id` 的旧 push 暂时保留界面回包兼容。

状态：已落地。Agent Tool transport 不支持关联 `request()` 时明确失败，不再把单向 push 伪装成成功。

### HB-D02（P0）Web/RPC 的会话字段与授权边界矛盾

§0.1/§2.3 写“显式传 `session_id/channel_id`”，§6.2 又写从当前会话自动注入、只读；§2.4 允许 Web/RPC 迁移到任意 session，但没有定义用户主体、会话可见范围和迁移权限。

风险：客户端伪造 session id 后读取或修改其他会话的任务。

修订：普通 Web/TUI/RPC 始终用 handler 的可信当前 session/channel 覆盖客户端值；所有 get/update/delete/toggle/preview/run_now/cancel 做 session ownership 校验。跨 session 迁移只允许未来的显式管理端 capability，不复用普通 update。

状态：基准实现已按安全口径落地；修订原文，不开放普通 rebind。

### HB-D03（P0）`scope=all_visible` 没有可执行的权限模型

原方案没有定义 principal、tenant、owner、共享会话或管理员 capability，因此无法实现“all visible”而不泄露 prompt、session id 和路由信息。

修订：当前接口默认且仅保证 `scope=current`；`all_visible` 必须由调用层显式传入 `heartbeat.jobs.all` capability，否则 `FORBIDDEN`。在统一身份模型落地前，前端不展示“全部可见”。

状态：基准实现已落地安全默认；后续身份系统接入时扩展。

### HB-D04（P0）`status=running` 同时承担调度状态和执行状态

单一 `status` 无法无歧义表达以下合法组合：任务已停用但当前 run 允许收尾、任务运行中且下一周期已到、queue 中还有待执行 run、运行中修改 schedule。原状态表还把 `running` 的 `next_run_at` 写成 `-`，但并发策略需要它在运行期间继续推进。

修订：兼容版本暂不删除 `running`，以 `run_state.current_run_id` 作为真实运行判定；完成回调使用 run-id CAS，且必须尊重运行期间最新的 `enabled/status/schedule/next_run_at`，不能用 run 开始时快照覆盖用户修改。下一主版本建议拆成 `schedule_status` 与 `run_status`，并迁移掉顶层 `running`。

状态：本轮补齐“运行期间修改不被回滚”；结构拆分列入 breaking change。

### HB-D05（P0）Agent run 完成语义在文档内自相矛盾

§9.2 建议第一版“投递成功即完成”，但 `running/max_runs/delete_after_run/replace/cancel` 都要求真实 run 完成事件。投递成功不等于执行成功，会提前累计 run_count、触发 completed，并让并发策略失效。

修订：只接受 MessageHandler 对精确 request/run id 的 finally 回调；dispatch 失败记 failed；陈旧回调必须被 CAS 忽略。

状态：基准实现已落地。

### HB-D06（P0）取消接口会把“尝试取消”报告成“已取消”

原响应只有 `cancelled_run_id/paused`，但行为又要求 `cancel_pending/cancel_failed`，数据模型没有相应字段。调用方无法区分成功、目标已不存在和投递失败。

修订：响应增加 `cancel_status=cancelled|not_found|failed|idle`；`paused` 只表示调度已停用。精确取消结果必须由 MessageHandler 的 bool 返回值决定；失败原因写入 run_state 的最近错误信息。保留旧字段以兼容前端。

状态：已落地。取消先确认、后结束本地 run；取消失败保留真实运行态，delete/replace 不再继续破坏性状态转换。

### HB-D07（P0）运行中停用/修改可能被旧完成回调覆盖

run 开始时保存的 `resume_*` 快照在完成时无条件恢复。若用户在运行期间 disable 或修改 schedule，旧回调可能重新启用任务或恢复旧 next_run_at。

修订：完成时读取锁内最新 job；显式 disabled 优先；非 reschedule 的 run_now 保留当前实时 schedule 状态而非陈旧快照；所有 finish 仍按精确 run id CAS。

状态：本轮编码。

### HB-D08（P1）Gateway 重启后的 running lease 设计错误

原方案未定义 scheduler owner/lease。当前固定 24 小时恢复窗口会把重启后已不存在的 Gateway stream 当作仍在运行，任务最多阻塞一天。反过来，多 scheduler 实例又可能相互误判。

修订：明确单 Gateway scheduler owner。进程内 `_active_runs` 是 stream 存活证据；启动 reload 时，持久化 running 但内存无对应 run 的记录立即标记 failed 并恢复调度。若未来支持多活，必须增加 owner_id、lease heartbeat 和 fencing token，不能复用当前 JSON 方案。

状态：本轮编码单 owner 恢复；多活列入架构升级。

### HB-D09（P1）资源限制防御性跳过会形成热循环

当配置下调导致已有任务超限，scheduler 对 due job 只 warning/return，不推进 next_run_at。该 job 每个 poll 周期都会再次命中，产生持续日志和扫描压力。

修订：防御性限流按一次 skipped 处理，记录原因并基于 now 推进下一次触发；once 无下一次时进入 expired。每个 tick 以 `running > created_at > id` 做确定性准入，只有超额任务被跳过，避免降配后所有 due job 一起饥饿。资源限制值规范化为 API 中稳定的数字类型，重新启用任务也必须重新校验限额。

状态：已落地。

### HB-D10（P1）会话“不存在”和“暂时不可读”被混为一类

`_read_metadata` 对文件不存在、JSON 损坏和 I/O 异常都可能返回空 dict。原方案要求直接执行 session_deleted_policy，会因临时写入窗口或损坏永久停任务。

修订：仅确认 session 目录不存在时返回 missing；session 目录存在但 metadata 尚未生成、为空、损坏或 I/O 失败应抛 transient error，本轮跳过且不改变 job。显式 session delete hook 仍立即执行策略。

状态：本轮编码。

### HB-D11（P1）配置“迁移”实际上只有运行时 fallback

原文称单向迁移，但仅在启动时读取旧 `heartbeat.every/target/active_hours`，没有持久化到 `health_check`，旧键也不会清理。配置保存函数还绕过项目已有的原子 `update_config`。

修订：提供幂等、带文件锁的迁移函数：新段不存在时复制旧探活键到 `health_check`，保留 `heartbeat.jobs`，删除 heartbeat 下的旧探活键；所有后续写只写 `health_check`。新增 `HEALTH_CHECK_*` env，旧 `HEARTBEAT_*` 仅作为兼容 fallback。

状态：本轮编码。

### HB-D12（P1）旧探活“命名铁律”与内部协议不一致

原文要求类/常量/事件/API 全量改名，但兼容实现仍使用 `__heartbeat__`、`heartbeat_` 临时 session、`HEARTBEAT_OK`。如果只改 producer 会破坏 Agent rail、历史过滤和前端 session 隐藏。

修订：迁移必须跨 producer/consumer 原子完成；新请求发送 `__health_check__`、`health_check_`、`HEALTH_CHECK_OK`，读取/过滤端在一个兼容窗口内同时接受旧值。外部 API 继续只暴露 `health_check.*`。

状态：本轮编码双读单写。

### HB-D13（P1）interval 的锚点语义未定义

“基于 now 重算、不补跑”没有说明 now 是触发时刻还是完成时刻。当前 claim 和 finish 都会重算，短任务会使 interval 随执行耗时漂移，长任务又会在运行中触发 concurrency policy。

修订：scheduler 自动触发采用 fixed-rate-without-backfill：claim 时基于触发 now 推进一次 next_run_at，finish 不再次移动；若已落后则下次调度从当前 now 推进。手动 run_now 只有 `reschedule=true` 才在完成时重算。

状态：本轮编码。

### HB-D14（P1）`delete_after_run` 命名与行为相反

字段名通常表示物理删除，原文却规定执行后 `completed` 并保留记录；这会误导 API 使用者。它与 once、max_runs 的终止语义也有重叠。

修订：兼容期保持原字段但明确其语义是 `complete_after_run`；响应 meta 增加 deprecation 提示。下一版本新增 `complete_after_run` 并只读兼容旧字段，最终移除误导命名。

状态：本轮只补充 meta 提示；字段重命名需版本升级。

### HB-D15（P1）max_runs/run_count 对失败、取消、skip 的计数未定义

“最大触发次数”可能理解为所有调度命中，也可能理解为实际执行完成。不同理解会直接改变停止时间。

修订：`run_count` 定义为已进入 Agent 且以 succeeded/failed 结束的 attempt 数；cancelled/skipped 不计数，`skipped_count` 单列。max_runs 基于 run_count。未来若产品需要总触发量，新增 `trigger_count`，不改变 run_count 语义。

状态：现实现符合，补文档和测试。

### HB-D16（P1）run_now 在 disabled/terminal/once 上的语义缺失

原文只说“不改变 next_run_at”，没有说明是否允许 disabled、是否消耗 max_runs、once 已过期如何处理。

修订：允许对仍绑定有效 session 的保留记录 run_now；`reschedule=false` 恢复调用前的调度状态；实际 succeeded/failed 仍计入 run_count；若达到 max_runs 或 complete-after-run，则 completed；`reschedule=true` 要求 schedule 能计算未来时间，否则 BAD_REQUEST。

状态：基准实现大部分已落地；补边界测试。

### HB-D17（P1）缺少幂等键和乐观并发控制

create 在网络重试时可能创建重复任务；两个编辑端同时 update 时后写静默覆盖前写。文件锁只能保证写入原子，不能解决业务幂等和 lost update。

修订：下一兼容版本建议增加 `idempotency_key`（create、run_now）和单调 `revision`（update/delete 的 `if_revision`）。在字段加入前，前端不得自动重试非幂等 create/run_now。

状态：需要 API version 变更，暂不在本轮无条件加入。

### HB-D18（P1）session 路由快照的来源与生命周期未定义

只保存 channel_id 不足以恢复多 app/chat/user/bot 路由；永久保存完整 route 又可能泄露或陈旧。

修订：job 只保存 session_id/channel_id，执行时从 session metadata 动态恢复当前 mode，并从 `delivery_context` 恢复 provider/chat/user/bot/app；MessageHandler 不得用频道默认状态或 SessionMap 覆盖 heartbeat 绑定会话。路由读取失败按 transient 处理，不复制敏感 token 到 job。

状态：基准实现已落地。

### HB-D19（P2）schema version 没有迁移注册表

`version: 1` 只有数字，没有升级函数、未知版本拒绝策略或备份约定。后续新增 run_state 字段只能靠宽松默认，无法处理语义变化。

修订：读取未知大版本应拒绝写回；每次结构升级提供 `vN -> vN+1` 纯函数和备份。当前新增字段保持 v1 可选字段兼容，结构拆分时升 v2。

状态：v2 状态拆分时实施。

### HB-D20（P2）列表接口缺少稳定排序、分页和脱敏定义

虽然全局默认上限 100，接口仍应定义顺序；完整快照包含 prompt 和路由审计信息，不适合跨会话列表。

修订：current scope 按 `created_at,id` 稳定排序；未来 all-visible 只返回 summary，详情再走 get。若提高全局上限再加入 cursor pagination。

状态：本轮补稳定排序；跨会话 summary 随权限模型实施。

### HB-D21（P2）错误码映射不完整

资源冲突、重复幂等键、运行中状态冲突都被压成 BAD_REQUEST；异步 Agent Tool 还可能丢失 code。

修订：本轮关联响应保留 `code/error`；下一 API 版本增加 `CONFLICT` 和 `TIMEOUT`。现有 Web 错误码集合保持兼容。

状态：本轮保真传递；扩展错误码后续统一处理。

### HB-D22（P2）preview 与实际调度的时间基准可能漂移

preview 未返回计算基准，客户端收到结果时无法解释边界差异；DST/cron helper 行为只写“复用”，没有明确跳时/重时策略。

修订：响应增加可选 `computed_at/timezone`；cron 语义完全继承 Cron helper，并用 DST 用例锁定。属于兼容性新增字段。

状态：建议后续小版本加入。

### HB-D23（P2）前端 automation 标记只规定输入，没有端到端保留保证

原方案没有验证 request metadata 会被所有 delta/final/processing_status 分支回传，徽章可能只在部分事件出现。

修订：MessageHandler 合并 request metadata 到所有 stream chunk；用 delta/final/processing_status 三类事件验证。取消改走精确 request-id 控制 API，不再伪造一条普通 chat cancel 消息。前端对历史版本缺标记的中间 processing 状态应容错。

状态：已落地并补测试。

### HB-D24（P2）`max_runs=null` 的无限任务只有 UI 提醒，没有服务端治理

无限任务可能长期消耗资源，尤其 prompt 无法可靠自停。

修订：默认仍有限；null 仅显式传入时允许，审计 source/created_by；未来加入 max lifetime、连续失败熔断和管理端清理策略。

状态：治理能力后续实现。

## 3. 修订后的关键契约

### 3.1 Agent Tool 往返

1. Agent Tool 生成唯一 `operation_id`，注册 Future。
2. AgentServer server-push 到 Gateway，携带 action、可信 session 上下文和 operation_id。
3. Gateway 校验 ownership，执行 Controller。
4. Gateway 通过内部 `heartbeat.tool_response` 将 `{ok,data,error,code}` 回送 AgentServer。
5. Agent Tool Future 完成并把真实结果返回模型；超时明确失败。
6. 旧 Gateway push 未带 operation_id 时只保留界面回包兼容；Agent Tool transport 本身不支持关联请求时明确报错。

### 3.2 调度与运行修改优先级

优先级从高到低：显式 pause/disable > terminal stopping condition > 运行期间最新 schedule > run 开始时恢复快照。任何回调必须同时匹配 job_id 和 current_run_id。

### 3.3 恢复策略

- 同一进程内：`_active_runs` 与 MessageHandler 精确 stream tracking 是运行证据。
- Gateway 冷启动：持久化 running 且无内存证据，立即 failed recovery，不等待固定 24h。
- session metadata 文件存在但不可读：transient，本轮不改变任务。
- 明确 session delete hook 或目录确认不存在：执行 session_deleted_policy。

### 3.4 兼容迁移

- 配置：读取 `health_check` 优先；启动时幂等迁移旧探活键；写入仅写 `health_check`。
- env：新 `HEALTH_CHECK_*` 优先，旧 `HEARTBEAT_*` fallback。
- 探活内部 token：新值单写，旧 session prefix/OK token 双读一个兼容窗口。
- Heartbeat job JSON：新增 run_state 字段均为可选，保持 version 1；结构拆分才升级 version 2。

## 4. 本轮编码范围

本轮立即实施：HB-D01、D06、D07、D08、D09、D10、D11、D12、D13、D20，并为 D14/D15/D16 增加明确 meta/测试。

已由基准提交覆盖：HB-D02、D03、D05、D18，以及 D04 的 run-id CAS 部分。

需要独立版本或统一平台能力后实施：D04 的状态字段拆分、D14 字段重命名、D17 幂等与 revision、D19 schema v2、D21 新错误码、D22 preview 扩展、D24 长期治理。

## 5. 验收补充

- Agent create/list/get/update/cancel 工具必须拿到 Gateway 真实返回，不得只断言 push 成功。
- 运行期间 disable 后，旧 succeeded callback 不得重新启用。
- Gateway 冷启动后，近期和陈旧 orphan running 都能恢复，不等待 24h。
- 配置下调导致 scheduler 限流时，next_run_at 必须推进且不会每 poll 热循环。
- metadata 文件存在但 JSON 损坏时 job 保持原状态；明确删除时才执行删除策略。
- scheduler interval finish 不二次漂移；run_now(reschedule=true) 才从完成时刻重算。
- cancel 响应必须区分 cancelled/not_found/failed/idle。
- 新探活请求只生成 health_check 命名；旧 heartbeat 前缀/token 仍能被过滤识别。

## 6. 本轮实施结果

上述“本轮编码”项已经落地，并补充了对应回归测试：

- Agent Tool 使用 `operation_id` 等待 Gateway 的真实数据或错误；旧单向 transport 仅保留兼容路径。
- 精确取消结果返回并持久化 `cancelled/not_found/failed/idle`，pause 与取消事实分离。
- cancel/replace 先确认精确 stream 已停止，再结束旧 run 或提升替代 run；失败与调用协程中断都保持旧 run 权威并清理预留。
- delete 在活动 run 取消失败时拒绝删除；显式 session 删除会先精确取消活动 run，再执行 completed/disabled 策略。
- finish 采用最新 job 状态，运行中 disable 不会被旧回调重新启用；scheduler 与 run_now 的 interval 锚点分别锁定。
- Gateway reload 以精确 stream task 为运行证据；无证据 orphan 立即恢复，活跃 stream 可重新挂接。
- store 的生产状态转换全部改成锁内原子 mutation，避免 read-modify-write 覆盖并发回调。
- 限流采用确定性准入，只有超额任务原子记录 skipped 并推进 `next_run_at`，避免 poll 热循环和全体饥饿；重新启用也校验限额。
- session 目录缺失和 metadata 暂时不可读已分流。
- MessageHandler 对 heartbeat 保留绑定 session，并从 session metadata 恢复当前 mode，不再被频道默认状态覆盖。
- 旧探活配置在启动时加锁幂等迁移；`HEALTH_CHECK_*` 环境变量优先，旧变量只读 fallback。
- 探活内部协议改为 health_check 单写，所有已识别的 session/history/UI/relay consumer（含 Telegram、DingTalk）对旧 heartbeat 双读。
- delta/final/processing_status 流事件均保留 heartbeat `automation` 元数据。
- list 增加稳定排序；limits 输出规范为数字/null；meta 明确 run_count 和 `delete_after_run` 兼容语义。

仍按本审计保留到后续版本的项目：D04 状态字段拆分、D14 字段重命名、D17 幂等/revision、D19 schema v2、D21 新错误码、D22 preview 时间基准、D24 长期治理。它们不是本轮基础要求的隐式欠实现，而是需要版本或平台能力配合的显式后续项。

## 7. 原文典型场景覆盖复核

原文 §8.2 的 schedule、三种并发策略、状态迁移、Web/Team session 删除、停止条件、ghost 清理、store reload、source 审计、九个 Agent Tool、命名/配置迁移以及 `params.mode` 缺失等场景均已有自动化用例。本轮又补上了原清单未写出、但实现正确性必需的边界：

| 新增覆盖 | 断言重点 |
|---|---|
| Agent Tool 关联往返 | 模型拿到真实 job 数据；Gateway code/error 进入工具错误 |
| get missing | 返回 `NOT_FOUND`，不因 `None` 封装崩溃 |
| cancel 真实性 | cancelled 与 exact stream not_found 均进入响应和 run_state |
| cancel 失败/中断 | 保留权威 active run；delete/replace 停止；协程中断清除 intent/reservation |
| 完成竞态 | 运行中 disable 不被旧 callback 覆盖 |
| interval 锚点 | scheduler 按 claim 时刻；run_now reschedule 按完成时刻 |
| Gateway reload | orphan 立即恢复；精确活跃 stream 重新挂接 |
| 限流降配/重启用 | 确定性准入一个合法任务，其余 skipped 且推进；重新启用受限额约束 |
| session metadata | 目录缺失与文件缺失/损坏分别走 missing/transient |
| session 现场恢复 | heartbeat 保留绑定 session，并恢复该 session 当前 mode |
| 配置/命名兼容 | 迁移幂等且保留 jobs；health_check 单写、heartbeat 双读 |
| relay/automation | Telegram、DingTalk 新旧 payload 双读；delta/final/processing 元数据保留 |
| API 稳定性 | list 稳定排序；limits 数字/null 归一化；弃用语义进入 meta |

截至本轮，Heartbeat/HealthCheck 定向回归为 161 个用例通过（另有 73 个被筛选排除）；此前 session metadata/history 兼容回归为 90 个用例通过。测试框架在用例全部结束后仍有项目既有后台线程/事件循环未退出，因此本地运行需在结果完整输出后中断进程；该现象与本轮断言结果无关，但应另行清理测试生命周期。
