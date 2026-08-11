# TUI 流式卡顿看门狗误报修复设计

- **日期**: 2026-08-11
- **范围**: `jiuwenswarm/channels/tui/frontend/src/app-state.ts`（仅此一个文件）
- **背景 issue**: 内网/air-gapped 环境下，首次对话及稍长任务期间 TUI 反复报
  `Network appears offline while the task is running. Stopped the current TUI response; reconnect and retry.`
  后端实际仍在正常运行。

## 1. 问题根因（已对照当前代码核实）

该错误**不是 WebSocket 断连报错**，而是 TUI 前端的**流式卡顿看门狗**（stream stall
watchdog），与 ws 连接状态独立——报错时 ws 往往仍为 `connected`。

触发链（`app-state.ts`）：

1. 进入响应态：`streamingState === Responding` 且 `connectionStatus === connected`
   → `hasActiveResponseStream()`（`:866`）为真 → `noteStreamActivity()`（`:886`）记
   `lastStreamActivityAt = now`，排一个 8 秒定时器（`scheduleStreamStallWatchdog`
   `:896`）。
2. 每个"进度帧"重置定时器：`handleFrame`（`:3342`）对属于当前 session 且非排除类
   （排除 `chat.processing_status`/`connection.ack`/`history.message`/
   `context.usage`/`context.compression_state`，见 `isStreamProgressFrame` `:945`）
   的帧调 `noteStreamActivity()`，刷新时间戳并重排定时器。只要 token/工具帧持续来，
   watchdog 永远不触发。
3. 8 秒内无任何进度帧：定时器在 `ACTIVE_NETWORK_CHECK_INTERVAL_MS = 8000`（`:249`）
   后 fire `handleStreamStallNotice()`（`:922`）。
4. TCP 三探针全失败：`hasExternalNetwork()`（`:269`）对 `223.5.5.5:53`、
   `114.114.114.114:53`、`1.1.1.1:443` 各 1500ms 超时 `probeTcp`，
   `results.some(Boolean)` 为假才往下走。
5. 首次（`streamStallNoticeShown` 为 false）才真正报错，调
   `failActiveTurnAfterConnectionLoss("Network appears offline...")`（`:935`）。

**air-gapped 内网必然中招**：① 首对话冷启动首 token 慢 / 工具执行久 / AgentServer
忙 → 8s 无帧常见；② 公网三探针对 air-gapped 永远失败 → 必然走到第 5 步误报。

**报错后做了什么（最伤体验的部分）**：`failActiveTurnAfterConnectionLoss`
（`:1031`）全是**前端单方面清理，不通知后端**：

- `wsClient.cancelRequest` 只清本地 pending map + reject Promise，不发任何 ws 取消包；
- `markRunningToolsConnectionLost`（`:2725`）把本地 running 工具改 error/"Connection
  lost"，纯前端展示；
- 流式状态回 Idle、清子任务/todos、`addItem(addError)`。

结论：**后端毫不知情、继续跑**。用户若直接重发，会和后端仍在跑的旧任务重叠；后端跑完
的最终帧到达时，会被当作"属于已 Idle 旧 turn 的迟到帧"被延迟或丢弃。

**现有逃生舱**（commit `6ef18a9e9`，Refs #1667）：环境变量 `JIUWENSWARM_SKIP_NETWORK_CHECK`
让 `hasExternalNetwork()` 直接返 true（`:270`）。但它是全有或全无的隐藏开关，且 TUI 是
Node 进程不自己 load `.env`，需在启动 TUI 的 shell 里设并重启 TUI；不在 `config.yaml`，
全仓库无文档。

## 2. 关键不变量（决定最小方案可行性的依据）

已对照当前代码核实：

- **看门狗只在 `connectionStatus === "connected"` 时才可能触发**：`hasActiveResponseStream()`
  （`:866`）要求 `connectionStatus === "connected"`，而 `handleStreamStallNotice()`
  入口（`:923`）和 `scheduleStreamStallWatchdog()`（`:898`）都依赖它。`connectionStatus`
  是普通字段，由 `handleConnectionStatusChanged`（`:690` 设值点附近）维护。
- **ws 一旦 `reconnecting`，看门狗会被立即取消**：`handleConnectionStatusChanged()`
  （`:954`）在 `status === "reconnecting"` 分支里先调 `clearStreamStallWatchdog()`
  （`:956`），再 `startActiveTurnReconnectWatchdog()`（`:957`）。也就是说
  `reconnecting`/`disconnected` 状态下 `handleStreamStallNotice` 根本不会被调用——它
  会在 `:923` 的 `hasActiveResponseStream()` 守卫处提前 return。

**推论**：在 `handleStreamStallNotice` 函数体内重新检查 `connectionStatus === "connected"`，
在 air-gapped 内网里**永远为真**（因为能进来就说明 ws 仍 connected）→ 永远续期、不报错。
而公网用户真断网时 ws 会先变 `reconnecting`，走的是另一条 60s 报错路（`:993`
`startActiveTurnReconnectWatchdog` → `:1018` `failActiveTurnAfterConnectionLoss("Connection
lost for over 60 seconds...")`），根本到不了这条公网探针分支——**公网用户的保护语义完全
不被弱化**。

## 3. 选定方案：ws 连接状态作判据（最省）

把 `handleStreamStallNotice()` 的判据从"8s 无帧 → 直接探公网"改为"8s 无帧 → 先看 ws
连接状态：仍 connected 则判本地服务还活着、任务在跑只是没吐帧，刷时间戳续期、不报错；
ws 不在 connected 才降级到现有 `hasExternalNetwork()` 公网探针兜底"。

### 3.1 改动点（唯一）

**只改 `app-state.ts` 的 `handleStreamStallNotice()` 函数体（`:922`–`:938`）一个函数。**
不新增任何函数、不新增任何探针、不新增任何字段、不新增任何常量。

#### 改动前（当前代码）

```ts
private async handleStreamStallNotice(): Promise<void> {
  if (!this.hasActiveResponseStream()) {
    return;
  }
  if (await hasExternalNetwork()) {
    this.lastStreamActivityAt = Date.now();
    this.scheduleStreamStallWatchdog();
    return;
  }
  if (this.streamStallNoticeShown) {
    return;
  }
  this.streamStallNoticeShown = true;
  this.failActiveTurnAfterConnectionLoss(
    "Network appears offline while the task is running. Stopped the current TUI response; reconnect and retry.",
  );
}
```

#### 改动后（目标代码）

```ts
private async handleStreamStallNotice(): Promise<void> {
  if (!this.hasActiveResponseStream()) {
    return;
  }
  // ws 仍 connected 即认为本地服务（Gateway/AgentServer）还活着、任务在跑只是暂未吐帧。
  // air-gapped 内网里 ws 恒 connected，故永远续期不报错，消除公网探针必然失败导致的误报。
  // 公网用户真断网时 ws 会先变 reconnecting（见 handleConnectionStatusChanged，走 60s
  // 重连看门狗），不会走到本分支，故公网保护语义不变。
  if (this.connectionStatus === "connected") {
    this.lastStreamActivityAt = Date.now();
    this.scheduleStreamStallWatchdog();
    return;
  }
  // ws 已不 connected：保留原公网探针作为防御性兜底。
  if (await hasExternalNetwork()) {
    this.lastStreamActivityAt = Date.now();
    this.scheduleStreamStallWatchdog();
    return;
  }
  if (this.streamStallNoticeShown) {
    return;
  }
  this.streamStallNoticeShown = true;
  this.failActiveTurnAfterConnectionLoss(
    "Network appears offline while the task is running. Stopped the current TUI response; reconnect and retry.",
  );
}
```

**净改动**：在原有 `if (await hasExternalNetwork())` 之前，**新增一个**
`if (this.connectionStatus === "connected")` 早返回分支，逻辑与原 `hasExternalNetwork()`
为真时完全一致（刷时间戳 + 重排看门狗 + return）。原公网探针分支原封保留，作为 ws 不
connected 时的防御性兜底。

### 3.2 为什么不破坏现有公网用户保护

| 场景 | 改动前 | 改动后 | 是否退化 |
|---|---|---|---|
| 公网用户真断网 | ws 先 reconnecting → 走 60s 重连看门狗报错（不到 stall 分支） | 同左 | 否 |
| 公网用户 ws 仍 connected 但 8s 无帧 + 公网通 | 探针通过 → 续期 | ws connected → 续期（更早返回） | 否（结果一致，只是判据更省） |
| 公网用户 ws 仍 connected 但 8s 无帧 + 公网不通（罕见：本地通、公网被墙但 ws 没断） | 探针失败 → 报错中断 | ws connected → 续期不报错 | **行为变化**：从"报错中断"变"续期等待" |
| air-gapped 内网 ws connected、8s 无帧 | 探针必然失败 → 误报中断 | ws connected → 续期不报错 | **目标场景，修复** |

唯一的行为变化在第 3 行（公网用户 ws 仍 connected 但公网不通）：改动前会报错中断，
改动后续期等待。但这一行恰恰是**误报**本身——ws 还 connected 说明本地链路活着、后端
在跑，只因公网探针失败就中断后端正在跑的任务，正是本次要修的问题。所以这个"行为变化"
是期望的修复，不是退化。

### 3.3 不改的东西（明确边界）

- **不改 `config.yaml`**：本方案零配置改动，符合约束。
- **不改后端 / 不改协议 / 不改 ws-client.ts**：`cancelRequest`/`failActiveTurnAfterConnectionLoss`
  等共用函数一行不动。
- **不新增 env 变量、不删现有 `JIUWENSWARM_SKIP_NETWORK_CHECK`**：现有逃生舱保留，互不
  冲突。新方案的 ws 分支在 `hasExternalNetwork()` 调用之前执行；若 ws 仍 connected（内网
  恒成立）则已提前续期返回，根本不会调到 `hasExternalNetwork()`。即便 ws 不 connected
  落到 `hasExternalNetwork()`，env=1 时该函数第一行仍直接返 true——两者结果一致：都续期
  不报错，只是判定路径不同。
- **不动 `failActiveTurnAfterConnectionLoss`**：它仍被 `auth_failed`/`message_too_big`/
  60s 重连超时等多条路共用，语义不变。

## 4. 已知盲区与取舍

- **ws 仍 connected 但本地服务实际僵死**（TCP 连接还在、服务进程卡住不吐帧）：改动后
  不报错，会一直续期等待。改动前在 air-gapped 里同样护不住（公网探针必失败，会误报中断
  正常任务，而非检测出这种僵死）。该 case 只能靠 ws 自身的 keepalive 超时 → reconnect
  路径兜底，与本次改动正交，不在范围内。
- **无硬性续期上限**：ws 恒 connected 时会一直续期。这是期望行为（后端在跑就等），且
  后端任务终会结束吐帧或 ws 最终会因 keepalive 断开进入 reconnect。如未来需要"等太久
  也提示"的软提示，可作为独立增强项，不进本次最小改动。

## 5. 验证

- **构建**：`cd jiuwenswarm/channels/tui/frontend && npm run build`（tsc）通过、`npm run
  typecheck` 通过。
- **静态**：`npm run lint`（oxlint）通过。
- **手测（air-gapped 复现）**：内网环境启动 jiuwenswarm，TUI 首次对话 + 触发一个会执行
  >8s 的任务（如调用慢工具 / 长 LLM 首 token），确认不再弹出该错误、后端任务正常完成、
  最终帧正常显示。
- **回归（公网保护不退化）**：公网环境模拟"ws connected + 8s 无帧 + 公网通"应续期（与
  改动前一致）；"真断网"应走 60s 重连看门狗报错（与改动前一致）。

## 6. 文档

本设计文档自身即记录。现有 `JIUWENSWARM_SKIP_NETWORK_CHECK` env 仍无文档，本次不在范围
内补（避免扩大改动）；如后续需要，可作为独立小任务在 TUI README 补一行说明。
