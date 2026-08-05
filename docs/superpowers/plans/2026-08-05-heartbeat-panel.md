# Heartbeat Job 前端面板 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 jiuwenswarm Web 前端新增一个会话级「心跳任务（Heartbeat Job）」管理面板，覆盖 `heartbeat.job.*` 全部 10 个 RPC 方法（meta/list/get/create/update/delete/toggle/preview/run_now/cancel），从当前会话的聊天界面里打开，不做跨会话/全局管理，不与 Cron 定时任务列表混用。

**Architecture:** 面板以「右侧滑出抽屉」的形式挂载在 `ChatPanel`（会话聊天界面）的 header 按钮之后，仅在存在真实 `session_id`（非 `NEW_CONVERSATION_ID` 占位）时可用。内部分三层：纯逻辑层（TypeScript 模块，负责 DTO↔表单转换、5 段 cron 校验、状态/结果文案映射，走仓库既有的 `tsc` 编译 + `node --test` 单测约定）、展示层（`HeartbeatStatusBadge`/`HeartbeatScheduleEditor` 等无状态组件）、容器层（`HeartbeatPanel/index.tsx` 负责数据获取、写操作、轮询与会话切换清理）。所有请求通过既有 `webRequest()` 单例发起，`session_id` 显式携带在每次请求的 `params` 里（`heartbeat.job.meta` 除外）。

**Tech Stack:** React 18 + TypeScript, Zustand（读 `activeSessionId`），Tailwind 工具类（复用 CronPanel 已有的设计 token：`bg-card`/`text-text-muted`/`bg-cron-action` 等），`react-i18next`，Node 内置 `node --test` 做纯逻辑单测（无 Jest/Vitest，无 React 组件测试基建——这是仓库既有约定，不在本轮引入新测试框架）。

## Global Constraints

- 后端参考实现分支已 clone 到 `C:\cjh\code\openJiuwen\jiuwenswarm_heartbeat`（`feature-HEARTBEAT`），字段/方法名以该分支 `jiuwenswarm/gateway/heartbeat/models.py`、`jiuwenswarm/gateway/channel_manager/web/app_web_handlers.py:5569-5821` 为准；行为语义以 `cjh/feature/heart/心跳任务前端开发接口规格说明.md` 为准，两者已核对一致。
- 前端工作目录：`jiuwenswarm/channels/web/frontend/`（相对本 worktree 根目录）。以下所有相对路径均相对此目录。
- 时间戳全部是 Unix **秒**，展示前必须 `*1000` 再传给 `Date`。
- 创建/更新请求禁止携带 `id/kind/status/channel_id/source/metadata/run_state`、以及 `mode/model/model_name/approval/sandbox/worktree/work_mode`——心跳任务永远继承原会话当前配置，UI 不提供这些字段的任何输入控件。
- 除 `heartbeat.job.meta` 外，所有请求必须显式携带 `params.session_id`，取当前**真实**会话 ID；`session_id === 'new'`（`NEW_CONVERSATION_ID`）时整个入口隐藏/禁用，不发任何 `heartbeat.job.*` 请求。
- `run_now` 只看 `payload.accepted` 才能提示"已接收"；`cancel` 只看 `payload.cancel_status === 'cancelled'` 才能提示"已取消"；两者的 RPC 层 `ok:true` 都不代表业务成功。
- `delete` 收到 `{deleted:true}` 前不得从列表乐观移除；遇到 `CONFLICT` 保留该行并提示重试。
- 列表展示状态一律读服务端 `status`/`run_state` 字段，不用 `enabled` 反推"是否运行中"，不在前端本地推导并覆盖 `run_count`/`next_run_at`。
- 心跳任务的 `schedule.type==='cron'` 用**标准 5 段** crontab（`分 时 日 月 周`），与 CronPanel 内部用的 7 段 croniter 表达式（含秒/年）不是同一种格式，不能直接复用 CronPanel 的表单，但校验规则可以补全后复用。
- 全部用户可见文案走 `t('heartbeat.*')` i18n key，新增 key 必须同时写入 `src/i18n/locales/zh.json` 和 `en.json`；组件 JSX 里不允许出现硬编码中文。
- 每个改动了 `.ts`/`.tsx` 的任务，验证步骤必须包含实际跑通对应命令（`node --test` 或 `npm run build`），不能只描述"应该通过"。

---

## File Structure

```
src/types/heartbeat.ts                              # 新增：与后端 HeartbeatJob.to_dict() 对齐的 DTO/UI/Meta 类型
src/components/HeartbeatPanel/
  heartbeatScheduleConvert.ts                        # 新增：schedule DTO ↔ 表单值 转换 + 摘要文案（纯逻辑，有单测）
  heartbeatCronValidation.ts                         # 新增：5 段 cron 校验，复用 CronPanel 校验规则（纯逻辑，有单测）
  heartbeatStatusText.ts                             # 新增：status/run_state/run_now/cancel → i18n key 映射（纯逻辑，有单测）
  HeartbeatStatusBadge.tsx                           # 新增：状态徽标展示组件
  HeartbeatScheduleEditor.tsx                        # 新增：interval/cron/once 三态调度编辑器
  HeartbeatTaskDrawer.tsx                            # 新增：创建/编辑表单（不含外层浮层容器）
  index.tsx                                          # 新增：容器组件，抽屉外壳 + 列表 + 创建/编辑 + 行操作 + 轮询
src/components/ChatPanel/index.tsx                   # 修改：header 挂载入口按钮 + 抽屉渲染
src/i18n/locales/zh.json                             # 修改：新增 heartbeat.* 文案块（各任务分别追加自己用到的 key）
src/i18n/locales/en.json                             # 修改：同上，英文版
package.json                                         # 修改：新增 3 条 test:heartbeat-* 脚本
tests/heartbeatScheduleConvert.test.mjs               # 新增
tests/heartbeatCronValidation.test.mjs                 # 新增
tests/heartbeatStatusText.test.mjs                     # 新增
```

不复用 `CronJobDTO`/`CronTaskUI`/`CronTaskFormValue`——心跳任务没有 `project_id`，字段语义也不同（`prompt` vs `description`），混用会让两套模型互相污染。可以复用的是 `CronPanel` 里跟业务无关的纯 UI 组件：`ConfirmDialog`、`StatusBadge.tsx` 导出的 `RunningIcon`/`BoldRingIcon`、`SimpleSelect`、`constants.ts` 的 `TIMEZONE_OPTIONS`、`cronExprValidation.ts` 的 `validateCronExpr`（间接复用，见 Task 3）。

---

### Task 1: Heartbeat 数据类型定义

**Files:**
- Create: `src/types/heartbeat.ts`

**Interfaces:**
- Produces: `HeartbeatJobStatus`、`HeartbeatRunStatus`、`HeartbeatConcurrencyPolicy`、`HeartbeatSessionDeletedPolicy`、`HeartbeatScheduleKind`、`HeartbeatScheduleDTO`、`HeartbeatRunState`、`HeartbeatJobDTO`、`HeartbeatTaskUI`、`HeartbeatMeta`、`HeartbeatRunNowResult`、`HeartbeatCancelStatus`、`HeartbeatCancelResult`、`HeartbeatPreviewItem` —— 后续所有任务从这里 import。

- [ ] **Step 1: 写类型文件**

```ts
// src/types/heartbeat.ts
/** 后端 heartbeat.job.* 系列 RPC 实际收发的字段，对齐 jiuwenswarm/gateway/heartbeat/models.py 的 HeartbeatJob.to_dict() */

export type HeartbeatJobStatus = 'scheduled' | 'running' | 'completed' | 'expired' | 'disabled';
export type HeartbeatRunStatus = 'succeeded' | 'failed' | 'skipped' | 'cancelled';
export type HeartbeatConcurrencyPolicy = 'skip' | 'queue' | 'replace';
export type HeartbeatSessionDeletedPolicy = 'disable' | 'completed';
export type HeartbeatScheduleKind = 'interval' | 'cron' | 'once';

export type HeartbeatScheduleDTO =
  | { type: 'interval'; interval_seconds: number }
  | { type: 'cron'; cron_expr: string; timezone: string }
  | { type: 'once'; run_at: number };

export interface HeartbeatRunState {
  current_run_id: string | null;
  current_run_started_at: number | null;
  last_run_status: HeartbeatRunStatus | null;
  last_error: string | null;
  last_cancel_status: string | null;
  last_cancel_error: string | null;
  queued_run_id: string | null;
  queued_trigger: string | null;
  queued_reschedule: boolean;
  current_trigger: string | null;
  current_reschedule: boolean;
  resume_status: string | null;
  resume_enabled: boolean | null;
  resume_next_run_at: number | null;
  skipped_count: number;
}

export interface HeartbeatJobDTO {
  id: string;
  kind: 'heartbeat';
  name: string;
  enabled: boolean;
  status: HeartbeatJobStatus;
  channel_id: string;
  session_id: string;
  prompt: string;
  schedule: HeartbeatScheduleDTO;
  timezone: string;
  concurrency_policy: HeartbeatConcurrencyPolicy;
  session_deleted_policy: HeartbeatSessionDeletedPolicy;
  max_runs: number | null;
  delete_after_run: boolean;
  created_at: number | null;
  updated_at: number | null;
  next_run_at: number | null;
  last_run_at: number | null;
  run_count: number;
  metadata: { source: string; [key: string]: unknown };
  run_state: HeartbeatRunState;
}

/** UI 层展示用结构，来自 HeartbeatJobDTO 派生，见 HeartbeatPanel/index.tsx 的 heartbeatJobToUI */
export interface HeartbeatTaskUI {
  id: string;
  name: string;
  prompt: string;
  enabled: boolean;
  status: HeartbeatJobStatus;
  schedule: HeartbeatScheduleDTO;
  timezone: string;
  concurrencyPolicy: HeartbeatConcurrencyPolicy;
  sessionDeletedPolicy: HeartbeatSessionDeletedPolicy;
  maxRuns: number | null;
  createdAt: number | null;
  updatedAt: number | null;
  nextRunAt: number | null;
  lastRunAt: number | null;
  runCount: number;
  runState: HeartbeatRunState;
}

export interface HeartbeatMeta {
  limits: {
    min_interval_seconds: number;
    max_active_jobs_per_session: number;
    max_active_jobs_global: number;
    default_max_runs: number;
    default_concurrency_policy: HeartbeatConcurrencyPolicy;
    default_session_deleted_policy: HeartbeatSessionDeletedPolicy;
  };
  schedule_types: HeartbeatScheduleKind[];
  concurrency_policies: HeartbeatConcurrencyPolicy[];
  session_deleted_policies: HeartbeatSessionDeletedPolicy[];
  statuses: HeartbeatJobStatus[];
  sources: string[];
  run_count_semantics: string;
  deprecated_fields: Record<string, string>;
}

export interface HeartbeatRunNowResult {
  accepted: boolean;
  run_id: string;
  session_id?: string;
  queued?: boolean;
  reason?:
    | 'session_missing'
    | 'session_busy'
    | 'previous_run_active'
    | 'already_queued'
    | 'replacement_pending'
    | 'replacement_cancel_failed'
    | 'job_disabled_during_replace';
}

export type HeartbeatCancelStatus = 'idle' | 'cancelled' | 'not_found' | 'failed';

export interface HeartbeatCancelResult {
  job_id: string;
  cancelled_run_id: string | null;
  cancel_status: HeartbeatCancelStatus;
  paused: boolean;
}

export interface HeartbeatPreviewItem {
  run_at: number;
  iso: string;
}
```

- [ ] **Step 2: 类型检查**

Run: `npx tsc src/types/heartbeat.ts --target ES2020 --module ES2020 --moduleResolution Bundler --rootDir src --outDir node_modules/.cache/heartbeat-types --skipLibCheck --noEmitOnError`
Expected: 无输出、退出码 0（纯类型声明文件，没有运行时逻辑，因此不需要 `node --test`；后续任务会通过 import 它间接验证类型正确）。

- [ ] **Step 3: Commit**

```bash
git add src/types/heartbeat.ts
git commit -m "feat(heartbeat): add HeartbeatJob frontend types"
```

---

### Task 2: 调度表单 ↔ DTO 转换（heartbeatScheduleConvert）

**Files:**
- Create: `src/components/HeartbeatPanel/heartbeatScheduleConvert.ts`
- Test: `tests/heartbeatScheduleConvert.test.mjs`
- Modify: `package.json`（新增 `test:heartbeat-schedule-convert` 脚本）

**Interfaces:**
- Consumes: `HeartbeatScheduleDTO`、`HeartbeatScheduleKind`（Task 1，`../../types/heartbeat`）
- Produces: `HeartbeatScheduleFormValue`、`emptyHeartbeatScheduleForm(defaultTimezone)`、`scheduleDtoToForm(schedule, fallbackTimezone)`、`scheduleFormToDto(form)`、`onceLocalToEpochSeconds(date, time)`、`epochSecondsToOnceLocal(epochSeconds)`、`summarizeHeartbeatSchedule(schedule, t)` —— Task 6（编辑器组件）、Task 7（表单）、Task 8（列表摘要）都会 import 这些。

- [ ] **Step 1: 写失败的测试**

```js
// tests/heartbeatScheduleConvert.test.mjs
import assert from 'node:assert/strict';
import test from 'node:test';

import {
  emptyHeartbeatScheduleForm,
  scheduleDtoToForm,
  scheduleFormToDto,
  onceLocalToEpochSeconds,
  epochSecondsToOnceLocal,
} from '../node_modules/.cache/heartbeat-schedule-convert/components/HeartbeatPanel/heartbeatScheduleConvert.js';

test('emptyHeartbeatScheduleForm defaults to a 30-minute interval with the given timezone', () => {
  const form = emptyHeartbeatScheduleForm('Asia/Shanghai');
  assert.equal(form.kind, 'interval');
  assert.equal(form.intervalSeconds, 1800);
  assert.equal(form.timezone, 'Asia/Shanghai');
});

test('scheduleDtoToForm/scheduleFormToDto round-trip for interval', () => {
  const dto = { type: 'interval', interval_seconds: 900 };
  const form = scheduleDtoToForm(dto, 'Asia/Shanghai');
  assert.equal(form.kind, 'interval');
  assert.equal(form.intervalSeconds, 900);
  assert.deepEqual(scheduleFormToDto(form), dto);
});

test('scheduleFormToDto clamps interval below 60 seconds up to 60', () => {
  const form = emptyHeartbeatScheduleForm('Asia/Shanghai');
  form.intervalSeconds = 10;
  assert.deepEqual(scheduleFormToDto(form), { type: 'interval', interval_seconds: 60 });
});

test('scheduleDtoToForm/scheduleFormToDto round-trip for cron', () => {
  const dto = { type: 'cron', cron_expr: '0 9 * * 1-5', timezone: 'Asia/Tokyo' };
  const form = scheduleDtoToForm(dto, 'Asia/Shanghai');
  assert.equal(form.kind, 'cron');
  assert.equal(form.cronExpr, '0 9 * * 1-5');
  assert.equal(form.timezone, 'Asia/Tokyo');
  assert.deepEqual(scheduleFormToDto(form), dto);
});

test('scheduleFormToDto trims cron_expr whitespace', () => {
  const form = emptyHeartbeatScheduleForm('Asia/Shanghai');
  form.kind = 'cron';
  form.cronExpr = '  0 9 * * 1-5  ';
  assert.deepEqual(scheduleFormToDto(form), { type: 'cron', cron_expr: '0 9 * * 1-5', timezone: 'Asia/Shanghai' });
});

test('once epoch <-> local date/time round-trips regardless of host timezone', () => {
  // 用"整分钟"时间戳做往返测试，不依赖运行测试的机器处于哪个时区
  const epoch = Math.floor(Date.now() / 1000 / 60) * 60;
  const { date, time } = epochSecondsToOnceLocal(epoch);
  assert.notEqual(date, '');
  assert.notEqual(time, '');
  assert.equal(onceLocalToEpochSeconds(date, time), epoch);
});

test('epochSecondsToOnceLocal returns empty strings for falsy input', () => {
  assert.deepEqual(epochSecondsToOnceLocal(null), { date: '', time: '' });
  assert.deepEqual(epochSecondsToOnceLocal(undefined), { date: '', time: '' });
  assert.deepEqual(epochSecondsToOnceLocal(0), { date: '', time: '' });
});

test('onceLocalToEpochSeconds returns 0 when date or time missing', () => {
  assert.equal(onceLocalToEpochSeconds('', '09:00'), 0);
  assert.equal(onceLocalToEpochSeconds('2026-08-10', ''), 0);
});

test('scheduleDtoToForm/scheduleFormToDto round-trip for once', () => {
  const epoch = Math.floor(Date.now() / 1000 / 60) * 60 + 3600;
  const dto = { type: 'once', run_at: epoch };
  const form = scheduleDtoToForm(dto, 'Asia/Shanghai');
  assert.equal(form.kind, 'once');
  assert.deepEqual(scheduleFormToDto(form), dto);
});
```

- [ ] **Step 2: 运行测试确认失败**

Run: `node --test tests/heartbeatScheduleConvert.test.mjs`
Expected: FAIL（`Cannot find module '../node_modules/.cache/heartbeat-schedule-convert/...'`，因为源文件和编译产物都还不存在）

- [ ] **Step 3: 写实现**

```ts
// src/components/HeartbeatPanel/heartbeatScheduleConvert.ts
import type { HeartbeatScheduleDTO, HeartbeatScheduleKind } from '../../types/heartbeat';

/** ScheduleEditor 内部表单状态：三种 kind 共用一个结构，提交时按 kind 只取对应字段，见 scheduleFormToDto */
export interface HeartbeatScheduleFormValue {
  kind: HeartbeatScheduleKind;
  intervalSeconds: number; // interval 用
  cronExpr: string; // cron 用，标准 5 段（分 时 日 月 周），不是 CronPanel 的 7 段格式
  timezone: string; // cron 用；也作为整个表单/任务顶层 timezone 的唯一来源，见 HeartbeatTaskDrawer
  onceDate: string; // once 用，YYYY-MM-DD，本地时区
  onceTime: string; // once 用，HH:mm，本地时区
}

const MIN_INTERVAL_SECONDS = 60;

export function emptyHeartbeatScheduleForm(defaultTimezone: string): HeartbeatScheduleFormValue {
  return {
    kind: 'interval',
    intervalSeconds: 1800,
    cronExpr: '',
    timezone: defaultTimezone,
    onceDate: '',
    onceTime: '',
  };
}

/** run_at（Unix 秒） -> 本地日期/时间字符串；用于回填 once 表单和摘要展示 */
export function epochSecondsToOnceLocal(epochSeconds: number | null | undefined): { date: string; time: string } {
  if (!epochSeconds) return { date: '', time: '' };
  const d = new Date(epochSeconds * 1000);
  if (Number.isNaN(d.getTime())) return { date: '', time: '' };
  const pad = (n: number) => String(n).padStart(2, '0');
  const date = `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  const time = `${pad(d.getHours())}:${pad(d.getMinutes())}`;
  return { date, time };
}

/** 本地日期/时间字符串 -> run_at（Unix 秒）；缺任一项按 0 处理，交给表单校验层拦截 */
export function onceLocalToEpochSeconds(date: string, time: string): number {
  if (!date || !time) return 0;
  const ms = new Date(`${date}T${time}:00`).getTime();
  return Number.isFinite(ms) ? Math.floor(ms / 1000) : 0;
}

export function scheduleDtoToForm(schedule: HeartbeatScheduleDTO, fallbackTimezone: string): HeartbeatScheduleFormValue {
  if (schedule.type === 'interval') {
    return { kind: 'interval', intervalSeconds: schedule.interval_seconds, cronExpr: '', timezone: fallbackTimezone, onceDate: '', onceTime: '' };
  }
  if (schedule.type === 'cron') {
    return { kind: 'cron', intervalSeconds: 1800, cronExpr: schedule.cron_expr, timezone: schedule.timezone, onceDate: '', onceTime: '' };
  }
  const { date, time } = epochSecondsToOnceLocal(schedule.run_at);
  return { kind: 'once', intervalSeconds: 1800, cronExpr: '', timezone: fallbackTimezone, onceDate: date, onceTime: time };
}

export function scheduleFormToDto(form: HeartbeatScheduleFormValue): HeartbeatScheduleDTO {
  if (form.kind === 'interval') {
    const seconds = Math.max(MIN_INTERVAL_SECONDS, Math.floor(form.intervalSeconds) || 0);
    return { type: 'interval', interval_seconds: seconds };
  }
  if (form.kind === 'cron') {
    return { type: 'cron', cron_expr: form.cronExpr.trim(), timezone: form.timezone };
  }
  return { type: 'once', run_at: onceLocalToEpochSeconds(form.onceDate, form.onceTime) };
}

/** 列表/详情里的一行摘要文案，t 传 i18next 的 t 函数 */
export function summarizeHeartbeatSchedule(
  schedule: HeartbeatScheduleDTO,
  t: (key: string, opts?: Record<string, unknown>) => string,
): string {
  if (schedule.type === 'interval') {
    const minutes = Math.round(schedule.interval_seconds / 60);
    return t('heartbeat.schedule.summary.interval', { minutes });
  }
  if (schedule.type === 'cron') {
    return t('heartbeat.schedule.summary.cron', { expr: schedule.cron_expr, timezone: schedule.timezone });
  }
  const { date, time } = epochSecondsToOnceLocal(schedule.run_at);
  return t('heartbeat.schedule.summary.once', { date, time });
}
```

- [ ] **Step 4: 加编译+测试脚本**

在 `package.json` 的 `scripts` 块里，紧挨着其他 `test:cron-*`/`test:heartbeat-*` 脚本新增一行（参考 `test:cron-wake-offset` 的写法）：

```json
"test:heartbeat-schedule-convert": "tsc src/components/HeartbeatPanel/heartbeatScheduleConvert.ts --target ES2020 --module ES2020 --moduleResolution Bundler --rootDir src --outDir node_modules/.cache/heartbeat-schedule-convert --skipLibCheck --noEmitOnError && node --test tests/heartbeatScheduleConvert.test.mjs",
```

- [ ] **Step 5: 运行测试确认通过**

Run: `npm run test:heartbeat-schedule-convert`
Expected: 全部 9 个 test 用例 PASS

- [ ] **Step 6: Commit**

```bash
git add src/components/HeartbeatPanel/heartbeatScheduleConvert.ts tests/heartbeatScheduleConvert.test.mjs package.json
git commit -m "feat(heartbeat): add schedule form <-> DTO conversion helpers"
```

---

### Task 3: 心跳 Cron 表达式校验（5 段，复用 CronPanel 规则）

**Files:**
- Create: `src/components/HeartbeatPanel/heartbeatCronValidation.ts`
- Test: `tests/heartbeatCronValidation.test.mjs`
- Modify: `package.json`（新增 `test:heartbeat-cron-validation` 脚本）

**Interfaces:**
- Consumes: `validateCronExpr` from `../CronPanel/cronExprValidation`（已存在，7 段校验）
- Produces: `validateHeartbeatCronExpr(expr)` —— Task 6（HeartbeatScheduleEditor）会 import。

- [ ] **Step 1: 写失败的测试**

```js
// tests/heartbeatCronValidation.test.mjs
import assert from 'node:assert/strict';
import test from 'node:test';

import { validateHeartbeatCronExpr } from '../node_modules/.cache/heartbeat-cron-validation/components/HeartbeatPanel/heartbeatCronValidation.js';

test('accepts a valid 5-field weekday cron expression', () => {
  assert.deepEqual(validateHeartbeatCronExpr('0 9 * * 1-5'), { valid: true });
});

test('accepts a valid 5-field wildcard expression', () => {
  assert.deepEqual(validateHeartbeatCronExpr('*/15 * * * *'), { valid: true });
});

test('rejects expressions that are not exactly 5 fields', () => {
  assert.equal(validateHeartbeatCronExpr('0 9 * * * *').valid, false);
  assert.equal(validateHeartbeatCronExpr('0 9 * *').valid, false);
  assert.equal(validateHeartbeatCronExpr('9 * * *').valid, false);
});

test('rejects an out-of-range field and reuses CronPanel field-range error keys', () => {
  const result = validateHeartbeatCronExpr('0 25 * * *'); // 小时 25 超出 0-23
  assert.equal(result.valid, false);
  assert.equal(result.error, 'cron.errors.cronHour');
});

test('rejects an invalid weekday field', () => {
  const result = validateHeartbeatCronExpr('0 9 * * 8'); // 周字段 0-6，8 非法
  assert.equal(result.valid, false);
  assert.equal(result.error, 'cron.errors.cronWeek');
});

test('trims surrounding whitespace before field-count check', () => {
  assert.deepEqual(validateHeartbeatCronExpr('  0 9 * * 1-5  '), { valid: true });
});
```

- [ ] **Step 2: 运行测试确认失败**

Run: `node --test tests/heartbeatCronValidation.test.mjs`
Expected: FAIL（找不到编译产物模块）

- [ ] **Step 3: 写实现**

```ts
// src/components/HeartbeatPanel/heartbeatCronValidation.ts
// 心跳的 cron 计划用标准 5 段 crontab（分 时 日 月 周），跟 CronPanel 内部使用的 7 段 croniter
// 表达式（含 second/year）不是同一种格式，见接口规格说明 §4.1。校验时补全 second=0、year=* 后
// 复用 CronPanel 现成的字段范围规则（validateCronExpr），避免维护两套 min/max 规则并让两处校验
// 结果不一致。
import { validateCronExpr } from '../CronPanel/cronExprValidation';

export function validateHeartbeatCronExpr(expr: string): { valid: boolean; error?: string } {
  const trimmed = expr.trim();
  const parts = trimmed.split(/\s+/);
  if (parts.length !== 5) {
    return { valid: false, error: 'heartbeat.errors.cronFieldCount' };
  }
  return validateCronExpr(`0 ${trimmed} *`);
}
```

- [ ] **Step 4: 加编译+测试脚本**

```json
"test:heartbeat-cron-validation": "tsc src/components/HeartbeatPanel/heartbeatCronValidation.ts --target ES2020 --module ES2020 --moduleResolution Bundler --rootDir src --outDir node_modules/.cache/heartbeat-cron-validation --skipLibCheck --noEmitOnError && node --test tests/heartbeatCronValidation.test.mjs",
```

- [ ] **Step 5: 运行测试确认通过**

Run: `npm run test:heartbeat-cron-validation`
Expected: 全部 6 个 test 用例 PASS

- [ ] **Step 6: Commit**

```bash
git add src/components/HeartbeatPanel/heartbeatCronValidation.ts tests/heartbeatCronValidation.test.mjs package.json
git commit -m "feat(heartbeat): add 5-field cron validation reusing CronPanel rules"
```

---

### Task 4: 状态/操作结果 → 文案 key 映射（heartbeatStatusText）

**Files:**
- Create: `src/components/HeartbeatPanel/heartbeatStatusText.ts`
- Test: `tests/heartbeatStatusText.test.mjs`
- Modify: `package.json`（新增 `test:heartbeat-status-text` 脚本）

**Interfaces:**
- Consumes: `HeartbeatJobStatus`、`HeartbeatRunStatus`（Task 1）
- Produces: `HeartbeatStatusVariant`、`heartbeatStatusVariant(status)`、`heartbeatStatusLabelKey(status)`、`heartbeatRunNowMessageKey(accepted, reason?, queued?)`、`heartbeatCancelMessageKey(cancelStatus)`、`heartbeatLastRunStatusLabelKey(status)` —— Task 5（StatusBadge）、Task 10（行操作 toast）会 import。

- [ ] **Step 1: 写失败的测试**

```js
// tests/heartbeatStatusText.test.mjs
import assert from 'node:assert/strict';
import test from 'node:test';

import {
  heartbeatStatusVariant,
  heartbeatStatusLabelKey,
  heartbeatRunNowMessageKey,
  heartbeatCancelMessageKey,
  heartbeatLastRunStatusLabelKey,
} from '../node_modules/.cache/heartbeat-status-text/components/HeartbeatPanel/heartbeatStatusText.js';

test('heartbeatStatusVariant maps every backend status to a display variant', () => {
  assert.equal(heartbeatStatusVariant('scheduled'), 'scheduled');
  assert.equal(heartbeatStatusVariant('running'), 'running');
  assert.equal(heartbeatStatusVariant('disabled'), 'paused');
  assert.equal(heartbeatStatusVariant('completed'), 'completed');
  assert.equal(heartbeatStatusVariant('expired'), 'expired');
});

test('heartbeatStatusLabelKey namespaces under heartbeat.status.*', () => {
  assert.equal(heartbeatStatusLabelKey('running'), 'heartbeat.status.running');
  assert.equal(heartbeatStatusLabelKey('disabled'), 'heartbeat.status.paused');
});

test('heartbeatRunNowMessageKey: accepted without queue', () => {
  assert.equal(heartbeatRunNowMessageKey(true), 'heartbeat.toast.runNowAccepted');
});

test('heartbeatRunNowMessageKey: accepted and queued', () => {
  assert.equal(heartbeatRunNowMessageKey(true, undefined, true), 'heartbeat.toast.runNowQueued');
});

test('heartbeatRunNowMessageKey: rejected with a known reason', () => {
  assert.equal(heartbeatRunNowMessageKey(false, 'session_busy'), 'heartbeat.toast.runNowRejected.session_busy');
  assert.equal(
    heartbeatRunNowMessageKey(false, 'replacement_cancel_failed'),
    'heartbeat.toast.runNowRejected.replacement_cancel_failed',
  );
});

test('heartbeatRunNowMessageKey: rejected with an unrecognized reason falls back to unknown', () => {
  assert.equal(heartbeatRunNowMessageKey(false, 'something_new_from_backend'), 'heartbeat.toast.runNowRejected.unknown');
  assert.equal(heartbeatRunNowMessageKey(false, undefined), 'heartbeat.toast.runNowRejected.unknown');
});

test('heartbeatCancelMessageKey maps known cancel_status values', () => {
  assert.equal(heartbeatCancelMessageKey('idle'), 'heartbeat.toast.cancel.idle');
  assert.equal(heartbeatCancelMessageKey('cancelled'), 'heartbeat.toast.cancel.cancelled');
  assert.equal(heartbeatCancelMessageKey('not_found'), 'heartbeat.toast.cancel.not_found');
  assert.equal(heartbeatCancelMessageKey('failed'), 'heartbeat.toast.cancel.failed');
});

test('heartbeatCancelMessageKey falls back to failed for unknown values', () => {
  assert.equal(heartbeatCancelMessageKey('bogus'), 'heartbeat.toast.cancel.failed');
});

test('heartbeatLastRunStatusLabelKey returns null for null input, key otherwise', () => {
  assert.equal(heartbeatLastRunStatusLabelKey(null), null);
  assert.equal(heartbeatLastRunStatusLabelKey('failed'), 'heartbeat.runState.failed');
  assert.equal(heartbeatLastRunStatusLabelKey('skipped'), 'heartbeat.runState.skipped');
});
```

- [ ] **Step 2: 运行测试确认失败**

Run: `node --test tests/heartbeatStatusText.test.mjs`
Expected: FAIL（找不到编译产物模块）

- [ ] **Step 3: 写实现**

```ts
// src/components/HeartbeatPanel/heartbeatStatusText.ts
import type { HeartbeatJobStatus, HeartbeatRunStatus } from '../../types/heartbeat';

export type HeartbeatStatusVariant = 'running' | 'scheduled' | 'paused' | 'completed' | 'expired';

/** 状态展示口径完全来自服务端 status 字段，不由 enabled 反推，见接口规格说明 §10.12 */
export function heartbeatStatusVariant(status: HeartbeatJobStatus): HeartbeatStatusVariant {
  switch (status) {
    case 'running':
      return 'running';
    case 'scheduled':
      return 'scheduled';
    case 'disabled':
      return 'paused';
    case 'completed':
      return 'completed';
    case 'expired':
      return 'expired';
    default:
      return 'paused';
  }
}

export function heartbeatStatusLabelKey(status: HeartbeatJobStatus): string {
  return `heartbeat.status.${heartbeatStatusVariant(status)}`;
}

const KNOWN_RUN_NOW_REJECT_REASONS = [
  'session_missing',
  'session_busy',
  'previous_run_active',
  'already_queued',
  'replacement_pending',
  'replacement_cancel_failed',
  'job_disabled_during_replace',
];

/**
 * run_now 结果 -> 文案 key。accepted=true 才能提示"已接收/已开始执行"，绝不显示"执行成功"；
 * accepted=false 按 reason 显示具体原因，不显示"RPC 失败"，见接口规格说明 §16.7。
 */
export function heartbeatRunNowMessageKey(accepted: boolean, reason?: string, queued?: boolean): string {
  if (accepted) {
    return queued ? 'heartbeat.toast.runNowQueued' : 'heartbeat.toast.runNowAccepted';
  }
  const key = reason && KNOWN_RUN_NOW_REJECT_REASONS.includes(reason) ? reason : 'unknown';
  return `heartbeat.toast.runNowRejected.${key}`;
}

const KNOWN_CANCEL_STATUSES = ['idle', 'cancelled', 'not_found', 'failed'];

/** cancel_status -> 文案 key；not_found 不能显示"取消成功"，见接口规格说明 §16.7 */
export function heartbeatCancelMessageKey(cancelStatus: string): string {
  const key = KNOWN_CANCEL_STATUSES.includes(cancelStatus) ? cancelStatus : 'failed';
  return `heartbeat.toast.cancel.${key}`;
}

export function heartbeatLastRunStatusLabelKey(status: HeartbeatRunStatus | null): string | null {
  if (!status) return null;
  return `heartbeat.runState.${status}`;
}
```

- [ ] **Step 4: 加编译+测试脚本**

```json
"test:heartbeat-status-text": "tsc src/components/HeartbeatPanel/heartbeatStatusText.ts --target ES2020 --module ES2020 --moduleResolution Bundler --rootDir src --outDir node_modules/.cache/heartbeat-status-text --skipLibCheck --noEmitOnError && node --test tests/heartbeatStatusText.test.mjs",
```

- [ ] **Step 5: 运行测试确认通过**

Run: `npm run test:heartbeat-status-text`
Expected: 全部 9 个 test 用例 PASS

- [ ] **Step 6: Commit**

```bash
git add src/components/HeartbeatPanel/heartbeatStatusText.ts tests/heartbeatStatusText.test.mjs package.json
git commit -m "feat(heartbeat): add status/result to i18n-key mapping helpers"
```

---

### Task 5: HeartbeatStatusBadge 组件 + i18n 状态文案

**Files:**
- Create: `src/components/HeartbeatPanel/HeartbeatStatusBadge.tsx`
- Modify: `src/i18n/locales/zh.json`, `src/i18n/locales/en.json`

**Interfaces:**
- Consumes: `HeartbeatJobStatus`（Task 1）、`heartbeatStatusVariant`/`heartbeatStatusLabelKey`（Task 4）、`RunningIcon`/`BoldRingIcon`（已存在，`../CronPanel/StatusBadge`）
- Produces: `<HeartbeatStatusBadge status={HeartbeatJobStatus} />` —— Task 8（列表渲染）会用。

- [ ] **Step 1: 写组件**

```tsx
// src/components/HeartbeatPanel/HeartbeatStatusBadge.tsx
import { useTranslation } from 'react-i18next';
import type { HeartbeatJobStatus } from '../../types/heartbeat';
import { heartbeatStatusVariant, heartbeatStatusLabelKey, type HeartbeatStatusVariant } from './heartbeatStatusText';
import { RunningIcon, BoldRingIcon } from '../CronPanel/StatusBadge';

const VARIANT_CLASS: Record<HeartbeatStatusVariant, string> = {
  running: 'text-cron-running',
  scheduled: 'text-cron-running',
  paused: 'text-text-muted',
  completed: 'text-text-muted',
  expired: 'text-amber-600',
};

export default function HeartbeatStatusBadge({ status }: { status: HeartbeatJobStatus }) {
  const { t } = useTranslation();
  const variant = heartbeatStatusVariant(status);
  const Icon = variant === 'running' ? RunningIcon : BoldRingIcon;
  return (
    <span className={`inline-flex items-center gap-1.5 text-sm ${VARIANT_CLASS[variant]}`}>
      <Icon />
      {t(heartbeatStatusLabelKey(status))}
    </span>
  );
}
```

- [ ] **Step 2: 加 i18n key**

在 `src/i18n/locales/zh.json` 顶层新增 `heartbeat` 对象（后续任务继续往这个对象里追加 key，不要新建第二个 `heartbeat` 顶层 key）：

```json
"heartbeat": {
  "status": {
    "running": "执行中",
    "scheduled": "已排期",
    "paused": "已暂停",
    "completed": "已完成",
    "expired": "已过期"
  }
}
```

在 `src/i18n/locales/en.json` 对应位置：

```json
"heartbeat": {
  "status": {
    "running": "Running",
    "scheduled": "Scheduled",
    "paused": "Paused",
    "completed": "Completed",
    "expired": "Expired"
  }
}
```

- [ ] **Step 3: 类型检查**

Run: `npx tsc src/components/HeartbeatPanel/HeartbeatStatusBadge.tsx --target ES2020 --module ESNext --moduleResolution Bundler --jsx react-jsx --rootDir src --outDir node_modules/.cache/heartbeat-status-badge --skipLibCheck --noEmitOnError`
Expected: 无输出、退出码 0

- [ ] **Step 4: Commit**

```bash
git add src/components/HeartbeatPanel/HeartbeatStatusBadge.tsx src/i18n/locales/zh.json src/i18n/locales/en.json
git commit -m "feat(heartbeat): add HeartbeatStatusBadge component"
```

---

### Task 6: HeartbeatScheduleEditor 组件（interval/cron/once）

**Files:**
- Create: `src/components/HeartbeatPanel/HeartbeatScheduleEditor.tsx`
- Modify: `src/i18n/locales/zh.json`, `src/i18n/locales/en.json`

**Interfaces:**
- Consumes: `HeartbeatScheduleFormValue`（Task 2）、`validateHeartbeatCronExpr`（Task 3）、`TIMEZONE_OPTIONS`（已存在，`../CronPanel/constants`）、`SimpleSelect`（已存在，`../CronPanel/SimpleSelect`）
- Produces: `<HeartbeatScheduleEditor value onChange minIntervalSeconds />` —— Task 7（HeartbeatTaskDrawer）会用。

- [ ] **Step 1: 写组件**

```tsx
// src/components/HeartbeatPanel/HeartbeatScheduleEditor.tsx
import { useTranslation } from 'react-i18next';
import type { HeartbeatScheduleFormValue } from './heartbeatScheduleConvert';
import type { HeartbeatScheduleKind } from '../../types/heartbeat';
import { validateHeartbeatCronExpr } from './heartbeatCronValidation';
import { TIMEZONE_OPTIONS } from '../CronPanel/constants';
import SimpleSelect from '../CronPanel/SimpleSelect';

interface HeartbeatScheduleEditorProps {
  value: HeartbeatScheduleFormValue;
  onChange: (value: HeartbeatScheduleFormValue) => void;
  /** 来自 heartbeat.job.meta 的 limits.min_interval_seconds，缺省 60 */
  minIntervalSeconds: number;
}

const KIND_TABS: HeartbeatScheduleKind[] = ['interval', 'cron', 'once'];
const TIMEZONE_SELECT_OPTIONS = TIMEZONE_OPTIONS.map((tz) => ({ value: tz, label: tz }));

export default function HeartbeatScheduleEditor({ value, onChange, minIntervalSeconds }: HeartbeatScheduleEditorProps) {
  const { t } = useTranslation();
  const minIntervalMinutes = Math.max(1, Math.ceil(minIntervalSeconds / 60));
  const intervalMinutes = Math.max(minIntervalMinutes, Math.round(value.intervalSeconds / 60));
  const cronError =
    value.kind === 'cron' && value.cronExpr.trim() ? validateHeartbeatCronExpr(value.cronExpr).error : undefined;

  return (
    <div className="space-y-3">
      <div className="flex gap-2">
        {KIND_TABS.map((kind) => (
          <button
            key={kind}
            type="button"
            onClick={() => onChange({ ...value, kind })}
            className={`rounded-full px-3 py-1 text-sm ${
              value.kind === kind ? 'bg-cron-action font-bold text-cron-action-foreground' : 'border border-border text-text'
            }`}
          >
            {t(`heartbeat.schedule.tab.${kind}`)}
          </button>
        ))}
      </div>

      {value.kind === 'interval' && (
        <div className="flex items-center gap-2">
          <span className="text-sm text-text">{t('heartbeat.schedule.interval.label')}</span>
          <input
            type="number"
            min={minIntervalMinutes}
            value={intervalMinutes}
            onChange={(e) => {
              const minutes = Math.max(minIntervalMinutes, Math.floor(Number(e.target.value) || minIntervalMinutes));
              onChange({ ...value, intervalSeconds: minutes * 60 });
            }}
            className="w-24 rounded-md border border-border bg-card px-2 py-1 text-sm"
          />
          <span className="text-sm text-text-muted">{t('heartbeat.schedule.interval.unit')}</span>
        </div>
      )}

      {value.kind === 'cron' && (
        <div className="space-y-2">
          <input
            type="text"
            placeholder="0 9 * * 1-5"
            value={value.cronExpr}
            onChange={(e) => onChange({ ...value, cronExpr: e.target.value })}
            className="w-full rounded-md border border-border bg-card px-2 py-1 text-sm font-mono"
          />
          {cronError && <p className="text-xs text-red-500">{t(cronError)}</p>}
          <SimpleSelect
            value={value.timezone}
            onChange={(v) => onChange({ ...value, timezone: v })}
            options={TIMEZONE_SELECT_OPTIONS}
            className="w-48"
          />
        </div>
      )}

      {value.kind === 'once' && (
        <div className="flex items-center gap-2">
          <input
            type="date"
            value={value.onceDate}
            onChange={(e) => onChange({ ...value, onceDate: e.target.value })}
            className="rounded-md border border-border bg-card px-2 py-1 text-sm"
          />
          <input
            type="time"
            value={value.onceTime}
            onChange={(e) => onChange({ ...value, onceTime: e.target.value })}
            className="rounded-md border border-border bg-card px-2 py-1 text-sm"
          />
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 2: 加 i18n key**

在 zh.json 的 `heartbeat` 对象里追加（跟 Task 5 已有的 `status` 同级）：

```json
"schedule": {
  "tab": { "interval": "按间隔", "cron": "Cron 表达式", "once": "单次" },
  "interval": { "label": "每隔", "unit": "分钟回到当前会话" },
  "summary": {
    "interval": "每 {{minutes}} 分钟回到当前会话",
    "cron": "{{expr}}（{{timezone}}）回到当前会话",
    "once": "{{date}} {{time}} 回到当前会话一次"
  }
}
```

en.json 对应：

```json
"schedule": {
  "tab": { "interval": "Interval", "cron": "Cron expression", "once": "Once" },
  "interval": { "label": "Every", "unit": "minutes, return to this session" },
  "summary": {
    "interval": "Return to this session every {{minutes}} min",
    "cron": "{{expr}} ({{timezone}}), return to this session",
    "once": "Return to this session once at {{date}} {{time}}"
  }
}
```

同时在 zh.json/en.json 的 `heartbeat` 对象里加一个跟 `schedule` 同级的 `errors` block（`heartbeatCronValidation.ts` 在字段数不对时用到）：

zh.json:
```json
"errors": {
  "cronFieldCount": "心跳的 Cron 表达式需要 5 段（分 时 日 月 周）"
}
```

en.json:
```json
"errors": {
  "cronFieldCount": "Heartbeat cron expressions must have 5 fields (minute hour day month weekday)"
}
```

- [ ] **Step 3: 类型检查**

Run: `npx tsc src/components/HeartbeatPanel/HeartbeatScheduleEditor.tsx --target ES2020 --module ESNext --moduleResolution Bundler --jsx react-jsx --rootDir src --outDir node_modules/.cache/heartbeat-schedule-editor --skipLibCheck --noEmitOnError`
Expected: 无输出、退出码 0

- [ ] **Step 4: Commit**

```bash
git add src/components/HeartbeatPanel/HeartbeatScheduleEditor.tsx src/i18n/locales/zh.json src/i18n/locales/en.json
git commit -m "feat(heartbeat): add HeartbeatScheduleEditor component"
```

---

### Task 7: HeartbeatTaskDrawer 组件（创建/编辑表单）

**Files:**
- Create: `src/components/HeartbeatPanel/HeartbeatTaskDrawer.tsx`
- Modify: `src/i18n/locales/zh.json`, `src/i18n/locales/en.json`

**Interfaces:**
- Consumes: `HeartbeatTaskUI`、`HeartbeatMeta`、`HeartbeatConcurrencyPolicy`、`HeartbeatSessionDeletedPolicy`（Task 1）、`HeartbeatScheduleFormValue`/`emptyHeartbeatScheduleForm`/`scheduleDtoToForm`（Task 2）、`validateHeartbeatCronExpr`（Task 3）、`HeartbeatScheduleEditor`（Task 6）、`SimpleSelect`（已存在）
- Produces: `HeartbeatTaskFormValue`、`emptyHeartbeatTaskForm(meta)`、`jobToHeartbeatTaskForm(job)`、`<HeartbeatTaskDrawer mode initial meta onSubmit onCancel submitting error />` —— Task 9（HeartbeatPanel 创建/编辑接线）会用。

- [ ] **Step 1: 写组件**

```tsx
// src/components/HeartbeatPanel/HeartbeatTaskDrawer.tsx
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import type { HeartbeatConcurrencyPolicy, HeartbeatMeta, HeartbeatSessionDeletedPolicy, HeartbeatTaskUI } from '../../types/heartbeat';
import {
  emptyHeartbeatScheduleForm,
  scheduleDtoToForm,
  type HeartbeatScheduleFormValue,
} from './heartbeatScheduleConvert';
import { validateHeartbeatCronExpr } from './heartbeatCronValidation';
import HeartbeatScheduleEditor from './HeartbeatScheduleEditor';
import SimpleSelect from '../CronPanel/SimpleSelect';

const NAME_MAX_LENGTH = 64;
const PROMPT_MAX_LENGTH = 2000;

export interface HeartbeatTaskFormValue {
  name: string;
  prompt: string;
  schedule: HeartbeatScheduleFormValue;
  concurrencyPolicy: HeartbeatConcurrencyPolicy;
  sessionDeletedPolicy: HeartbeatSessionDeletedPolicy;
  maxRuns: number | null;
  enabled: boolean;
}

export function emptyHeartbeatTaskForm(meta: HeartbeatMeta): HeartbeatTaskFormValue {
  return {
    name: '',
    prompt: '',
    schedule: emptyHeartbeatScheduleForm('Asia/Shanghai'),
    concurrencyPolicy: meta.limits.default_concurrency_policy,
    sessionDeletedPolicy: meta.limits.default_session_deleted_policy,
    maxRuns: meta.limits.default_max_runs,
    enabled: true,
  };
}

export function jobToHeartbeatTaskForm(job: HeartbeatTaskUI): HeartbeatTaskFormValue {
  return {
    name: job.name,
    prompt: job.prompt,
    schedule: scheduleDtoToForm(job.schedule, job.timezone),
    concurrencyPolicy: job.concurrencyPolicy,
    sessionDeletedPolicy: job.sessionDeletedPolicy,
    maxRuns: job.maxRuns,
    enabled: job.enabled,
  };
}

interface HeartbeatTaskDrawerProps {
  mode: 'create' | 'edit';
  initial: HeartbeatTaskFormValue;
  meta: HeartbeatMeta;
  submitting: boolean;
  error: string | null;
  onSubmit: (value: HeartbeatTaskFormValue) => void;
  onCancel: () => void;
}

export default function HeartbeatTaskDrawer({ mode, initial, meta, submitting, error, onSubmit, onCancel }: HeartbeatTaskDrawerProps) {
  const { t } = useTranslation();
  const [form, setForm] = useState<HeartbeatTaskFormValue>(initial);

  const concurrencyOptions = meta.concurrency_policies.map((p) => ({ value: p, label: t(`heartbeat.concurrencyPolicy.${p}`) }));
  const sessionDeletedOptions = meta.session_deleted_policies.map((p) => ({
    value: p,
    label: t(`heartbeat.sessionDeletedPolicy.${p}`),
  }));

  const missingFieldLabels: string[] = [];
  if (!form.name.trim()) missingFieldLabels.push(t('heartbeat.drawer.fieldName'));
  if (form.name.length > NAME_MAX_LENGTH) missingFieldLabels.push(t('heartbeat.drawer.fieldNameTooLong'));
  if (!form.prompt.trim()) missingFieldLabels.push(t('heartbeat.drawer.fieldPrompt'));
  if (form.prompt.length > PROMPT_MAX_LENGTH) missingFieldLabels.push(t('heartbeat.drawer.fieldPromptTooLong'));
  if (form.schedule.kind === 'cron') {
    const cronCheck = validateHeartbeatCronExpr(form.schedule.cronExpr);
    if (!form.schedule.cronExpr.trim() || !cronCheck.valid) missingFieldLabels.push(t('heartbeat.drawer.fieldSchedule'));
  }
  if (form.schedule.kind === 'once' && (!form.schedule.onceDate || !form.schedule.onceTime)) {
    missingFieldLabels.push(t('heartbeat.drawer.fieldSchedule'));
  }
  const canSubmit = missingFieldLabels.length === 0 && !submitting;

  return (
    <div className="space-y-4 p-4">
      <div>
        <label className="mb-1 block text-sm text-text-muted">{t('heartbeat.drawer.fieldName')}</label>
        <input
          type="text"
          value={form.name}
          maxLength={NAME_MAX_LENGTH}
          onChange={(e) => setForm({ ...form, name: e.target.value })}
          className="w-full rounded-md border border-border bg-card px-2 py-1.5 text-sm"
        />
      </div>
      <div>
        <label className="mb-1 block text-sm text-text-muted">{t('heartbeat.drawer.fieldPrompt')}</label>
        <textarea
          value={form.prompt}
          maxLength={PROMPT_MAX_LENGTH}
          rows={4}
          onChange={(e) => setForm({ ...form, prompt: e.target.value })}
          className="w-full rounded-md border border-border bg-card px-2 py-1.5 text-sm"
        />
      </div>
      <div>
        <label className="mb-1 block text-sm text-text-muted">{t('heartbeat.drawer.fieldSchedule')}</label>
        <HeartbeatScheduleEditor
          value={form.schedule}
          onChange={(schedule) => setForm({ ...form, schedule })}
          minIntervalSeconds={meta.limits.min_interval_seconds}
        />
      </div>
      <div className="flex gap-4">
        <div className="flex-1">
          <label className="mb-1 block text-sm text-text-muted">{t('heartbeat.drawer.fieldConcurrencyPolicy')}</label>
          <SimpleSelect
            value={form.concurrencyPolicy}
            onChange={(v) => setForm({ ...form, concurrencyPolicy: v as HeartbeatConcurrencyPolicy })}
            options={concurrencyOptions}
          />
        </div>
        <div className="flex-1">
          <label className="mb-1 block text-sm text-text-muted">{t('heartbeat.drawer.fieldSessionDeletedPolicy')}</label>
          <SimpleSelect
            value={form.sessionDeletedPolicy}
            onChange={(v) => setForm({ ...form, sessionDeletedPolicy: v as HeartbeatSessionDeletedPolicy })}
            options={sessionDeletedOptions}
          />
        </div>
      </div>
      <div>
        <label className="mb-1 block text-sm text-text-muted">{t('heartbeat.drawer.fieldMaxRuns')}</label>
        <input
          type="number"
          min={1}
          value={form.maxRuns ?? ''}
          placeholder={t('heartbeat.drawer.fieldMaxRunsUnlimited') ?? ''}
          onChange={(e) => setForm({ ...form, maxRuns: e.target.value === '' ? null : Math.max(1, Number(e.target.value)) })}
          className="w-32 rounded-md border border-border bg-card px-2 py-1.5 text-sm"
        />
      </div>

      {error && <p className="text-sm text-red-500">{error}</p>}
      {!error && missingFieldLabels.length > 0 && (
        <p className="text-xs text-text-muted">{t('heartbeat.drawer.missingFields', { fields: missingFieldLabels.join('、') })}</p>
      )}

      <div className="flex justify-end gap-2 pt-2">
        <button
          type="button"
          onClick={onCancel}
          className="rounded-full border border-border bg-card px-6 py-1.5 text-sm text-text hover:bg-bg-hover"
        >
          {t('common.cancel')}
        </button>
        <button
          type="button"
          disabled={!canSubmit}
          onClick={() => onSubmit(form)}
          className="rounded-full bg-cron-action px-6 py-1.5 text-sm font-bold text-cron-action-foreground hover:bg-cron-action-hover disabled:cursor-not-allowed disabled:opacity-60"
        >
          {mode === 'create' ? t('heartbeat.drawer.submitCreate') : t('heartbeat.drawer.submitUpdate')}
        </button>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: 加 i18n key**

zh.json 的 `heartbeat` 对象追加：

```json
"drawer": {
  "fieldName": "任务名称",
  "fieldNameTooLong": "任务名称不能超过 64 字符",
  "fieldPrompt": "续跑提示词",
  "fieldPromptTooLong": "续跑提示词不能超过 2000 字符",
  "fieldSchedule": "调度计划",
  "fieldConcurrencyPolicy": "重叠运行策略",
  "fieldSessionDeletedPolicy": "会话删除后策略",
  "fieldMaxRuns": "最大触发次数",
  "fieldMaxRunsUnlimited": "不限制",
  "missingFields": "还需要填写：{{fields}}",
  "submitCreate": "创建",
  "submitUpdate": "保存"
},
"concurrencyPolicy": { "skip": "跳过（skip）", "queue": "排队（queue）", "replace": "替换（replace）" },
"sessionDeletedPolicy": { "disable": "停用任务", "completed": "标记为已完成" }
```

en.json 对应：

```json
"drawer": {
  "fieldName": "Task name",
  "fieldNameTooLong": "Name must be at most 64 characters",
  "fieldPrompt": "Follow-up prompt",
  "fieldPromptTooLong": "Prompt must be at most 2000 characters",
  "fieldSchedule": "Schedule",
  "fieldConcurrencyPolicy": "Concurrency policy",
  "fieldSessionDeletedPolicy": "On session deleted",
  "fieldMaxRuns": "Max runs",
  "fieldMaxRunsUnlimited": "Unlimited",
  "missingFields": "Still needed: {{fields}}",
  "submitCreate": "Create",
  "submitUpdate": "Save"
},
"concurrencyPolicy": { "skip": "Skip", "queue": "Queue", "replace": "Replace" },
"sessionDeletedPolicy": { "disable": "Disable job", "completed": "Mark completed" }
```

- [ ] **Step 3: 类型检查**

Run: `npx tsc src/components/HeartbeatPanel/HeartbeatTaskDrawer.tsx --target ES2020 --module ESNext --moduleResolution Bundler --jsx react-jsx --rootDir src --outDir node_modules/.cache/heartbeat-task-drawer --skipLibCheck --noEmitOnError`
Expected: 无输出、退出码 0

- [ ] **Step 4: Commit**

```bash
git add src/components/HeartbeatPanel/HeartbeatTaskDrawer.tsx src/i18n/locales/zh.json src/i18n/locales/en.json
git commit -m "feat(heartbeat): add HeartbeatTaskDrawer create/edit form"
```

---

### Task 8: HeartbeatPanel 容器 —— 抽屉外壳 + meta/list 只读展示

**Files:**
- Create: `src/components/HeartbeatPanel/index.tsx`
- Modify: `src/i18n/locales/zh.json`, `src/i18n/locales/en.json`

**Interfaces:**
- Consumes: `webRequest`（已存在，`../../services/webClient`）、`HeartbeatJobDTO`/`HeartbeatMeta`/`HeartbeatTaskUI`（Task 1）、`summarizeHeartbeatSchedule`（Task 2）、`HeartbeatStatusBadge`（Task 5）
- Produces: `<HeartbeatPanel sessionId={string} onClose={() => void} />` —— Task 11（ChatPanel 挂载）会用；`heartbeatJobToUI`、内部 `jobs`/`meta`/`loading`/`loadError` state —— Task 9、Task 10 会继续在这个文件里加代码。

- [ ] **Step 1: 写容器组件（只读列表）**

```tsx
// src/components/HeartbeatPanel/index.tsx
import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { X } from 'lucide-react';
import { webRequest } from '../../services/webClient';
import type { WebError } from '../../types';
import type { HeartbeatJobDTO, HeartbeatMeta, HeartbeatTaskUI } from '../../types/heartbeat';
import { summarizeHeartbeatSchedule } from './heartbeatScheduleConvert';
import HeartbeatStatusBadge from './HeartbeatStatusBadge';

interface HeartbeatPanelProps {
  sessionId: string;
  onClose: () => void;
}

function heartbeatJobToUI(job: HeartbeatJobDTO): HeartbeatTaskUI {
  return {
    id: job.id,
    name: job.name,
    prompt: job.prompt,
    enabled: job.enabled,
    status: job.status,
    schedule: job.schedule,
    timezone: job.timezone,
    concurrencyPolicy: job.concurrency_policy,
    sessionDeletedPolicy: job.session_deleted_policy,
    maxRuns: job.max_runs,
    createdAt: job.created_at,
    updatedAt: job.updated_at,
    nextRunAt: job.next_run_at,
    lastRunAt: job.last_run_at,
    runCount: job.run_count,
    runState: job.run_state,
  };
}

export default function HeartbeatPanel({ sessionId, onClose }: HeartbeatPanelProps) {
  const { t } = useTranslation();
  const [meta, setMeta] = useState<HeartbeatMeta | null>(null);
  const [jobs, setJobs] = useState<HeartbeatTaskUI[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  // 会话切换/组件卸载时中止未完成请求，避免旧会话的响应覆盖新会话状态，见接口规格说明 §16.3
  const sessionIdRef = useRef(sessionId);
  sessionIdRef.current = sessionId;

  const loadAll = useCallback(async (signal: AbortSignal) => {
    setLoading(true);
    setLoadError(null);
    try {
      const [metaPayload, listPayload] = await Promise.all([
        webRequest<HeartbeatMeta>('heartbeat.job.meta', { session_id: sessionId }, { signal }),
        webRequest<{ jobs: HeartbeatJobDTO[] }>('heartbeat.job.list', { session_id: sessionId }, { signal }),
      ]);
      if (sessionIdRef.current !== sessionId) return; // 会话已切换，丢弃过期响应
      setMeta(metaPayload);
      setJobs((listPayload.jobs ?? []).map(heartbeatJobToUI));
    } catch (err) {
      if ((err as { name?: string }).name === 'AbortError') return;
      if (sessionIdRef.current !== sessionId) return;
      setLoadError(err instanceof Error ? err.message : String(err));
    } finally {
      if (sessionIdRef.current === sessionId) setLoading(false);
    }
  }, [sessionId]);

  useEffect(() => {
    const controller = new AbortController();
    void loadAll(controller.signal);
    return () => controller.abort();
  }, [loadAll]);

  return (
    <div className="fixed inset-0 z-40 flex justify-end bg-overlay-cron-dialog" onClick={onClose}>
      <div
        className="flex h-full w-[520px] max-w-full flex-col bg-card shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-border p-4">
          <h2 className="text-lg font-bold text-text-strong">{t('heartbeat.panel.title')}</h2>
          <button onClick={onClose} className="text-text-muted hover:text-text">
            <X size={20} />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-4">
          {loading && <p className="text-sm text-text-muted">{t('heartbeat.panel.loading')}</p>}
          {!loading && loadError && <p className="text-sm text-red-500">{loadError}</p>}
          {!loading && !loadError && jobs.length === 0 && (
            <p className="text-sm text-text-muted">{t('heartbeat.panel.empty')}</p>
          )}
          {!loading && !loadError && jobs.length > 0 && meta && (
            <ul className="space-y-3">
              {jobs.map((job) => (
                <li key={job.id} className="rounded-lg border border-border p-3">
                  <div className="flex items-center justify-between">
                    <span className="font-medium text-text-strong">{job.name}</span>
                    <HeartbeatStatusBadge status={job.status} />
                  </div>
                  <p className="mt-1 line-clamp-2 text-sm text-text-muted">{job.prompt}</p>
                  <p className="mt-1 text-xs text-text-muted">{summarizeHeartbeatSchedule(job.schedule, t)}</p>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: 加 i18n key**

zh.json 的 `heartbeat` 对象追加：

```json
"panel": {
  "title": "心跳任务",
  "loading": "加载中…",
  "empty": "当前会话还没有心跳任务"
}
```

en.json：

```json
"panel": {
  "title": "Heartbeat Jobs",
  "loading": "Loading…",
  "empty": "No heartbeat jobs in this session yet"
}
```

- [ ] **Step 3: 类型检查**

Run: `npx tsc src/components/HeartbeatPanel/index.tsx --target ES2020 --module ESNext --moduleResolution Bundler --jsx react-jsx --rootDir src --outDir node_modules/.cache/heartbeat-panel --skipLibCheck --noEmitOnError`
Expected: 无输出、退出码 0

- [ ] **Step 4: Commit**

```bash
git add src/components/HeartbeatPanel/index.tsx src/i18n/locales/zh.json src/i18n/locales/en.json
git commit -m "feat(heartbeat): add HeartbeatPanel shell with meta/list loading"
```

---

### Task 9: HeartbeatPanel —— 创建/编辑接线

**Files:**
- Modify: `src/components/HeartbeatPanel/index.tsx`
- Modify: `src/i18n/locales/zh.json`, `src/i18n/locales/en.json`

**Interfaces:**
- Consumes: `HeartbeatTaskDrawer`/`emptyHeartbeatTaskForm`/`jobToHeartbeatTaskForm`/`HeartbeatTaskFormValue`（Task 7）、`scheduleFormToDto`（Task 2）
- Produces: 在 `jobs` 列表里新增"新建任务"入口按钮、每行"编辑"按钮；`drawerState`（`{mode:'create'|'edit', form, submitting, error} | null`）状态供 Task 10 复用同一个抽屉渲染块。

- [ ] **Step 1: 在 index.tsx 顶部新增 import，并加抽屉状态与提交逻辑**

在现有 import 块末尾追加：

```tsx
import HeartbeatTaskDrawer, {
  emptyHeartbeatTaskForm,
  jobToHeartbeatTaskForm,
  type HeartbeatTaskFormValue,
} from './HeartbeatTaskDrawer';
import { scheduleFormToDto } from './heartbeatScheduleConvert';
```

在 `HeartbeatPanel` 函数体内、`loadAll`/`useEffect` 之后新增：

```tsx
  const [drawer, setDrawer] = useState<
    | { mode: 'create'; form: HeartbeatTaskFormValue; submitting: boolean; error: string | null }
    | { mode: 'edit'; jobId: string; form: HeartbeatTaskFormValue; submitting: boolean; error: string | null }
    | null
  >(null);

  const openCreateDrawer = useCallback(() => {
    if (!meta) return;
    setDrawer({ mode: 'create', form: emptyHeartbeatTaskForm(meta), submitting: false, error: null });
  }, [meta]);

  const openEditDrawer = useCallback((job: HeartbeatTaskUI) => {
    setDrawer({ mode: 'edit', jobId: job.id, form: jobToHeartbeatTaskForm(job), submitting: false, error: null });
  }, []);

  const submitDrawer = useCallback(
    async (value: HeartbeatTaskFormValue) => {
      if (!drawer) return;
      setDrawer({ ...drawer, form: value, submitting: true, error: null });
      const payload = {
        name: value.name.trim(),
        prompt: value.prompt.trim(),
        schedule: scheduleFormToDto(value.schedule),
        timezone: value.schedule.timezone,
        enabled: value.enabled,
        concurrency_policy: value.concurrencyPolicy,
        session_deleted_policy: value.sessionDeletedPolicy,
        max_runs: value.maxRuns,
      };
      try {
        if (drawer.mode === 'create') {
          await webRequest<{ job: HeartbeatJobDTO }>('heartbeat.job.create', { session_id: sessionId, ...payload });
        } else {
          await webRequest<{ job: HeartbeatJobDTO }>('heartbeat.job.update', {
            session_id: sessionId,
            id: drawer.jobId,
            patch: payload,
          });
        }
        setDrawer(null);
        const controller = new AbortController();
        await loadAll(controller.signal);
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        setDrawer((prev) => (prev ? { ...prev, submitting: false, error: message } : prev));
      }
    },
    [drawer, sessionId, loadAll],
  );
```

- [ ] **Step 2: 在 header 里加"新建"按钮，在每个 job 卡片里加"编辑"按钮，并在组件末尾渲染抽屉**

把 header 的 `<div className="flex items-center justify-between border-b border-border p-4">` 内容替换为：

```tsx
        <div className="flex items-center justify-between border-b border-border p-4">
          <h2 className="text-lg font-bold text-text-strong">{t('heartbeat.panel.title')}</h2>
          <div className="flex items-center gap-3">
            <button
              type="button"
              disabled={!meta}
              onClick={openCreateDrawer}
              className="rounded-full bg-cron-action px-4 py-1.5 text-sm font-bold text-cron-action-foreground hover:bg-cron-action-hover disabled:cursor-not-allowed disabled:opacity-60"
            >
              {t('heartbeat.panel.create')}
            </button>
            <button onClick={onClose} className="text-text-muted hover:text-text">
              <X size={20} />
            </button>
          </div>
        </div>
```

把每个 job 卡片（`<li key={job.id} ...>`）内部末尾追加编辑按钮：

```tsx
                <li key={job.id} className="rounded-lg border border-border p-3">
                  <div className="flex items-center justify-between">
                    <span className="font-medium text-text-strong">{job.name}</span>
                    <HeartbeatStatusBadge status={job.status} />
                  </div>
                  <p className="mt-1 line-clamp-2 text-sm text-text-muted">{job.prompt}</p>
                  <p className="mt-1 text-xs text-text-muted">{summarizeHeartbeatSchedule(job.schedule, t)}</p>
                  <div className="mt-2 flex justify-end">
                    <button
                      type="button"
                      onClick={() => openEditDrawer(job)}
                      className="rounded-full border border-border px-3 py-1 text-xs text-text hover:bg-bg-hover"
                    >
                      {t('heartbeat.panel.edit')}
                    </button>
                  </div>
                </li>
```

在最外层容器 `</div>`（抽屉主体 `flex h-full w-[520px] ...` 的关闭标签）之前追加抽屉渲染：

```tsx
        {drawer && meta && (
          <div className="border-t border-border">
            <HeartbeatTaskDrawer
              mode={drawer.mode}
              initial={drawer.form}
              meta={meta}
              submitting={drawer.submitting}
              error={drawer.error}
              onSubmit={submitDrawer}
              onCancel={() => setDrawer(null)}
            />
          </div>
        )}
```

- [ ] **Step 3: 加 i18n key**

zh.json 的 `heartbeat.panel` 对象追加两个 key（跟已有的 `title`/`loading`/`empty` 同级）：

```json
"create": "新建心跳任务",
"edit": "编辑"
```

en.json 对应：

```json
"create": "New heartbeat job",
"edit": "Edit"
```

- [ ] **Step 4: 类型检查**

Run: `npx tsc src/components/HeartbeatPanel/index.tsx --target ES2020 --module ESNext --moduleResolution Bundler --jsx react-jsx --rootDir src --outDir node_modules/.cache/heartbeat-panel --skipLibCheck --noEmitOnError`
Expected: 无输出、退出码 0

- [ ] **Step 5: Commit**

```bash
git add src/components/HeartbeatPanel/index.tsx src/i18n/locales/zh.json src/i18n/locales/en.json
git commit -m "feat(heartbeat): wire create/edit drawer into HeartbeatPanel"
```

---

### Task 10: HeartbeatPanel —— toggle/delete/run_now/cancel + 运行中轮询

**Files:**
- Modify: `src/components/HeartbeatPanel/index.tsx`
- Modify: `src/i18n/locales/zh.json`, `src/i18n/locales/en.json`

**Interfaces:**
- Consumes: `heartbeatRunNowMessageKey`/`heartbeatCancelMessageKey`（Task 4）、`ConfirmDialog`（已存在，`../CronPanel/ConfirmDialog`）、`HeartbeatRunNowResult`/`HeartbeatCancelResult`（Task 1）
- Produces: 每个 job 行的启停/删除/立即运行/取消按钮；`toast` 提示状态；运行中任务的 2~5 秒轮询刷新，`status!=='running'` 后自动停止。

- [ ] **Step 1: 加 import 和轮询/toast 状态**

在 `index.tsx` import 块追加：

```tsx
import { heartbeatRunNowMessageKey, heartbeatCancelMessageKey } from './heartbeatStatusText';
import ConfirmDialog from '../CronPanel/ConfirmDialog';
```

在组件内 `drawer` state 之后追加：

```tsx
  const [toast, setToast] = useState<string | null>(null);
  const [pendingDelete, setPendingDelete] = useState<HeartbeatTaskUI | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [actingJobId, setActingJobId] = useState<string | null>(null);

  useEffect(() => {
    if (!toast) return;
    const timer = window.setTimeout(() => setToast(null), 4000);
    return () => window.clearTimeout(timer);
  }, [toast]);

  // 有任务处于 running 时，每 3 秒静默刷新一次列表；全部离开 running 后自动停止，
  // 页面隐藏/组件卸载时也停止，见接口规格说明 §7 建议刷新策略
  useEffect(() => {
    const hasRunning = jobs.some((job) => job.status === 'running');
    if (!hasRunning) return;
    const controller = new AbortController();
    const timer = window.setInterval(() => {
      if (document.hidden) return;
      void loadAll(controller.signal);
    }, 3000);
    return () => {
      window.clearInterval(timer);
      controller.abort();
    };
  }, [jobs, loadAll]);
```

- [ ] **Step 2: 加 toggle/delete/run_now/cancel 处理函数**

紧接着上面的代码追加：

```tsx
  const handleToggle = useCallback(
    async (job: HeartbeatTaskUI) => {
      setActingJobId(job.id);
      try {
        await webRequest<{ job: HeartbeatJobDTO }>('heartbeat.job.toggle', {
          session_id: sessionId,
          id: job.id,
          enabled: !job.enabled,
        });
        setToast(t(job.enabled ? 'heartbeat.toast.paused' : 'heartbeat.toast.resumed'));
        const controller = new AbortController();
        await loadAll(controller.signal);
      } catch (err) {
        setToast(err instanceof Error ? err.message : String(err));
      } finally {
        setActingJobId(null);
      }
    },
    [sessionId, loadAll, t],
  );

  const handleRunNow = useCallback(
    async (job: HeartbeatTaskUI) => {
      setActingJobId(job.id);
      try {
        const result = await webRequest<HeartbeatRunNowResult>('heartbeat.job.run_now', {
          session_id: sessionId,
          id: job.id,
          reschedule: false,
        });
        setToast(t(heartbeatRunNowMessageKey(result.accepted, result.reason, result.queued)));
        const controller = new AbortController();
        await loadAll(controller.signal);
      } catch (err) {
        setToast(err instanceof Error ? err.message : String(err));
      } finally {
        setActingJobId(null);
      }
    },
    [sessionId, loadAll, t],
  );

  const handleCancel = useCallback(
    async (job: HeartbeatTaskUI, pauseSchedule: boolean) => {
      setActingJobId(job.id);
      try {
        const result = await webRequest<HeartbeatCancelResult>('heartbeat.job.cancel', {
          session_id: sessionId,
          id: job.id,
          pause_schedule: pauseSchedule,
        });
        setToast(t(heartbeatCancelMessageKey(result.cancel_status)));
        const controller = new AbortController();
        await loadAll(controller.signal);
      } catch (err) {
        setToast(err instanceof Error ? err.message : String(err));
      } finally {
        setActingJobId(null);
      }
    },
    [sessionId, loadAll, t],
  );

  const confirmDelete = useCallback(async () => {
    if (!pendingDelete) return;
    setDeleting(true);
    setDeleteError(null);
    try {
      const result = await webRequest<{ deleted: boolean }>('heartbeat.job.delete', {
        session_id: sessionId,
        id: pendingDelete.id,
      });
      if (!result.deleted) {
        setDeleteError(t('heartbeat.toast.deleteConflict') ?? undefined);
        return;
      }
      setPendingDelete(null);
      const controller = new AbortController();
      await loadAll(controller.signal);
    } catch (err) {
      const webErr = err as WebError;
      if (webErr.code === 'CONFLICT') {
        setDeleteError(t('heartbeat.toast.deleteConflict'));
      } else {
        setDeleteError(webErr.message ?? String(err));
      }
    } finally {
      setDeleting(false);
    }
  }, [pendingDelete, sessionId, loadAll, t]);
```

- [ ] **Step 3: 在 job 卡片里加操作按钮，并在组件末尾渲染 toast + 删除确认弹窗**

把 Task 9 里已加的"编辑"按钮所在 `<div className="mt-2 flex justify-end">` 替换为：

```tsx
                  <div className="mt-2 flex flex-wrap justify-end gap-2">
                    {job.status === 'running' && (
                      <button
                        type="button"
                        disabled={actingJobId === job.id}
                        onClick={() => void handleCancel(job, false)}
                        className="rounded-full border border-border px-3 py-1 text-xs text-text hover:bg-bg-hover disabled:opacity-60"
                      >
                        {t('heartbeat.panel.cancelRun')}
                      </button>
                    )}
                    <button
                      type="button"
                      disabled={actingJobId === job.id}
                      onClick={() => void handleRunNow(job)}
                      className="rounded-full border border-border px-3 py-1 text-xs text-text hover:bg-bg-hover disabled:opacity-60"
                    >
                      {t('heartbeat.panel.runNow')}
                    </button>
                    <button
                      type="button"
                      disabled={actingJobId === job.id || job.status === 'completed' || job.status === 'expired'}
                      onClick={() => void handleToggle(job)}
                      className="rounded-full border border-border px-3 py-1 text-xs text-text hover:bg-bg-hover disabled:cursor-not-allowed disabled:opacity-40"
                    >
                      {t(job.enabled ? 'heartbeat.panel.pause' : 'heartbeat.panel.resume')}
                    </button>
                    <button
                      type="button"
                      onClick={() => openEditDrawer(job)}
                      className="rounded-full border border-border px-3 py-1 text-xs text-text hover:bg-bg-hover"
                    >
                      {t('heartbeat.panel.edit')}
                    </button>
                    <button
                      type="button"
                      onClick={() => {
                        setDeleteError(null);
                        setPendingDelete(job);
                      }}
                      className="rounded-full border border-red-300 px-3 py-1 text-xs text-red-500 hover:bg-red-50"
                    >
                      {t('heartbeat.panel.delete')}
                    </button>
                  </div>
```

在组件最外层 `return (...)` 的最后一个 `</div>`（抽屉最外层容器的关闭标签）之后、函数体 `return` 语句仍在同一个顶层 fragment 内追加 toast 与删除确认弹窗。因为当前结构是单个根 `<div className="fixed inset-0 ...">`，把 toast/弹窗放进这个根 `<div>` 内部、抽屉内容 `<div className="flex h-full w-[520px] ...">` 之后：

```tsx
        {toast && (
          <div className="pointer-events-none fixed bottom-6 right-6 z-50 rounded-md bg-text-strong px-4 py-2 text-sm text-card shadow-lg">
            {toast}
          </div>
        )}
        {pendingDelete && (
          <ConfirmDialog
            title={t('heartbeat.panel.delete')}
            message={deleteError ?? t('heartbeat.panel.deleteConfirm', { name: pendingDelete.name })}
            loading={deleting}
            onConfirm={() => void confirmDelete()}
            onCancel={() => setPendingDelete(null)}
          />
        )}
```

（`toast`/`ConfirmDialog` 挂在最外层 `fixed inset-0` 容器内、抽屉主体 `onClick={(e) => e.stopPropagation()}` 之外，这样点击遮罩关闭面板的行为不受影响，但 `ConfirmDialog` 自己也是 `fixed inset-0` 浮层，会正确盖在最上层。）

- [ ] **Step 4: 加 i18n key**

zh.json 的 `heartbeat.panel` 对象追加：

```json
"runNow": "立即运行",
"cancelRun": "停止本次运行",
"pause": "暂停",
"resume": "恢复",
"delete": "删除",
"deleteConfirm": "确定删除心跳任务「{{name}}」吗？"
```

zh.json 新增 `heartbeat.toast` 对象（与 `panel`/`drawer`/`schedule` 同级）：

```json
"toast": {
  "paused": "已暂停后续计划",
  "resumed": "已恢复后续计划",
  "deleteConflict": "当前有运行未能取消，请稍后重试",
  "runNowAccepted": "已接收，即将执行",
  "runNowQueued": "已加入待执行队列",
  "runNowRejected": {
    "session_missing": "原会话不存在，未执行",
    "session_busy": "当前会话忙碌，未执行",
    "previous_run_active": "上一轮尚未结束，已跳过",
    "already_queued": "已有一个待执行触发，未重复排队",
    "replacement_pending": "正在替换上一轮运行，请稍候",
    "replacement_cancel_failed": "无法取消上一轮运行，未执行",
    "job_disabled_during_replace": "替换期间任务已被停用，未执行",
    "unknown": "未执行，原因未知"
  },
  "cancel": {
    "idle": "当前没有运行中的执行",
    "cancelled": "已取消当前执行",
    "not_found": "当前执行已结束或不存在",
    "failed": "取消失败，状态待确认"
  }
}
```

en.json 对应（`panel` 追加）：

```json
"runNow": "Run now",
"cancelRun": "Cancel this run",
"pause": "Pause",
"resume": "Resume",
"delete": "Delete",
"deleteConfirm": "Delete heartbeat job \"{{name}}\"?"
```

en.json 新增 `heartbeat.toast`：

```json
"toast": {
  "paused": "Future schedule paused",
  "resumed": "Future schedule resumed",
  "deleteConflict": "Could not cancel the active run, try again later",
  "runNowAccepted": "Accepted, starting shortly",
  "runNowQueued": "Queued for execution",
  "runNowRejected": {
    "session_missing": "Original session missing, not executed",
    "session_busy": "Session busy, not executed",
    "previous_run_active": "Previous run still active, skipped",
    "already_queued": "A run is already queued, not duplicated",
    "replacement_pending": "Replacing the previous run, please wait",
    "replacement_cancel_failed": "Could not cancel the previous run, not executed",
    "job_disabled_during_replace": "Job was disabled during replacement, not executed",
    "unknown": "Not executed, reason unknown"
  },
  "cancel": {
    "idle": "No run currently active",
    "cancelled": "Current run cancelled",
    "not_found": "Current run already finished or missing",
    "failed": "Cancel failed, status unconfirmed"
  }
}
```

- [ ] **Step 5: 类型检查**

Run: `npx tsc src/components/HeartbeatPanel/index.tsx --target ES2020 --module ESNext --moduleResolution Bundler --jsx react-jsx --rootDir src --outDir node_modules/.cache/heartbeat-panel --skipLibCheck --noEmitOnError`
Expected: 无输出、退出码 0

- [ ] **Step 6: Commit**

```bash
git add src/components/HeartbeatPanel/index.tsx src/i18n/locales/zh.json src/i18n/locales/en.json
git commit -m "feat(heartbeat): wire toggle/delete/run_now/cancel and running-state polling"
```

---

### Task 11: 挂载到 ChatPanel + 全量 build 验证

**Files:**
- Modify: `src/components/ChatPanel/index.tsx`
- Modify: `src/i18n/locales/zh.json`, `src/i18n/locales/en.json`

**Interfaces:**
- Consumes: `HeartbeatPanel`（Task 8-10）、`NEW_CONVERSATION_ID`（已存在，`../../multi-session/state/newConversationLifecycle`）

- [ ] **Step 1: 加 import**

在 `src/components/ChatPanel/index.tsx` 第 9 行的 lucide-react import 里追加 `Activity`：

```tsx
import { Activity, ArrowRight, CheckCircle2, ClipboardList, Copy, Info, LoaderCircle, Share2, Sparkles, X } from 'lucide-react';
```

在第 20 行 `lineUpIcon` import 之后追加：

```tsx
import HeartbeatPanel from '../HeartbeatPanel';
import { NEW_CONVERSATION_ID } from '../../multi-session/state/newConversationLifecycle';
```

- [ ] **Step 2: 加状态和可用性判断**

在第 780 行 `const [humanShareOpen, setHumanShareOpen] = React.useState(false);` 之后追加：

```tsx
  const [heartbeatPanelOpen, setHeartbeatPanelOpen] = React.useState(false);
  // 新会话占位符 'new' 还没有真实 session_id，隐藏心跳入口，见接口规格说明 §16.2
  const heartbeatAvailable = Boolean(activeSessionId && activeSessionId !== NEW_CONVERSATION_ID);
```

- [ ] **Step 3: 加 header 按钮**

在 `chat-panel-header__actions` 内、`teamAreaExpanded` 相关的两个按钮之前（约第 1085 行之前）插入：

```tsx
            {heartbeatAvailable && (
              <button
                type="button"
                className={`chat-header-icon-btn ${heartbeatPanelOpen ? 'chat-header-icon-btn--active' : ''}`}
                onClick={() => setHeartbeatPanelOpen((v) => !v)}
                title={t('heartbeat.panel.title')}
              >
                <Activity size={16} strokeWidth={2} />
              </button>
            )}
```

- [ ] **Step 4: 渲染面板**

在第 1107 行 `{humanShareOpen && (...)}` 渲染块之后追加：

```tsx
      {heartbeatPanelOpen && heartbeatAvailable && (
        <HeartbeatPanel sessionId={activeSessionId as string} onClose={() => setHeartbeatPanelOpen(false)} />
      )}
```

- [ ] **Step 5: i18n 无需新增**

`heartbeat.panel.title` 已在 Task 8 加入，这里直接复用，不用再加新 key。

- [ ] **Step 6: 全量 build**

Run: `npm run build`
Expected: `tsc && vite build` 全部成功，退出码 0，无 TypeScript 报错（这一步会把此前每个任务的局部 `tsc --noEmit` 检查串联起来做一次真正的整体验证，是本仓库约定的强制自测门槛）。

- [ ] **Step 7: Commit**

```bash
git add src/components/ChatPanel/index.tsx
git commit -m "feat(heartbeat): mount HeartbeatPanel from chat header"
```

- [ ] **Step 8: 手动浏览器验证**

用 `npm run dev -- --port 5177`（复用主仓库 `node_modules` 的 junction，见项目 `jiuwenswarm-optimize` skill 的"前端启动约定"）启动前端，后端由用户自行运行 `feature-HEARTBEAT` 分支或已合并该功能的后端。验收路径：

1. 打开一个真实会话（非新建占位），聊天 header 出现心跳图标按钮；新建会话未发消息前，按钮不出现。
2. 点击按钮打开右侧抽屉，空状态显示"当前会话还没有心跳任务"。
3. 点"新建心跳任务"，分别测试 interval/cron/once 三种调度类型创建成功、列表刷新出现新任务。
4. 编辑任务、暂停/恢复、立即运行、停止运行、删除，逐一确认 toast 文案和 §16.7 的"禁止误报"规则（比如 run_now 不接受时不显示"执行成功"）。
5. 让一个 interval 任务触发一次，确认自动续跑消息出现在**当前这个会话**里，而不是新开一个会话或出现在 Cron 面板列表。

---

## Self-Review

**Spec 覆盖检查：**
- `heartbeat.job.meta/list/get*/create/update/delete/toggle/run_now/cancel` 九个方法在 Task 8-10 均已接线；`heartbeat.job.get`（单任务详情）当前列表页不需要单独调用，编辑走 `jobToHeartbeatTaskForm(job)` 直接用列表里已有的完整 job 对象，不重复请求，符合 YAGNI。
- `heartbeat.job.preview` 未接线：接口规格说明 §16.11 明确"创建表单无法用该接口预览尚未保存的 schedule"、"不要为了预览先创建再删除临时任务"，且列表页每行已有 `summarizeHeartbeatSchedule` 文本摘要满足"下次何时触发"的核心诉求，因此本轮不做独立的"预览未来 N 次触发时间"入口，留空是有意为之，不是遗漏。
- 时间戳秒→毫秒：`epochSecondsToOnceLocal`/toast 均未直接拼接毫秒误差，`Date` 构造统一在 Task 2 一处处理。
- 会话隔离（§16.2/16.3）：Task 11 的 `heartbeatAvailable` 守卫 + Task 8 的 `sessionIdRef`/`AbortController` 覆盖。
- 禁止误报文案（§16.7）：Task 4 的纯函数 + Task 10 消费，覆盖 run_now/cancel/toggle/delete 四类操作。
- 不暴露 `mode/model/approval/sandbox/worktree` 等字段：`HeartbeatTaskFormValue`/`HeartbeatTaskDrawer` 全文没有这些字段，天然满足。
- 未覆盖的已知限制（有意跳过，均在接口规格说明"当前联调已知限制"章节里被标注为限制而非待实现功能）：本地分页/搜索筛选（§9-11 任务数受 `max_active_jobs_per_session<=5` 限制，量级不需要）、运行历史查询（§9-8，接口本就不存在）、断线重连后的强制重同步（§16.4，超出本轮"新增面板"范围，跟随 `webClient` 未来的全局重连事件统一处理更合适，此处先不单独实现）。

**占位符扫描：** 全部 11 个任务的每个 Step 都给了可直接运行的真实代码/命令，没有"TBD"/"看情况实现"这类描述。

**类型一致性检查：**
- `HeartbeatTaskUI`（Task 1）字段名在 `heartbeatJobToUI`（Task 8）、`jobToHeartbeatTaskForm`（Task 7）、`summarizeHeartbeatSchedule` 调用点（Task 8）三处保持一致（`concurrencyPolicy`/`sessionDeletedPolicy`/`maxRuns` 驼峰命名统一）。
- `HeartbeatScheduleFormValue.timezone` 在 Task 2 定义、Task 6（编辑器读写）、Task 7（`emptyHeartbeatTaskForm`/`jobToHeartbeatTaskForm`/提交 payload）、Task 9（`scheduleFormToDto` 提交）四处用法一致，没有出现 `schedule.timezone` 和顶层 `timezone` 两个不同变量名混用的情况。
- `webRequest` 的调用签名（`method, params, options`）在 Task 8（`meta`/`list` 带 `signal`）、Task 9（`create`/`update`）、Task 10（`toggle`/`run_now`/`cancel`/`delete`）保持一致，均显式传 `session_id`。

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-08-05-heartbeat-panel.md`（当前 worktree：`feat_heartbeat`，基于 `upstream/develop`）。两种执行方式可选：

**1. Subagent-Driven（推荐）** —— 每个任务派一个全新子代理执行，任务间人工 review，迭代快
**2. Inline Execution** —— 在当前会话里按批次直接执行，带检查点

你想用哪种方式？
