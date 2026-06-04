import { addError, addInfo, parseArgs } from "../helpers.js";
import { CommandKind, type SlashCommand, type CommandContext } from "../types.js";

interface CronJobPayload {
  id: string;
  name: string;
  enabled: boolean;
  expired: boolean;
  cron_expr: string;
  timezone: string;
  wake_offset_seconds: number;
  description: string;
  targets: string;
  mode: string;
  delete_after_run: boolean;
  created_at: number | null;
  updated_at: number | null;
}

interface CronJobListPayload {
  jobs: CronJobPayload[];
}

const TARGET_CHANNELS = ["tui", "web", "feishu", "whatsapp", "wecom", "xiaoyi", "wechat", "dingtalk"];
const MODES = ["agent", "plan"];

// ── cron expression syntax validation ──

interface CronFieldRange {
  min: number;
  max: number;
  allowQuestion: boolean;
}

const CRON_5FIELD_RANGES: CronFieldRange[] = [
  { min: 0, max: 59, allowQuestion: false }, // minute
  { min: 0, max: 23, allowQuestion: false }, // hour
  { min: 1, max: 31, allowQuestion: true },  // day
  { min: 1, max: 12, allowQuestion: false }, // month
  { min: 0, max: 7, allowQuestion: true },   // dow (0=Sun,7=Sun)
];

const CRON_7FIELD_RANGES: CronFieldRange[] = [
  { min: 0, max: 59, allowQuestion: false }, // second
  { min: 0, max: 59, allowQuestion: false }, // minute
  { min: 0, max: 23, allowQuestion: false }, // hour
  { min: 1, max: 31, allowQuestion: true },  // day
  { min: 1, max: 12, allowQuestion: false }, // month
  { min: 1, max: 7, allowQuestion: true },   // dow
  // year handled separately
];

const FIELD_NAMES_5 = ["minute(0-59)", "hour(0-23)", "day(1-31)", "month(1-12)", "dow(0-7)"];
const FIELD_NAMES_7 = ["second(0-59)", "minute(0-59)", "hour(0-23)", "day(1-31)", "month(1-12)", "dow(1-7)", "year"];

function isValidCronValue(value: string, range: CronFieldRange): boolean {
  if (value === "*") return true;
  if (range.allowQuestion && value === "?") return true;

  const parts = value.split(",");
  for (const part of parts) {
    if (part.includes("/")) {
      const [rangePart, stepStr] = part.split("/");
      const step = parseInt(stepStr, 10);
      if (isNaN(step) || step <= 0) return false;
      if (rangePart === "*") continue;
      if (!isValidCronRange(rangePart, range.min, range.max)) return false;
    } else if (part.includes("-")) {
      if (!isValidCronRange(part, range.min, range.max)) return false;
    } else {
      const num = parseInt(part, 10);
      if (isNaN(num)) return false;
      if (range.min === 0 && range.max === 7 && (num < 0 || num > 7)) return false;
      if (range.min !== 0 || range.max !== 7) {
        if (num < range.min || num > range.max) return false;
      }
    }
  }
  return true;
}

function isValidCronRange(range: string, min: number, max: number): boolean {
  const [startStr, endStr] = range.split("-");
  if (!startStr || !endStr) return false;
  const start = parseInt(startStr, 10);
  const end = parseInt(endStr, 10);
  if (isNaN(start) || isNaN(end)) return false;
  if (min === 0 && max === 7) return start >= 0 && end <= 7 && start <= end;
  return start >= min && end <= max && start <= end;
}

function validateCronExpr(expr: string): string | null {
  const trimmed = expr.trim();
  if (!trimmed) return "cron_expr 不能为空";

  const fields = trimmed.split(/\s+/);
  const fieldCount = fields.length;

  if (fieldCount !== 5 && fieldCount !== 7) {
    return `cron_expr 需要 5 字段(周期)或 7 字段(单次)，当前有 ${fieldCount} 个字段`;
  }

  const ranges = fieldCount === 5 ? CRON_5FIELD_RANGES : CRON_7FIELD_RANGES;
  const names = fieldCount === 5 ? FIELD_NAMES_5 : FIELD_NAMES_7;

  for (let i = 0; i < fields.length; i++) {
    // Year field (7-field, index 6) has special handling
    if (fieldCount === 7 && i === 6) {
      if (fields[i] === "*") continue;
      const yearNum = parseInt(fields[i], 10);
      if (isNaN(yearNum) || yearNum < 1970 || yearNum > 2099) {
        return `${names[i]} 字段无效: year 需为 1970-2099 或 *`;
      }
      continue;
    }

    if (!isValidCronValue(fields[i], ranges[i])) {
      return `${names[i]} 字段无效: "${fields[i]}"`;
    }
  }

  return null; // valid
}

// ── end cron validation ──

export function createCronCommand(): SlashCommand {
  return {
    name: "cron",
    altNames: ["crontab"],
    description: "管理定时任务（cron jobs）——到点让 Agent 帮你做事",
    usage: "/cron [list|show|add|update|delete|toggle|run|preview]",
    example:
      '/cron list\n' +
      '/cron show <id>\n' +
      '/cron add name=晨报 cron_expr="0 9 * * *" description="生成简短的中文健康打卡提醒" targets=tui\n' +
      '/cron update <id> description="新的任务内容"\n' +
      "/cron delete <id>\n" +
      "/cron toggle <id> on|off\n" +
      "/cron run <id>\n" +
      "/cron preview <id>",
    kind: CommandKind.BUILT_IN,
    takesArgs: true,
    subCommands: [
      {
        name: "list",
        description: "列出所有定时任务",
        usage: "/cron list",
        kind: CommandKind.BUILT_IN,
        takesArgs: false,
        isSafeConcurrent: true,
        action: async (ctx) => _handleList(ctx),
      },
      {
        name: "show",
        description: "查看定时任务详情",
        usage: "/cron show <job_id>",
        argGuide: "<job_id>",
        kind: CommandKind.BUILT_IN,
        takesArgs: true,
        action: async (ctx, args) => _handleShow(ctx, parseArgs(`show ${args}`)),
      },
      {
        name: "add",
        description: "创建定时任务",
        usage: "/cron add name=... cron_expr=\"...\" description=\"...\"",
        argGuide: "name=任务名 cron_expr=\"时间表达式(5字段或7字段)\" description=\"让Agent做什么\" targets=tui",
        kind: CommandKind.BUILT_IN,
        takesArgs: true,
        action: async (ctx, args) => _handleAdd(ctx, `add ${args}`),
      },
      {
        name: "update",
        description: "更新定时任务",
        usage: "/cron update <id> key=value ...",
        argGuide: "<id> description=\"新内容\" cron_expr=\"新时间\" ...",
        kind: CommandKind.BUILT_IN,
        takesArgs: true,
        action: async (ctx, args) => {
          const parts = parseArgs(`update ${args}`);
          await _handleUpdate(ctx, `update ${args}`, parts);
        },
      },
      {
        name: "delete",
        description: "删除定时任务",
        usage: "/cron delete <id>",
        argGuide: "<job_id>",
        kind: CommandKind.BUILT_IN,
        takesArgs: true,
        action: async (ctx, args) => _handleDelete(ctx, parseArgs(`delete ${args}`)),
      },
      {
        name: "toggle",
        description: "开关定时任务",
        usage: "/cron toggle <id> on|off",
        argGuide: "<job_id> on|off",
        kind: CommandKind.BUILT_IN,
        takesArgs: true,
        action: async (ctx, args) => _handleToggle(ctx, parseArgs(`toggle ${args}`)),
      },
      {
        name: "run",
        description: "立即执行定时任务",
        usage: "/cron run <id>",
        argGuide: "<job_id>",
        kind: CommandKind.BUILT_IN,
        takesArgs: true,
        action: async (ctx, args) => _handleRun(ctx, parseArgs(`run ${args}`)),
      },
      {
        name: "preview",
        description: "预览定时任务下次执行时间",
        usage: "/cron preview <id>",
        argGuide: "<job_id> [次数]",
        kind: CommandKind.BUILT_IN,
        takesArgs: true,
        action: async (ctx, args) => _handlePreview(ctx, parseArgs(`preview ${args}`)),
      },
    ],
    action: async (ctx: CommandContext, args: string) => {
      const raw = args.trim();
      const parts = parseArgs(raw);

      if (parts.length === 0 || parts[0] === "list") {
        await _handleList(ctx);
        return;
      }

      const sub = parts[0];

      switch (sub) {
        case "show":
          await _handleShow(ctx, parts);
          break;
        case "add":
          await _handleAdd(ctx, raw);
          break;
        case "update":
          await _handleUpdate(ctx, raw, parts);
          break;
        case "delete":
          await _handleDelete(ctx, parts);
          break;
        case "toggle":
          await _handleToggle(ctx, parts);
          break;
        case "run":
          await _handleRun(ctx, parts);
          break;
        case "preview":
          await _handlePreview(ctx, parts);
          break;
        default:
          ctx.addItem(
            addError(
              ctx.sessionId,
              `Unknown sub-command: "${sub}". Use: list, show, add, update, delete, toggle, run, preview`,
            ),
          );
      }
    },
  };
}

async function _handleList(ctx: CommandContext): Promise<void> {
  try {
    const payload = await ctx.request("cron.job.list", {}) as CronJobListPayload;
    const jobs = payload.jobs ?? [];

    if (jobs.length === 0) {
      ctx.addItem(addInfo(ctx.sessionId, "No cron jobs configured", "clock"));
      return;
    }

    const items = jobs.map((j: CronJobPayload, i: number) => {
      const statusIcon = j.enabled ? "ON" : "OFF";
      const expiredTag = j.expired ? " [expired]" : "";
      const descSnippet = j.description ? (j.description.length > 30 ? j.description.slice(0, 30) + "..." : j.description) : "-";
      return {
        label: String(i + 1),
        value: `${j.id} | ${j.name} | ${j.cron_expr} | ${statusIcon}${expiredTag} | ${descSnippet}`,
      };
    });

    ctx.addItem(
      addInfo(ctx.sessionId, `Cron Jobs (${jobs.length} total)`, "clock", {
        view: "list",
        title: "Cron Jobs",
        items,
      }),
    );
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    ctx.addItem(addError(ctx.sessionId, `Failed to list cron jobs: ${message}`));
  }
}

async function _handleShow(ctx: CommandContext, parts: string[]): Promise<void> {
  const jobId = parts[1];
  if (!jobId) {
    ctx.addItem(addError(ctx.sessionId, "Usage: /cron show <job_id>"));
    return;
  }

  try {
    const payload = await ctx.request("cron.job.get", { id: jobId }) as { job: CronJobPayload } | null;
    if (!payload || !payload.job) {
      ctx.addItem(addError(ctx.sessionId, `Job not found: ${jobId}`));
      return;
    }

    const job = payload.job;
    const statusTag = job.expired ? "EXPIRED" : job.enabled ? "ON" : "OFF";
    const isOneShot = job.delete_after_run === true;
    const taskType = isOneShot ? "单次任务" : "周期任务";

    ctx.addItem(
      addInfo(ctx.sessionId, `Cron Job: ${job.name}`, "clock", {
        view: "kv",
        title: `Cron Job Detail [${taskType}]`,
        items: [
          { label: "id", value: job.id },
          { label: "name", value: job.name },
          { label: "status", value: statusTag },
          { label: "cron_expr", value: job.cron_expr },
          { label: "timezone", value: job.timezone },
          { label: "description", value: job.description || "-" },
          { label: "targets", value: job.targets },
          { label: "mode", value: job.mode || "agent" },
          { label: "wake_offset_seconds", value: String(job.wake_offset_seconds ?? 300) },
          { label: "delete_after_run", value: String(isOneShot) },
        ],
      }),
    );
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    ctx.addItem(addError(ctx.sessionId, `Failed to get cron job: ${message}`));
  }
}

async function _handleAdd(ctx: CommandContext, raw: string): Promise<void> {
  const addPart = raw.replace(/^add\s+/, "");
  const kvPairs: Record<string, string> = {};

  // Handle quoted values like description="..." and cron_expr="0 9 * * *"
  // Also capture empty quoted values like description="" (key="" means empty string)
  const quotedRegex = /(\w+)="([^"]*)"/g;
  let m: RegExpExecArray | null;
  while ((m = quotedRegex.exec(addPart)) !== null) {
    kvPairs[m[1]] = m[2];
  }
  // Handle unquoted key=value pairs (skip ones already captured by quoted regex)
  const unquotedRegex = /(\w+)=(\S+)/g;
  while ((m = unquotedRegex.exec(addPart)) !== null) {
    if (!(m[1] in kvPairs)) {
      kvPairs[m[1]] = m[2];
    }
  }

  const requiredFields = ["name", "cron_expr", "description"];
  const missing = requiredFields.filter((f) => !kvPairs[f] || kvPairs[f].trim() === "");
  if (missing.length > 0) {
    ctx.addItem(
      addError(
        ctx.sessionId,
        `缺少必填字段: ${missing.join(", ")}。必填: name(任务名)、cron_expr(时间)、description(让Agent做什么)。示例: /cron add name=晨报 cron_expr="0 9 * * *" description="生成健康打卡提醒" targets=tui`,
      ),
    );
    return;
  }

  if (!kvPairs.targets) kvPairs.targets = "tui";
  if (!kvPairs.timezone) kvPairs.timezone = "Asia/Shanghai";
  if (!kvPairs.mode) kvPairs.mode = "agent";

  // cron_expr syntax validation
  const cronError = validateCronExpr(kvPairs.cron_expr);
  if (cronError) {
    ctx.addItem(addError(ctx.sessionId, `cron_expr 语法错误: ${cronError}`));
    return;
  }

  // targets validation
  if (
    !TARGET_CHANNELS.includes(kvPairs.targets.toLowerCase()) &&
    !kvPairs.targets.startsWith("feishu_enterprise:")
  ) {
    ctx.addItem(
      addError(
        ctx.sessionId,
        `Invalid target channel: "${kvPairs.targets}". Valid: ${TARGET_CHANNELS.join(", ")}, feishu_enterprise:<app_id>`,
      ),
    );
    return;
  }

  if (!MODES.includes(kvPairs.mode.toLowerCase())) {
    ctx.addItem(
      addError(ctx.sessionId, `Invalid mode: "${kvPairs.mode}". Valid: ${MODES.join(", ")}`),
    );
    return;
  }

  if (kvPairs.delete_after_run && kvPairs.delete_after_run !== "true" && kvPairs.delete_after_run !== "false") {
    ctx.addItem(
      addError(ctx.sessionId, `Invalid delete_after_run: "${kvPairs.delete_after_run}". Valid: "true" or "false"`),
    );
    return;
  }

  // wake_offset_seconds validation
  if (kvPairs.wake_offset_seconds) {
    const wos = parseInt(kvPairs.wake_offset_seconds, 10);
    if (isNaN(wos) || wos < 0) {
      ctx.addItem(addError(ctx.sessionId, `Invalid wake_offset_seconds: "${kvPairs.wake_offset_seconds}". Must be a non-negative integer`));
      return;
    }
  }

  try {
    const payload = await ctx.request("cron.job.create", {
      name: kvPairs.name,
      cron_expr: kvPairs.cron_expr,
      description: kvPairs.description,
      targets: kvPairs.targets,
      timezone: kvPairs.timezone,
      mode: kvPairs.mode,
      wake_offset_seconds: parseInt(kvPairs.wake_offset_seconds || "300", 10),
      delete_after_run: kvPairs.delete_after_run === "true",
    }) as { job: CronJobPayload };

    const job = payload.job;
    ctx.addItem(
      addInfo(ctx.sessionId, `Created cron job: ${job.name}`, "clock", {
        view: "kv",
        title: "Cron Job Created",
        items: [
          { label: "id", value: job.id },
          { label: "name", value: job.name },
          { label: "cron_expr", value: job.cron_expr },
          { label: "description", value: job.description },
          { label: "timezone", value: job.timezone },
          { label: "targets", value: job.targets },
          { label: "mode", value: job.mode },
          { label: "enabled", value: String(job.enabled) },
          { label: "delete_after_run", value: String(job.delete_after_run) },
        ],
      }),
    );
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    ctx.addItem(addError(ctx.sessionId, `Failed to create cron job: ${message}`));
  }
}

async function _handleDelete(ctx: CommandContext, parts: string[]): Promise<void> {
  const jobId = parts[1];
  if (!jobId) {
    ctx.addItem(addError(ctx.sessionId, "Usage: /cron delete <job_id>"));
    return;
  }

  try {
    const payload = await ctx.request("cron.job.delete", { id: jobId }) as { deleted: boolean };
    if (payload.deleted) {
      ctx.addItem(addInfo(ctx.sessionId, `Deleted cron job: ${jobId}`, "clock"));
    } else {
      ctx.addItem(addError(ctx.sessionId, `Job not found: ${jobId}`));
    }
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    ctx.addItem(addError(ctx.sessionId, `Failed to delete cron job: ${message}`));
  }
}

async function _handleUpdate(ctx: CommandContext, raw: string, parts: string[]): Promise<void> {
  const jobId = parts[1];
  if (!jobId) {
    ctx.addItem(addError(ctx.sessionId, "Usage: /cron update <job_id> key=value ...  (只改你想改的字段)"));
    return;
  }

  const updatePart = raw.replace(/^update\s+\S+\s+/, "");
  if (!updatePart) {
    ctx.addItem(addError(ctx.sessionId, "Usage: /cron update <job_id> key=value ...  例如: /cron update <id> description=\"新内容\" cron_expr=\"0 0 8 * * ? *\""));
    return;
  }

  const patch: Record<string, unknown> = {};

  // Handle quoted values like description="..." (also captures empty key="")
  const quotedRegex = /(\w+)="([^"]*)"/g;
  let m: RegExpExecArray | null;
  while ((m = quotedRegex.exec(updatePart)) !== null) {
    patch[m[1]] = m[2];
  }
  // Handle unquoted key=value pairs
  const unquotedRegex = /(\w+)=(\S+)/g;
  while ((m = unquotedRegex.exec(updatePart)) !== null) {
    if (!(m[1] in patch)) {
      patch[m[1]] = m[2];
    }
  }

  if (Object.keys(patch).length === 0) {
    ctx.addItem(addError(ctx.sessionId, "没有指定要更新的字段。用法: /cron update <id> key=value ..."));
    return;
  }

  // Validate each field in the patch
  if ("name" in patch && !String(patch.name).trim()) {
    ctx.addItem(addError(ctx.sessionId, "name 不能为空"));
    return;
  }
  if ("cron_expr" in patch) {
    const cronError = validateCronExpr(String(patch.cron_expr));
    if (cronError) {
      ctx.addItem(addError(ctx.sessionId, `cron_expr 语法错误: ${cronError}`));
      return;
    }
  }
  if ("description" in patch && !String(patch.description).trim()) {
    ctx.addItem(addError(ctx.sessionId, "description 不能为空"));
    return;
  }
  if ("targets" in patch) {
    const t = String(patch.targets).trim().toLowerCase();
    if (!TARGET_CHANNELS.includes(t) && !String(patch.targets).startsWith("feishu_enterprise:")) {
      ctx.addItem(addError(ctx.sessionId, `Invalid target channel: "${patch.targets}". Valid: ${TARGET_CHANNELS.join(", ")}, feishu_enterprise:<app_id>`));
      return;
    }
  }
  if ("mode" in patch) {
    const m = String(patch.mode).trim().toLowerCase();
    if (!MODES.includes(m)) {
      ctx.addItem(addError(ctx.sessionId, `Invalid mode: "${patch.mode}". Valid: ${MODES.join(", ")}`));
      return;
    }
  }
  if ("delete_after_run" in patch) {
    const raw = String(patch.delete_after_run).toLowerCase();
    if (raw !== "true" && raw !== "false") {
      ctx.addItem(addError(ctx.sessionId, `Invalid delete_after_run: "${patch.delete_after_run}". Valid: "true" or "false"`));
      return;
    }
    patch.delete_after_run = raw === "true";
  }
  if ("wake_offset_seconds" in patch) {
    const wos = parseInt(String(patch.wake_offset_seconds), 10);
    if (isNaN(wos) || wos < 0) {
      ctx.addItem(addError(ctx.sessionId, `Invalid wake_offset_seconds: "${patch.wake_offset_seconds}". Must be a non-negative integer`));
      return;
    }
    patch.wake_offset_seconds = wos;
  }

  try {
    const payload = await ctx.request("cron.job.update", { id: jobId, patch }) as { job: CronJobPayload };
    const job = payload.job;
    const updatedFields = Object.keys(patch);
    ctx.addItem(
      addInfo(ctx.sessionId, `Updated cron job: ${job.name} (修改了: ${updatedFields.join(", ")})`, "clock", {
        view: "kv",
        title: "Cron Job Updated",
        items: updatedFields.map((k) => ({
          label: k,
          value: String(job[k as keyof CronJobPayload] ?? patch[k]),
        })),
      }),
    );
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    ctx.addItem(addError(ctx.sessionId, `Failed to update cron job: ${message}`));
  }
}

async function _handleToggle(ctx: CommandContext, parts: string[]): Promise<void> {
  const jobId = parts[1];
  const onOff = parts[2];

  if (!jobId || !onOff) {
    ctx.addItem(addError(ctx.sessionId, "Usage: /cron toggle <job_id> on|off"));
    return;
  }

  const validOnOff = ["on", "off", "true", "false"];
  if (!validOnOff.includes(onOff.toLowerCase())) {
    ctx.addItem(addError(ctx.sessionId, `Invalid toggle value: "${onOff}". Must be "on" or "off"`));
    return;
  }

  const enabled = onOff.toLowerCase() === "on" || onOff.toLowerCase() === "true";

  try {
    const payload = await ctx.request("cron.job.toggle", {
      id: jobId,
      enabled,
    }) as { job: CronJobPayload };
    const job = payload.job;
    ctx.addItem(
      addInfo(ctx.sessionId, `Toggled cron job "${job.name}" to ${job.enabled ? "ON" : "OFF"}`, "clock", {
        view: "kv",
        title: "Cron Job Toggle",
        items: [
          { label: "id", value: job.id },
          { label: "name", value: job.name },
          { label: "enabled", value: String(job.enabled) },
        ],
      }),
    );
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    ctx.addItem(addError(ctx.sessionId, `Failed to toggle cron job: ${message}`));
  }
}

async function _handleRun(ctx: CommandContext, parts: string[]): Promise<void> {
  const jobId = parts[1];
  if (!jobId) {
    ctx.addItem(addError(ctx.sessionId, "Usage: /cron run <job_id>"));
    return;
  }

  try {
    const payload = await ctx.request("cron.job.run_now", { id: jobId }) as { run_id: string };
    ctx.addItem(
      addInfo(ctx.sessionId, `Triggered cron job: ${jobId} (run_id: ${payload.run_id})`, "clock"),
    );
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    ctx.addItem(addError(ctx.sessionId, `Failed to run cron job: ${message}`));
  }
}

async function _handlePreview(ctx: CommandContext, parts: string[]): Promise<void> {
  const jobId = parts[1];
  if (!jobId) {
    ctx.addItem(addError(ctx.sessionId, "Usage: /cron preview <job_id>"));
    return;
  }

  const count = parseInt(parts[2] || "5", 10);

  try {
    // Fetch job details to determine task type (one-shot vs recurring)
    // delete_after_run=true means execute once then auto-delete = one-shot
    const jobPayload = await ctx.request("cron.job.get", { id: jobId }) as { job: CronJobPayload } | null;
    const job = jobPayload?.job;
    const isOneShot = job?.delete_after_run === true;
    const taskType = isOneShot ? "单次任务" : "周期任务";
    const typeIcon = isOneShot ? "one-shot" : "recurring";

    const previewCount = isOneShot ? 1 : count;
    const payload = await ctx.request("cron.job.preview", {
      id: jobId,
      count: previewCount,
    }) as { next: Array<{ wake_at: string; push_at: string } | string> };
    const nextRuns = payload.next ?? [];

    if (nextRuns.length === 0) {
      ctx.addItem(addInfo(ctx.sessionId, `No upcoming runs for job ${jobId} (${taskType}, may be expired or disabled)`, "clock"));
      return;
    }

    const items = nextRuns.map((item: { wake_at: string; push_at: string } | string, i: number) => {
      if (typeof item === "string") {
        return { label: String(i + 1), value: item };
      }
      return {
        label: String(i + 1),
        value: `唤醒: ${item.wake_at}  推送: ${item.push_at}`,
      };
    });

    ctx.addItem(
      addInfo(ctx.sessionId, `${taskType} · Next ${nextRuns.length} runs for job ${jobId}`, "clock", {
        view: "list",
        title: `Cron Job Preview [${typeIcon}]`,
        items,
      }),
    );
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    ctx.addItem(addError(ctx.sessionId, `Failed to preview cron job: ${message}`));
  }
}