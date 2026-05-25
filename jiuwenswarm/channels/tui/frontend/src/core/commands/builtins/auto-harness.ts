// jiuwenclaw/cli/src/core/commands/builtins/auto-harness.ts

import { addError, addInfo, parseArgs } from "../helpers.js";
import { CommandKind, type SlashCommand, type CommandContext } from "../types.js";

// Pipeline options: friendly display names → backend values
export const PIPELINE_DISPLAY_NAMES = {
  optimize_expert_harness: { backend: "extended_evolve_pipeline", display: "生成扩展包", desc: "生成本地 harness package" },
  optimize_meta_harness: { backend: "meta_evolve_pipeline", display: "提交优化代码", desc: "提交 PR（需配置 git）" },
};
export const PIPELINE_DISPLAY_KEYS = Object.keys(PIPELINE_DISPLAY_NAMES);

// Backend pipeline values (for validation after resolving)
export const PIPELINE_BACKEND_VALUES = Object.values(PIPELINE_DISPLAY_NAMES).map(v => v.backend);

// Resolve friendly display name to backend value
export function resolvePipelineName(name: string): string {
  if (name in PIPELINE_DISPLAY_NAMES) return PIPELINE_DISPLAY_NAMES[name as keyof typeof PIPELINE_DISPLAY_NAMES].backend;
  // Already a backend value (passed directly) — accept it
  if (PIPELINE_BACKEND_VALUES.includes(name)) return name;
  return name;
}

// Aliases for backward compat with completion/validation logic
export const PIPELINE_OPTIONS = PIPELINE_DISPLAY_NAMES;
export const PIPELINE_VALUES = PIPELINE_DISPLAY_KEYS;

// Get display-friendly label for a pipeline (accepts both friendly and backend names)
export function pipelineDisplayLabel(name: string): string {
  // Friendly name directly
  if (name in PIPELINE_DISPLAY_NAMES) {
    return `${name} (${PIPELINE_DISPLAY_NAMES[name as keyof typeof PIPELINE_DISPLAY_NAMES].display})`;
  }
  // Backend name — reverse lookup
  for (const [key, val] of Object.entries(PIPELINE_DISPLAY_NAMES)) {
    if (val.backend === name) return `${key} (${val.display})`;
  }
  return name;
}

// Interval options
export const INTERVAL_OPTIONS = {
  "1": { desc: "每 1 小时执行" },
  "2": { desc: "每 2 小时执行" },
  "4": { desc: "每 4 小时执行" },
  "8": { desc: "每 8 小时执行" },
  "12": { desc: "每 12 小时执行" },
  "24": { desc: "每 24 小时执行（每天）" },
};
export const INTERVAL_VALUES = Object.keys(INTERVAL_OPTIONS);

// Flag options with descriptions (used by app-screen.ts for autocomplete descriptions)
export const FLAG_OPTIONS = {
  "--interval": { desc: "执行间隔（小时）", alias: "-i" },
  "-i": { desc: "执行间隔（小时）", alias: "--interval" },
  "--pipeline": { desc: "Pipeline 类型", alias: "-p" },
  "-p": { desc: "Pipeline 类型", alias: "--pipeline" },
};

// Pipeline completion helper - returns completions with existing args preserved
// The completion value becomes the FULL argument string, so we must preserve existing args
function getPipelineCompletions(_partial: string, parts: string[]): string[] {
  const lastPart = parts[parts.length - 1] || "";

  // Helper to build completion preserving existing arguments
  // Remove the last incomplete part and add the completion
  const buildCompletion = (completion: string): string => {
    // Keep all parts except the last one (which is being completed)
    const existingParts = parts.slice(0, -1);
    return [...existingParts, completion].join(" ");
  };

  // If --pipeline is typed (last part is the flag), suggest flag + value combinations
  if (lastPart === "--pipeline") {
    return PIPELINE_VALUES.map(v => buildCompletion(`--pipeline ${v}`));
  }

  // If -p is typed (last part is the short flag), suggest flag + value combinations
  if (lastPart === "-p") {
    return PIPELINE_VALUES.map(v => buildCompletion(`-p ${v}`));
  }

  // If we're typing a value after --pipeline/-p (flag exists, now typing value)
  const pipelineIndex = parts.indexOf("--pipeline");
  const shortPipelineIndex = parts.indexOf("-p");
  if (pipelineIndex !== -1 || shortPipelineIndex !== -1) {
    // Check if we're at the value position (right after the flag)
    const flagPos = Math.max(pipelineIndex, shortPipelineIndex);
    // parts.length === flagPos + 2 means we're at the value slot (flag at flagPos, value at flagPos+1)
    if (parts.length === flagPos + 2 && !lastPart.startsWith("-")) {
      // Return completions with flag preserved: "--pipeline <value>"
      const flag = pipelineIndex !== -1 ? "--pipeline" : "-p";
      return PIPELINE_VALUES
        .filter(v => v.startsWith(lastPart.toLowerCase()))
        .map(v => buildCompletion(`${flag} ${v}`));
    }
  }

  // If typing a flag prefix (e.g., "--p"), suggest only flag + value combinations
  if (lastPart.startsWith("-")) {
    const hasPipeline = parts.includes("--pipeline") || parts.includes("-p");
    if (!hasPipeline) {
      const completions: string[] = [];
      // Add matching flag + value combinations (skip bare flag)
      const matchingFlags = ["--pipeline", "-p"].filter(f => f.startsWith(lastPart));
      for (const f of matchingFlags) {
        // Only add flag + value combinations, not bare flag
        for (const v of PIPELINE_VALUES) {
          completions.push(buildCompletion(`${f} ${v}`));
        }
      }
      return completions;
    }
  }

  return [];
}

// Helper functions

function parseScheduleStartArgs(args: string): { interval: number; pipeline: string; query: string } {
  const parts = parseArgs(args);

  let interval = 0;
  let pipeline = "";
  let queryParts: string[] = [];
  let i = 0;

  while (i < parts.length) {
    if (parts[i] === "--interval" || parts[i] === "-i") {
      i++;
      if (i < parts.length) {
        interval = parseInt(parts[i], 10) || 0;
        i++;
      }
    } else if (parts[i] === "--pipeline" || parts[i] === "-p") {
      i++;
      if (i < parts.length && !parts[i].startsWith("-")) {
        pipeline = parts[i];
        i++;
      }
    } else {
      queryParts.push(parts[i]);
      i++;
    }
  }

  return {
    interval,
    pipeline,
    query: queryParts.join(" "),
  };
}

function parseRunArgs(args: string): { pipeline: string; query: string } {
  const parts = parseArgs(args);

  let pipeline = "";
  let queryParts: string[] = [];
  let i = 0;

  while (i < parts.length) {
    if (parts[i] === "--pipeline" || parts[i] === "-p") {
      i++;
      if (i < parts.length && !parts[i].startsWith("-")) {
        pipeline = parts[i];
        i++;
      }
    } else {
      queryParts.push(parts[i]);
      i++;
    }
  }

  return {
    pipeline,
    query: queryParts.join(" "),
  };
}

// Schedule subcommands

const scheduleStartCommand: SlashCommand = {
  name: "start",
  description: "创建定时 auto_harness 任务",
  usage: "/auto-harness schedule start --interval <hours> [--pipeline <pipeline>] <query>",
  example: "/auto-harness schedule start --interval 4 --pipeline extended_evolve_pipeline 优化上下文压缩能力",
  kind: CommandKind.BUILT_IN,
  takesArgs: true,
  completion: (_ctx, partial) => {
    const parts = partial.trim().split(/\s+/).filter(Boolean);
    const lastPart = parts[parts.length - 1] || "";

    // Helper to build completion preserving existing arguments
    const buildCompletion = (completion: string): string => {
      const existingParts = parts.slice(0, -1);
      return [...existingParts, completion].join(" ");
    };

    // If --interval is typed, suggest --interval + value
    if (lastPart === "--interval") {
      return ["1", "2", "4", "8", "12", "24"].map(v => buildCompletion(`--interval ${v}`));
    }

    // If -i is typed, suggest -i + value
    if (lastPart === "-i") {
      return ["1", "2", "4", "8", "12", "24"].map(v => buildCompletion(`-i ${v}`));
    }

    // If we're typing a value after --interval/-i
    const intervalIndex = parts.indexOf("--interval");
    const shortIntervalIndex = parts.indexOf("-i");
    const intervalValues = ["1", "2", "4", "8", "12", "24"];
    if (intervalIndex !== -1 || shortIntervalIndex !== -1) {
      const flagPos = Math.max(intervalIndex, shortIntervalIndex);
      if (parts.length === flagPos + 1 && !lastPart.startsWith("-") && /^\d/.test(lastPart)) {
        const flag = intervalIndex !== -1 ? "--interval" : "-i";
        return intervalValues
          .filter(v => v.startsWith(lastPart))
          .map(v => buildCompletion(`${flag} ${v}`));
      }
    }

    // Check pipeline completions (handles --pipeline/-p and values)
    const pipelineCompletions = getPipelineCompletions(partial, parts);
    if (pipelineCompletions.length > 0) return pipelineCompletions;

    // Otherwise suggest flags that aren't already used
    const usedFlags: string[] = [];
    if (parts.includes("--interval") || parts.includes("-i")) usedFlags.push("--interval", "-i");
    if (parts.includes("--pipeline") || parts.includes("-p")) usedFlags.push("--pipeline", "-p");

    if (lastPart.startsWith("-")) {
      const completions: string[] = [];
      // For interval flags: show only flag + value combinations (skip bare flag)
      if (!usedFlags.includes("--interval") && "--interval".startsWith(lastPart)) {
        for (const v of intervalValues) {
          completions.push(buildCompletion(`--interval ${v}`));
        }
      }
      if (!usedFlags.includes("-i") && "-i".startsWith(lastPart)) {
        for (const v of intervalValues) {
          completions.push(buildCompletion(`-i ${v}`));
        }
      }
      // For pipeline flags: show only flag + value combinations (skip bare flag)
      if (!usedFlags.includes("--pipeline") && "--pipeline".startsWith(lastPart)) {
        for (const v of PIPELINE_VALUES) {
          completions.push(buildCompletion(`--pipeline ${v}`));
        }
      }
      if (!usedFlags.includes("-p") && "-p".startsWith(lastPart)) {
        for (const v of PIPELINE_VALUES) {
          completions.push(buildCompletion(`-p ${v}`));
        }
      }
      return completions;
    }

    return [];
  },
  action: async (ctx, args) => {
    const parsed = parseScheduleStartArgs(args);

    if (!parsed.interval || parsed.interval < 1) {
      ctx.addItem(
        addError(ctx.sessionId, "用法: /auto-harness schedule start --interval <hours> [--pipeline <pipeline>] <query>\npipeline: optimize_expert_harness (生成扩展包), optimize_meta_harness (提交 PR)")
      );
      return;
    }

    if (!parsed.query) {
      ctx.addItem(
        addError(ctx.sessionId, "请提供执行目标 query")
      );
      return;
    }

    // Ask user to select pipeline if not specified
    let pipeline = parsed.pipeline;
    if (!pipeline) {
      try {
        const [answer] = await ctx.askQuestions([
          {
            header: "Pipeline",
            question: "请选择 Pipeline 类型:",
            options: [
              { label: "optimize_expert_harness", description: PIPELINE_DISPLAY_NAMES.optimize_expert_harness.desc },
              { label: "optimize_meta_harness", description: PIPELINE_DISPLAY_NAMES.optimize_meta_harness.desc },
            ],
          },
        ]);
        pipeline = answer.selected_options[0];
      } catch {
        // User cancelled
        ctx.addItem(addInfo(ctx.sessionId, "已取消创建任务"));
        return;
      }
    }

    // Validate pipeline value (accept both friendly and backend names)
    const resolvedPipeline = resolvePipelineName(pipeline);
    if (!PIPELINE_BACKEND_VALUES.includes(resolvedPipeline)) {
      ctx.addItem(
        addError(ctx.sessionId, `无效的 pipeline: ${pipeline}\n可选值: ${PIPELINE_DISPLAY_KEYS.join(", ")}`)
      );
      return;
    }

    // For optimize_meta_harness, check git config
    if (resolvedPipeline === "meta_evolve_pipeline") {
      const configCheck = await ctx.request<{ valid: boolean; missing_fields?: Array<{ id: string; prompt: string }> }>("schedule.check_config", {});

      const missingFields = configCheck.missing_fields as Array<{ id: string; prompt: string }> | undefined;
      if (missingFields && missingFields.length > 0) {
        const missingList = missingFields.map(f => `  - ${f.prompt}`).join("\n");
        ctx.addItem(
          addInfo(ctx.sessionId, `optimize_meta_harness 需要配置 git 信息:\n${missingList}\n\n请使用 /config edit 配置这些字段后重试`)
        );
        return;
      }
    }

    // Ask user whether to run immediately
    let run_immediately = false;
    {
      try {
        const [answer] = await ctx.askQuestions([
          {
            header: "立即执行",
            question: "是否立即执行一次任务？（如选否，则等待首个周期后再执行）",
            options: [{ label: "立即执行" }, { label: "等待周期" }],
          },
        ]);
        run_immediately = answer.selected_options[0] === "立即执行";
      } catch {
        // User cancelled or timeout, proceed without immediate execution
        run_immediately = false;
      }
    }

    // Create the scheduled task
    const result = await ctx.request<{ error?: string; task_id?: string; next_run_time?: string }>("schedule.create", {
      interval_hours: parsed.interval,
      query: parsed.query,
      pipeline: resolvedPipeline,
      run_immediately: run_immediately,
    });

    if (result.error) {
      ctx.addItem(
        addError(ctx.sessionId, `创建失败: ${result.error}`)
      );
      return;
    }

    ctx.addItem(
      addInfo(
        ctx.sessionId,
        `\n定时任务已创建\nID: ${result.task_id}\nPipeline: ${pipelineDisplayLabel(pipeline)}\n下次执行: ${formatLocalTime(result.next_run_time)}\n间隔: 每 ${parsed.interval} 小时${run_immediately ? "\n(已立即执行一次)" : ""}\n`
      )
    );
  },
};

const scheduleListCommand: SlashCommand = {
  name: "list",
  description: "列出所有任务",
  kind: CommandKind.BUILT_IN,
  takesArgs: false,
  action: async (ctx, _args) => {
    ctx.addItem(addInfo(ctx.sessionId, "\n正在查询任务...\n", "i"));

    const result = await ctx.request<{ tasks?: Array<{ task_id: string; query: string; status: string; interval_hours: number; next_run_time: string; created_at: string; is_one_time?: boolean; pipeline?: string }> }>("schedule.list", {});

    const tasks = result.tasks as Array<{ task_id: string; query: string; status: string; interval_hours: number; next_run_time: string; created_at: string; is_one_time?: boolean; pipeline?: string }> | undefined;
    if (!tasks || tasks.length === 0) {
      ctx.addItem(addInfo(ctx.sessionId, "\n暂无任务\n", "i"));
      return;
    }

    const lines = ["\n【任务列表】"];
    for (const task of tasks) {
      const statusEmoji = task.status === "running" ? "[运行中]" :
                         task.status === "pending" ? "[等待]" :
                         task.status === "cancelled" ? "[已取消]" : "[已完成]";
      const isOneTime = task.is_one_time ? "[一次性]" : "";
      const queryPreview = task.query.length > 50 ? task.query.substring(0, 50) + "..." : task.query;
      const pipelineInfo = task.pipeline ? `Pipeline: ${pipelineDisplayLabel(task.pipeline)}` : "";
      lines.push(
        `${statusEmoji}${isOneTime} ${task.task_id} - ${queryPreview}`
      );
      // Show interval only for recurring tasks
      if (task.is_one_time) {
        lines.push(`   状态: ${task.status} | 类型: 一次性${pipelineInfo ? ` | ${pipelineInfo}` : ""}`);
      } else {
        lines.push(`   状态: ${task.status} | 间隔: ${task.interval_hours}h | 下次执行: ${formatLocalTime(task.next_run_time)}${pipelineInfo ? ` | ${pipelineInfo}` : ""}`);
      }
      lines.push(`   创建时间: ${formatLocalTime(task.created_at)}`);
      lines.push("");
    }

    lines.push("");
    ctx.addItem(addInfo(ctx.sessionId, lines.join("\n")));
  },
};

const scheduleStatusCommand: SlashCommand = {
  name: "status",
  description: "查看任务详情",
  usage: "/auto-harness schedule status <task_id>",
  kind: CommandKind.BUILT_IN,
  takesArgs: true,
  completion: async (ctx, partial) => {
    // Fetch task list for task_id completion
    try {
      const result = await ctx.request<{ tasks?: Array<{ task_id: string }> }>("schedule.list", {}, 5000);
      const tasks = result.tasks || [];
      const prefix = partial.trim().toLowerCase();
      if (!prefix) return tasks.map((t) => t.task_id);
      return tasks.filter((t) => t.task_id.toLowerCase().startsWith(prefix)).map((t) => t.task_id);
    } catch {
      return [];
    }
  },
  action: async (ctx, args) => {
    const task_id = args.trim();

    if (!task_id) {
      ctx.addItem(
        addError(ctx.sessionId, "用法: /auto-harness schedule status <task_id>")
      );
      return;
    }

    const result = await ctx.request<{ error?: string; task_id?: string; query?: string; status?: string; interval_hours?: number; created_at?: string; next_run_time?: string; current_execution_id?: string; execution_history?: Array<{ execution_id: string; status: string; completed_at?: string }>; is_one_time?: boolean; pipeline?: string }>("schedule.status", { task_id });

    if (result.error) {
      ctx.addItem(
        addError(ctx.sessionId, result.error)
      );
      return;
    }

    const isOneTime = result.is_one_time;
    const lines = [`\n【任务详情: ${result.task_id}】`];
    lines.push(`目标: ${result.query}`);
    lines.push(`状态: ${result.status}`);
    if (result.pipeline) {
      lines.push(`Pipeline: ${pipelineDisplayLabel(result.pipeline)}`);
    }
    if (isOneTime) {
      lines.push(`类型: 一次性任务`);
    } else {
      lines.push(`类型: 定时任务 | 间隔: 每 ${result.interval_hours} 小时`);
      lines.push(`下次执行: ${formatLocalTime(result.next_run_time) || "已取消"}`);
    }
    lines.push(`创建时间: ${formatLocalTime(result.created_at)}`);

    if (result.current_execution_id) {
      lines.push(`当前执行: ${result.current_execution_id}`);
    }

    const history = result.execution_history as Array<{ execution_id: string; status: string; completed_at?: string }> | undefined;
    if (history && history.length > 0) {
      lines.push(`\n【执行历史】(${history.length} 次)`);
      const recentHistory = history.slice(-5);
      for (const record of recentHistory) {
        const statusText = record.status === "success" ? "[成功]" :
                           record.status === "cancelled" ? "[取消]" : "[异常]";
        lines.push(`${statusText} ${record.execution_id} - ${formatLocalTime(record.completed_at) || "进行中"}`);
      }
    }

    lines.push("");
    ctx.addItem(
      addInfo(ctx.sessionId, lines.join("\n"))
    );
  },
};

const scheduleLogsCommand: SlashCommand = {
  name: "logs",
  description: "查看任务执行日志（默认显示当前运行日志，--history 查看历史日志）",
  usage: "/auto-harness schedule logs <task_id> [--history <n>]",
  example: "/auto-harness schedule logs sch_abc123 --history 0",
  kind: CommandKind.BUILT_IN,
  takesArgs: true,
  completion: async (ctx, partial) => {
    // Check for trailing space before trimming (to detect completed arguments)
    const hasTrailingSpace = partial.endsWith(" ");
    const parts = partial.trim().split(/\s+/).filter(Boolean);
    const lastPart = parts[parts.length - 1] || "";

    // Extract history index value if present (to exclude from task_id detection)
    const historyMatch = partial.match(/--history\s+(\d+)/);
    const historyIndexValue = historyMatch ? historyMatch[1] : null;

    // Find existing task_id: first non-flag, non-history-index argument
    const existingTaskId = parts.find((p) => {
      return !p.startsWith("-") && p !== historyIndexValue && !/^\d+$/.test(p);
    }) || "";

    // Helper: build completion string preserving existing arguments
    // Returns the full argument string including existing args and the completion
    const buildCompletion = (completion: string, replaceLast: boolean = true): string => {
      const prefixParts = replaceLast ? parts.slice(0, -1) : parts;
      return [...prefixParts, completion].join(" ");
    };

    // Step 1: Check if we need a history index value
    // --history is present and we're at the value position
    if (parts.includes("--history")) {
      const historyIdx = parts.indexOf("--history");
      // If we're exactly at the position after --history (typing the value)
      if (parts.length === historyIdx + 1) {
        const values = ["0", "1", "2", "3", "4"];
        if (lastPart && /^\d/.test(lastPart)) {
          // Preserve existing task_id before --history when completing index value
          const taskIdBeforeHistory = parts.slice(0, historyIdx).find((p) => !p.startsWith("-"));
          const filtered = values.filter((v) => v.startsWith(lastPart));
          if (taskIdBeforeHistory) {
            return filtered.map((v) => `${taskIdBeforeHistory} --history ${v}`);
          }
          return filtered.map((v) => buildCompletion(v));
        }
        // No number typed yet, suggest values with existing args preserved
        const taskIdBeforeHistory = parts.slice(0, historyIdx).find((p) => !p.startsWith("-"));
        if (taskIdBeforeHistory) {
          return values.map((v) => `${taskIdBeforeHistory} --history ${v}`);
        }
        return values.map((v) => buildCompletion(v));
      }
      // If history index is complete with trailing space, suggest task_ids
      // Check: history value exists AND there's a trailing space
      if (parts.length >= historyIdx + 2 && /^\d+$/.test(parts[historyIdx + 1]) && hasTrailingSpace) {
        try {
          const result = await ctx.request<{ tasks?: Array<{ task_id: string }> }>("schedule.list", {}, 5000);
          const tasks = result.tasks || [];
          // Return full string with existing args + task_id
          const existingArgs = parts.slice(0, -1).join(" ");
          return tasks.map((t) => `${existingArgs} ${t.task_id}`);
        } catch {
          return [];
        }
      }
      // If history index is complete but no trailing space, don't suggest task_ids yet
      if (parts.length === historyIdx + 2 && /^\d+$/.test(parts[historyIdx + 1])) {
        return [];
      }
    }

    // Step 2: Check if lastPart is exactly --history - suggest values immediately
    if (lastPart === "--history") {
      // Preserve existing task_id when suggesting history index values
      if (existingTaskId) {
        return ["0", "1", "2", "3", "4"].map((v) => `${existingTaskId} --history ${v}`);
      }
      return ["0", "1", "2", "3", "4"].map((v) => buildCompletion(v));
    }

    // Step 3: If typing a flag, suggest --history with existing args preserved
    if (lastPart.startsWith("-") && lastPart !== "--history") {
      if (parts.includes("--history")) return [];
      // Preserve existing task_id when completing --history flag
      if (existingTaskId) {
        return ["--history"].filter((f) => f.startsWith(lastPart)).map((f) => `${existingTaskId} ${f}`);
      }
      return ["--history"].filter((f) => f.startsWith(lastPart)).map((f) => buildCompletion(f));
    }

    // Step 4: If we have a task_id already, suggest --history flag (preserve task_id)
    if (existingTaskId && !parts.includes("--history")) {
      return [`${existingTaskId} --history`];
    }

    // Step 5: Otherwise, suggest task_ids
    try {
      const result = await ctx.request<{ tasks?: Array<{ task_id: string }> }>("schedule.list", {}, 5000);
      const tasks = result.tasks || [];
      if (!lastPart) return tasks.map((t) => t.task_id);
      return tasks.filter((t) => t.task_id.toLowerCase().startsWith(lastPart.toLowerCase())).map((t) => t.task_id);
    } catch {
      return [];
    }
  },
  action: async (ctx, args) => {
    const parsed = parseLogArgs(args);

    if (!parsed.task_id) {
      ctx.addItem(
        addError(ctx.sessionId, "用法: /auto-harness schedule logs <task_id> [--history <n>]")
      );
      return;
    }

    if (parsed.log_type === "current") {
      // Tail -f style streaming for current running task
      await streamCurrentLogs(ctx, parsed.task_id);
    } else {
      // History mode: read full log in batches
      await readFullHistoryLogs(ctx, parsed.task_id, parsed.history_index);
    }
  },
};

interface LogEntry {
  event_type?: string;
  content?: string;
  stage?: string;
  status?: string;
  timestamp?: string;
  message?: string;
  is_processing?: boolean;
  source_chunk_type?: string;
  tool_name?: string;
  is_error?: boolean;
  // Error message (from chat.error event)
  error?: string;
  // Pipeline and stages info (from harness.message)
  pipeline?: string;
  stages?: Array<{ slot: string; display_name: string }>;
  // Session finished info
  is_terminal?: boolean;
  results_count?: number;
  // Extension-level fields (from harness.stage_result with scope='extension')
  scope?: string;              // 'extension' indicates extension-level event
  extension_name?: string;     // Extension name (e.g., context_fencing, merged_extensions)
  extension_stage?: string;    // 'implement_ext' | 'verify_ext' | 'activate_ext' | 'merge_ext'
  parent_stage?: string;       // Parent stage (e.g., 'build_verify', 'activate')
  task_id?: string;            // Task ID for extension
  // Extension ready fields (harness.extension_ready)
  runtime_path?: string;       // Runtime extension directory path
  extension_runtime_path?: string;
  config_path?: string;
  runtime_extensions?: Array<{ extension_name: string; runtime_path: string; config_path: string }>;
  components_summary?: { rails?: number; tools?: number; skills?: number };
  // Activate interaction fields (harness.activate_interaction)
  interaction_type?: string;
  interaction_id?: string;
  options?: string[];
  // Stage result messages
  messages?: string[];
  metrics?: Record<string, unknown>;
  // Nested tool payload (as in history-parser.ts resolveToolPayload)
  tool_call?: {
    name?: string;
    id?: string;
    tool_call_id?: string;
    arguments?: string | Record<string, unknown>;
    description?: string;
  };
  tool_result?: {
    name?: string;
    tool_name?: string;
    result?: string;
    status?: string;
    success?: boolean;
    summary?: string;
  };
  // Direct fields fallback
  name?: string;
  id?: string;
  tool_call_id?: string;
}

// Stream logs for currently running task (tail -f style)
async function streamCurrentLogs(
  ctx: CommandContext,
  task_id: string
): Promise<void> {
  let offset = 0;
  let isRunning = true;
  let executionId = "";
  let pollInterval = 2000; // 2 seconds
  let maxPolls = 300; // Max 300 polls (~10 minutes) to prevent infinite loop
  let pollCount = 0;
  let consecutiveEmptyPolls = 0;
  const maxEmptyPolls = 3; // Stop after 3 consecutive empty polls when not running

  // Parse state for maintaining pipeline info across batches
  let parseState: ParseState | undefined;

  // Clear any previous interrupt flag before starting new stream
  ctx.clearInterruptRequested();

  ctx.addItem(addInfo(ctx.sessionId, `\n【实时日志跟踪: ${task_id}】\n正在连接...\n`));

  // Helper: check interrupt and exit if requested
  const checkInterrupt = (): boolean => {
    if (ctx.isInterruptRequested()) {
      ctx.clearInterruptRequested();
      ctx.addItem(addInfo(ctx.sessionId, `\n【日志跟踪已中断】`));
      return true;
    }
    return false;
  };

  // Helper: interruptible request - checks interrupt flag while waiting
  const interruptibleRequest = async <T>(
    method: string,
    params: Record<string, unknown>,
    timeoutMs: number,
    checkIntervalMs: number = 200
  ): Promise<T | null> => {
    const requestPromise = ctx.request<T>(method, params, timeoutMs);
    // Poll interrupt flag periodically while waiting for request
    const interruptChecker = new Promise<null>((resolve) => {
      const interval = setInterval(() => {
        if (ctx.isInterruptRequested()) {
          clearInterval(interval);
          resolve(null);
        }
      }, checkIntervalMs);
      // Clean up interval when request completes
      requestPromise.then(() => clearInterval(interval)).catch(() => clearInterval(interval));
    });
    // Race between request and interrupt
    return Promise.race([requestPromise, interruptChecker]);
  };

  while (isRunning && pollCount < maxPolls) {
    // Check interrupt at start of each loop
    if (checkInterrupt()) return;

    pollCount++;
    try {
      // Use interruptible request for immediate Ctrl+C response
      const result = await interruptibleRequest<{
        error?: string;
        logs?: Array<LogEntry>;
        execution_id?: string;
        total_lines?: number;
        is_running?: boolean;
        has_more?: boolean;
      }>("schedule.logs", {
        task_id,
        log_type: "current",
        offset,
        limit: 3000,
      }, 1200000);  // 60s timeout

      // Request was interrupted - clear flag and show message via helper
      if (result === null) {
        checkInterrupt(); // clears flag and shows message (returns true when flag was set)
        return;
      }

      // Check interrupt after request completes
      if (checkInterrupt()) return;

      // Check for error - likely means execution finished
      if (result.error) {
        // Execution finished - show completion message
        if (result.error.includes("当前无正在执行的日志") || result.error.includes("不存在")) {
          ctx.addItem(addInfo(ctx.sessionId, `\n【任务执行完成】`));
          return;
        }
        ctx.addItem(addError(ctx.sessionId, result.error));
        return;
      }

      executionId = result.execution_id || "";
      isRunning = result.is_running ?? false;

      // Display new logs - aggregate streaming chunks for better display
      const logs = result.logs || [];
      if (logs.length > 0) {
        consecutiveEmptyPolls = 0;

        const parseResult = parseAndAggregateLogs(logs, parseState);
        parseState = parseResult.state;
        for (const section of parseResult.sections) {
          // Check interrupt during log display
          if (checkInterrupt()) return;
          const formattedLine = formatLogSection(section);
          if (formattedLine) {
            ctx.addItem(addInfo(ctx.sessionId, formattedLine, "i"));
          }
        }
        offset = offset + logs.length;
      } else {
        consecutiveEmptyPolls++;
      }

      // Stop if not running and no new logs for a few polls
      if (!isRunning && consecutiveEmptyPolls >= maxEmptyPolls) {
        break;
      }

      // Check for local interrupt request (Ctrl+C)
      if (checkInterrupt()) return;

      // Continue polling if still running - use shorter intervals for faster interrupt response
      if (isRunning) {
        // Use shorter 500ms intervals and check interrupt status each time
        for (let i = 0; i < pollInterval / 500; i++) {
          await new Promise((resolve) => setTimeout(resolve, 500));
          // Check for local interrupt request (Ctrl+C)
          if (checkInterrupt()) return;
        }
      } else {
        // Wait a bit more to get final logs
        await new Promise((resolve) => setTimeout(resolve, 1000));
      }
    } catch (e) {
      ctx.addItem(addError(ctx.sessionId, `日志流错误: ${e}`));
      return;
    }
  }

  if (pollCount >= maxPolls) {
    ctx.addItem(addInfo(ctx.sessionId, `\n【日志跟踪超时终止】`));
  } else {
    ctx.addItem(addInfo(ctx.sessionId, `\n【日志跟踪完成: ${executionId}】`));
  }
}

// Read full history logs in batches
async function readFullHistoryLogs(
  ctx: CommandContext,
  task_id: string,
  history_index: number
): Promise<void> {
  ctx.addItem(addInfo(ctx.sessionId, `\n正在读取完整日志...\n`, "i"));

  let allLogs: Array<LogEntry> = [];
  let offset = 0;
  const batchSize = 5000;
  let executionId = "";
  let completedAt = "";
  let status = "";
  let hasMore = true;

  while (hasMore) {
    const result = await ctx.request<{
      error?: string;
      logs?: Array<LogEntry>;
      execution_id?: string;
      completed_at?: string;
      status?: string;
      total_lines?: number;
      has_more?: boolean;
    }>("schedule.logs", {
      task_id,
      log_type: "history",
      history_index,
      offset,
      limit: batchSize,
    }, 60000);

    if (result.error) {
      ctx.addItem(addError(ctx.sessionId, result.error));
      return;
    }

    // Capture metadata from first batch
    if (offset === 0) {
      executionId = result.execution_id || "";
      completedAt = result.completed_at || "";
      status = result.status || "";
    }

    const logs = result.logs || [];
    if (logs.length === 0) {
      hasMore = false;
      break;
    }

    allLogs.push(...logs);
    offset = offset + logs.length;

    // Check if there's more to read
    hasMore = result.has_more ?? (logs.length >= batchSize);
  }

  // Display full aggregated logs
  if (allLogs.length === 0) {
    ctx.addItem(addInfo(ctx.sessionId, "日志为空"));
    return;
  }

  const result = parseAndAggregateLogs(allLogs);
  const lines: string[] = [`【执行日志: ${executionId}】`];
  if (completedAt) {
    lines.push(`完成时间: ${formatLocalTime(completedAt)} | 状态: ${status}`);
  }
  lines.push("");
  lines.push("=" .repeat(80));

  for (const section of result.sections) {
    const formattedLine = formatLogSection(section, true);
    if (formattedLine) {
      lines.push(formattedLine);
    }
  }

  lines.push("=" .repeat(80));
  lines.push("");

  const formattedContent = lines.join("\n");

  // Use FileViewer mode if available (TUI environment)
  if (ctx.enterFileViewer) {
    ctx.enterFileViewer(
      formattedContent,
      `执行日志: ${executionId}`,
      `task_id: ${task_id}, history_index: ${history_index}`,
    );
  } else {
    // Fallback: display directly (non-TUI environment)
    ctx.addItem(addInfo(ctx.sessionId, formattedContent));
  }
}

/**
 * Parse and aggregate logs similar to handleIncomingFrame.
 * Merges streaming chunks (chat.delta, chat.reasoning) into complete messages.
 */
interface ParsedLogSection {
  type: "assistant" | "stage" | "status" | "error" | "info" | "pipeline" | "session_finished" | "extension_ready" | "activate_interaction";
  content: string;
  stage?: string;
  status?: string;
  timestamp?: string;
  tool_name?: string;
  tool_success?: boolean;
  // Pipeline info
  pipeline?: string;
  stages?: Array<{ slot: string; display_name: string }>;
  completed_stages?: string[];
  // Extension info (for extended_evolve_pipeline)
  extension_order?: string[];
  extensions_by_name?: Record<string, ExtensionProgressInfo>;
  // Gap count for inline progress bar display
  gap_count?: number;
  // Stage messages (for meta_evolve_pipeline CI fix tracking)
  stage_messages?: string[];
  ci_fix_count?: number;
  // Extension ready info (harness.extension_ready)
  extension_name?: string;
  runtime_path?: string;
  components_summary?: { rails?: number; tools?: number; skills?: number };
  // Activate interaction info (harness.activate_interaction)
  interaction_id?: string;
}

// ANSI color codes for log display differentiation
const ANSI = {
  cyan: "\x1b[36m",     // 工具调用
  green: "\x1b[32m",    // 成功
  red: "\x1b[31m",      // 错误/失败
  yellow: "\x1b[33m",   // 阶段
  blue: "\x1b[34m",     // 状态
  magenta: "\x1b[35m",  // 压缩信息
  brightWhite: "\x1b[97m",  // 辅助输出 (柔和)
  gray: "\x1b[90m",     // 普通 gray (bright black)
  dimGray: "\x1b[38;5;240m", // 更暗的灰色 (256-color mode)
  lightBlue: "\x1b[94m",    // AI 消息 - 浅蓝色 (bright blue)
  bold: "\x1b[1m",
  underline: "\x1b[4m",
  reset: "\x1b[0m",
};

// Helper: get extension status icon
function getExtensionStatusIcon(status: ExtensionProgressStatus): string {
  switch (status) {
    case 'success': return '✓';
    case 'failed': return '✗';
    case 'running': return '⏳';
    case 'timeout': return '⏱';
    case 'waiting': return '?';
    case 'skipped': return '○';
    case 'rejected': return '✗';
    default: return '○'; // pending
  }
}

// Helper: get extension status color
function getExtensionStatusColor(status: ExtensionProgressStatus): string {
  switch (status) {
    case 'success': return ANSI.green;
    case 'failed': return ANSI.red;
    case 'running': return ANSI.yellow;
    case 'timeout': return ANSI.red;
    default: return ANSI.gray;
  }
}

// Helper: format extension status matrix for display
function formatExtensionStatusMatrix(
  extensionOrder: string[],
  extensionsByName: Record<string, ExtensionProgressInfo>
): string {
  const lines: string[] = [];
  // Header with box border (yellow/gold color for extensions) - matching design list width
  lines.push(`${ANSI.yellow}${ANSI.bold}┌${'─'.repeat(26)} 🔧 扩展状态 (${extensionOrder.length} 个) ${'─'.repeat(26)}┐${ANSI.reset}`);

  for (let i = 0; i < extensionOrder.length; i++) {
    const extName = extensionOrder[i];
    const ext = extensionsByName[extName];
    if (!ext) continue;

    const implIcon = getExtensionStatusIcon(ext.implementStatus);
    const implColor = getExtensionStatusColor(ext.implementStatus);
    const verifyIcon = getExtensionStatusIcon(ext.verifyStatus);
    const verifyColor = getExtensionStatusColor(ext.verifyStatus);

    const num = `${ANSI.yellow}${String(i + 1).padStart(2, ' ')}.${ANSI.reset}`;
    lines.push(`${ANSI.yellow}│${ANSI.reset} ${num} ${extName}: ${implColor}实现 ${implIcon}${ANSI.reset} → ${verifyColor}验证 ${verifyIcon}${ANSI.reset}`);
  }

  // Footer border - matching design list width (72 chars)
  lines.push(`${ANSI.yellow}${ANSI.bold}└${'─'.repeat(72)}┘${ANSI.reset}`);

  return lines.join('\n');
}

// Helper: parse named list from messages (same logic as Web's parseNamedList)
// Gaps: separated by ';', Designs: separated by ','
function parseNamedList(messages: string[], prefix: string): string[] {
  const values: string[] = [];
  for (const message of messages) {
    const normalized = message.trim();
    if (!normalized.startsWith(prefix)) continue;
    const raw = normalized.slice(prefix.length).trim();
    // Gaps use ';' separator, Designs use ',' separator (same as Web)
    const parts = prefix === 'Designs:' ? raw.split(',') : raw.split(';');
    for (const part of parts) {
      const value = part.trim();
      // Deduplicate
      if (value && !values.includes(value)) {
        values.push(value);
      }
    }
  }
  return values;
}

// Helper: format stage messages for display (extract key info)
function formatStageMessages(stage: string, messages: string[]): string {
  const lines: string[] = [];

  for (const msg of messages) {
    // Filter out structural messages (same logic as Web)
    const normalized = msg.trim();
    if (normalized.startsWith('Gaps:') || normalized.startsWith('Designs:') ||
        normalized.startsWith('Gap analysis complete:') || normalized.startsWith('Extension design complete:')) {
      continue; // These are shown separately
    }

    // Extract CI results for verify stage
    if (stage === 'verify' && normalized.includes('CI 结果:')) {
      const ciMatch = normalized.match(/CI 结果:\s*(.+)/);
      if (ciMatch) {
        const ciResults = ciMatch[1];
        // Format lint and type-check results
        const lintMatch = ciResults.match(/lint=(\w+)/);
        const typeMatch = ciResults.match(/type-check=(\w+)/);
        if (lintMatch && typeMatch) {
          const lintIcon = lintMatch[1] === 'PASS' ? '✓' : '✗';
          const lintColor = lintMatch[1] === 'PASS' ? ANSI.green : ANSI.red;
          const typeIcon = typeMatch[1] === 'PASS' ? '✓' : '✗';
          const typeColor = typeMatch[1] === 'PASS' ? ANSI.green : ANSI.red;
          lines.push(`  🔍 lint ${lintColor}${lintIcon}${ANSI.reset} type-check ${typeColor}${typeIcon}${ANSI.reset}`);
        }
      }
      continue;
    }

    // Show other messages with indent
    lines.push(`  ${normalized}`);
  }

  return lines.join('\n');
}

// Helper: format gap list for assess stage completion
function formatGapList(messages: string[]): string {
  const gaps = parseNamedList(messages, 'Gaps:');
  if (gaps.length === 0) return '';

  const lines: string[] = [];
  // Header with box border
  lines.push(`${ANSI.cyan}${ANSI.bold}┌${'─'.repeat(25)} 📋 发现 ${gaps.length} 个关键缺口 ${'─'.repeat(25)}┐${ANSI.reset}`);
  // Gap items with numbering
  for (let i = 0; i < gaps.length; i++) {
    const gap = gaps[i];
    const num = `${ANSI.cyan}${String(i + 1).padStart(2, ' ')}.${ANSI.reset}`;
    lines.push(`${ANSI.cyan}│${ANSI.reset} ${num} ${gap}`);
  }
  // Footer border
  lines.push(`${ANSI.cyan}${ANSI.bold}└${'─'.repeat(72)}┘${ANSI.reset}`);
  return lines.join('\n');
}

// Helper: format design list for plan stage completion
function formatDesignList(messages: string[]): string {
  const designs = parseNamedList(messages, 'Designs:');
  if (designs.length === 0) return '';

  const lines: string[] = [];
  // Header with box border (magenta color for designs)
  lines.push(`${ANSI.magenta}${ANSI.bold}┌${'─'.repeat(25)} 📝 生成 ${designs.length} 个设计方案 ${'─'.repeat(25)}┐${ANSI.reset}`);
  // Design items with numbering
  for (let i = 0; i < designs.length; i++) {
    const design = designs[i];
    const num = `${ANSI.magenta}${String(i + 1).padStart(2, ' ')}.${ANSI.reset}`;
    lines.push(`${ANSI.magenta}│${ANSI.reset} ${num} ${design}`);
  }
  // Footer border
  lines.push(`${ANSI.magenta}${ANSI.bold}└${'─'.repeat(72)}┘${ANSI.reset}`);
  return lines.join('\n');
}

// Helper function to calculate visual width (Chinese/CJK chars = 2, others = 1)
function visualWidth(str: string): number {
  let width = 0;
  // Use codePointAt to correctly handle emoji (surrogate pairs)
  for (let i = 0; i < str.length; i++) {
    const code = str.codePointAt(i) || 0;

    // Box drawing characters (U+2500-U+257F) are width 1
    if (code >= 0x2500 && code <= 0x257F) {
      width += 1; // Box drawing
    } else if (
      (code >= 0x4E00 && code <= 0x9FFF) || // CJK Unified Ideographs
      (code >= 0x3000 && code <= 0x303F) || // CJK Symbols
      (code >= 0xFF00 && code <= 0xFFEF) || // Halfwidth/Fullwidth
      code >= 0x1F000 // Emoji and other high ranges
    ) {
      width += 2; // Wide characters (Chinese, emoji)
    } else {
      width += 1; // ASCII and others
    }

    // Skip low surrogate if we processed a surrogate pair (emoji takes 2 UTF-16 units)
    if (code > 0xFFFF) {
      i++; // Skip the low surrogate
    }
  }
  return width;
}

// Helper function to wrap text to a maximum visual width
function wrapText(text: string, maxWidth?: number): string {
  // Auto-detect terminal width if not provided
  // "💬 " prefix takes 4 visual chars (emoji=2, space=1), leave 2 margin on right
  const defaultWidth = (process.stdout.columns || 100) - 6;
  const wrapWidth = maxWidth ?? defaultWidth;

  const lines: string[] = [];
  const paragraphs = text.split('\n');

  for (const paragraph of paragraphs) {
    if (paragraph.trim() === '') {
      lines.push('');
      continue;
    }

    let currentLine = '';
    let currentWidth = 0;

    for (const char of paragraph) {
      const charWidth = visualWidth(char);

      if (currentWidth + charWidth > wrapWidth && currentLine.length > 0) {
        lines.push(currentLine);
        currentLine = char;
        currentWidth = charWidth;
      } else {
        currentLine += char;
        currentWidth += charWidth;
      }
    }

    if (currentLine.length > 0) {
      lines.push(currentLine);
    }
  }

  return lines.join('\n');
}

// Helper function to create a proper box with title embedded in top border
function createBox(title: string, content: string, color: string): string {
  const leftDashes = 32;
  const rightDashes = 37;
  const titleVisualWidth = visualWidth(title);
  const topVisualWidth = 72 + titleVisualWidth;
  const contentPadding = topVisualWidth - 3 - visualWidth(content);

  const topBorder = `╔${"═".repeat(leftDashes)} ${title} ${"═".repeat(rightDashes)}╗`;
  const paddedContent = contentPadding < 0
    ? `║ ${content.substring(0, topVisualWidth - 7)}... ║`
    : `║ ${content}${" ".repeat(Math.max(0, contentPadding))} ║`;
  const bottomBorder = `╚${"═".repeat(topVisualWidth - 1)}╝`;

  return `${ANSI.bold}${color}${topBorder}${ANSI.reset}\n${color}${paddedContent}${ANSI.reset}\n${color}${ANSI.bold}${bottomBorder}${ANSI.reset}`;
}

// Helper: format assistant content with dialog bubble style (light blue)
function formatAssistantContent(content: string): string {
  const wrapped = wrapText(content);
  const lines = wrapped.split('\n');
  // First line has 💬 prefix in light blue, subsequent lines have indentation
  const formattedLines = lines.map((line, index) => {
    if (index === 0) {
      return `${ANSI.lightBlue}💬 ${line}${ANSI.reset}`;
    }
    return `${ANSI.lightBlue}   ${line}${ANSI.reset}`;  // 3 spaces indent to align with 💬
  });
  return formattedLines.join('\n');
}

// Format log section for display (compact for streaming, detailed for history)
function formatLogSection(section: ParsedLogSection, detailed: boolean = false): string | null {
  switch (section.type) {
    case "assistant":
      return formatAssistantContent(section.content);

    case "pipeline":
      const stagesDisplay = section.stages?.map((s) => s.display_name).join(" → ") || "";
      return `\n${createBox(`Pipeline: ${pipelineDisplayLabel(section.pipeline || "unknown")}`, `流程: ${stagesDisplay}`, ANSI.cyan)}\n`;

    case "stage":
      const stageDisplayName = section.stages?.find((s) => s.slot === section.stage)?.display_name || section.stage || "?";

      if (section.status) {
        const progressBar = formatStageProgress(section.stages, section.completed_stages, undefined, section.gap_count, section.extension_order?.length);
        const icon = section.status === "success" ? "✅" : section.status === "failed" ? "❌" : "⏸️";
        const color = section.status === "success" ? ANSI.green : section.status === "failed" ? ANSI.red : ANSI.yellow;
        const statusText = section.status === "success" ? "完成" : section.status === "failed" ? "失败" : section.status;

        let detailLines: string[] = [];
        if (section.stage === 'assess' && section.stage_messages) {
          const gapList = formatGapList(section.stage_messages);
          if (gapList) detailLines.push(gapList);
          const otherMsgs = formatStageMessages(section.stage, section.stage_messages);
          if (otherMsgs) detailLines.push(otherMsgs);
        }
        if (section.stage === 'plan' && section.stage_messages) {
          const designList = formatDesignList(section.stage_messages);
          if (designList) detailLines.push(designList);
          const otherMsgsPlan = formatStageMessages(section.stage, section.stage_messages);
          if (otherMsgsPlan) detailLines.push(otherMsgsPlan);
        }
        if (section.stage === 'verify' && section.stage_messages) {
          const formattedMsgs = formatStageMessages(section.stage, section.stage_messages);
          if (formattedMsgs) detailLines.push(formattedMsgs);
          if (section.ci_fix_count && section.ci_fix_count > 0) {
            detailLines.push(`  🔄 修复循环: ${section.ci_fix_count} 次`);
          }
        }
        // Extension status matrix shown AFTER stage-specific content, only when extensions have progress
        if (section.extension_order && section.extensions_by_name && section.extension_order.length > 0) {
          const hasProgress = Object.values(section.extensions_by_name).some(
            ext => ext.implementStatus !== 'pending' || ext.verifyStatus !== 'pending'
          );
          if (hasProgress) {
            detailLines.push(formatExtensionStatusMatrix(section.extension_order, section.extensions_by_name));
          }
        }

        const detailsBlock = detailLines.length > 0 ? '\n' + detailLines.join('\n') + '\n' : '';
        return `\n${progressBar}\n${color}${ANSI.bold}${icon} ${stageDisplayName} ${statusText}${ANSI.reset}${detailsBlock}\n`;
      }

      const startProgressBar = formatStageProgress(section.stages, section.completed_stages, section.stage, section.gap_count, section.extension_order?.length);
      const showContent = section.content && section.content !== stageDisplayName;
      const normalizedContent = (section.content || "").trim();

      if (normalizedContent.includes('Gap analysis complete') || normalizedContent.startsWith('Gaps:')) {
        if (detailed) {
          return `\n${startProgressBar}\n${ANSI.yellow}${ANSI.bold}▶ 📊 ${stageDisplayName}${ANSI.reset}\n`;
        }
        return `\n${startProgressBar}\n`;
      }

      if (showContent) {
        const wrappedContent = wrapText(section.content, 100);
        const indentedContent = wrappedContent.split("\n").map(line => "  " + line).join("\n");
        if (detailed) {
          return `\n${startProgressBar}\n${ANSI.yellow}${ANSI.bold}▶ 📊 ${stageDisplayName}${ANSI.reset}\n${ANSI.yellow}${indentedContent}${ANSI.reset}\n`;
        }
        return `\n${startProgressBar}\n${ANSI.gray}${indentedContent}${ANSI.reset}\n`;
      }

      if (detailed) {
        return `\n${startProgressBar}\n${ANSI.yellow}${ANSI.bold}▶ 📊 ${stageDisplayName}${ANSI.reset}\n`;
      }
      return `\n${startProgressBar}\n`;

    case "session_finished":
      const finishedIcon = section.status === "success" ? "🎉" : "⚠️";
      const finishedColor = section.status === "success" ? ANSI.green : ANSI.yellow;
      return `\n${createBox(`${finishedIcon} ${section.content}`, `Pipeline: ${pipelineDisplayLabel(section.pipeline || "unknown")}`, finishedColor)}\n`;

    case "status":
      return `${ANSI.blue}▶ ${section.content}${ANSI.reset}`;

    case "error":
      return `${ANSI.red}${ANSI.bold}🔥 错误: ${section.content}${ANSI.reset}`;

    case "info":
      return `${ANSI.gray}  · ${section.content}${ANSI.reset}`;

    case "extension_ready":
      const extReadyLines: string[] = [];
      extReadyLines.push(`${ANSI.green}${ANSI.bold}📦 ${section.content}${ANSI.reset}`);
      if (section.runtime_path) {
        extReadyLines.push(`  目录: ${ANSI.cyan}${ANSI.underline}${section.runtime_path}${ANSI.reset}`);
      }
      if (section.components_summary) {
        const cs = section.components_summary;
        const parts: string[] = [];
        if (cs.rails && cs.rails > 0) parts.push(`${ANSI.cyan}${cs.rails} rails${ANSI.reset}`);
        if (cs.tools && cs.tools > 0) parts.push(`${ANSI.yellow}${cs.tools} tools${ANSI.reset}`);
        if (cs.skills && cs.skills > 0) parts.push(`${ANSI.magenta}${cs.skills} skills${ANSI.reset}`);
        if (parts.length > 0) {
          extReadyLines.push(`  组件: ${parts.join(' ')}`);
        }
      }
      return extReadyLines.join('\n');

    case "activate_interaction":
      return `${ANSI.yellow}${ANSI.bold}⏳ ${section.content}${ANSI.reset}`;

    default:
      return null;
  }
}

// Helper: format stage progress bar with visual progress indicator
function formatStageProgress(
  stages?: Array<{ slot: string; display_name: string }>,
  completedStages?: string[],
  currentStage?: string,
  // Inline count display for progress bar
  gapCount?: number,
  extensionCount?: number
): string {
  if (!stages || stages.length === 0) return "";

  // Calculate progress
  const completedCount = completedStages?.length || 0;
  const total = stages.length;
  const percent = Math.min(100, Math.round((completedCount / total) * 100));

  // Create progress bar (fixed 80 chars for consistent look)
  const barLength = 80;
  const filledLength = Math.min(barLength, Math.round((completedCount / total) * barLength));
  const bar = `${ANSI.green}${"█".repeat(filledLength)}${ANSI.reset}${ANSI.gray}${"░".repeat(barLength - filledLength)}${ANSI.reset}`;

  // Create stage status line with icons and names
  const parts = stages.map((s) => {
    const isCompleted = completedStages?.includes(s.slot);
    const isCurrent = currentStage === s.slot;

    // Add inline count for specific stages
    let inlineCount = '';
    if (s.slot === 'assess' && gapCount && gapCount > 0 && (isCompleted || isCurrent)) {
      inlineCount = ` (${gapCount})`;
    }
    if (s.slot === 'plan' && extensionCount && extensionCount > 0 && (isCompleted || isCurrent)) {
      inlineCount = ` (${extensionCount})`;
    }

    if (isCompleted) {
      return `${ANSI.green}✓ ${s.display_name}${inlineCount}${ANSI.reset}`;
    } else if (isCurrent) {
      return `${ANSI.yellow}▶ ${s.display_name}${inlineCount}${ANSI.reset}`;
    } else {
      return `${ANSI.gray}○ ${s.display_name}${ANSI.reset}`;
    }
  });

  // Combine: progress bar with percent, then stage names on separate line
  return `${ANSI.bold}进度${ANSI.reset} ${bar} ${percent}%\n${parts.join(" → ")}`;
}

// Extension progress status types (matching Web's harnessStore.ts)
type ExtensionProgressStatus = 'pending' | 'running' | 'success' | 'failed' | 'timeout' | 'waiting' | 'skipped' | 'rejected';

// Extension progress info for tracking each extension's status
interface ExtensionProgressInfo {
  extensionName: string;
  implementStatus: ExtensionProgressStatus;
  verifyStatus: ExtensionProgressStatus;
  activateStatus: ExtensionProgressStatus;
}

// State for incremental log parsing (to maintain pipeline info across batches)
interface ParseState {
  pipelineInfo: { pipeline: string; stages: Array<{ slot: string; display_name: string }> } | null;
  completedStages: string[];
  currentStage: string | null;
  extensionOrder: string[];
  extensionsByName: Record<string, ExtensionProgressInfo>;
  gapCount: number;
  ciFixCount: number;
}

function parseAndAggregateLogs(
  logs: Array<LogEntry>,
  initialState?: ParseState
): { sections: ParsedLogSection[]; state: ParseState } {
  const sections: ParsedLogSection[] = [];

  // Track pipeline progress - use initial state if provided (for incremental parsing)
  let pipelineInfo = initialState?.pipelineInfo ?? null;
  const completedStages: string[] = initialState?.completedStages ?? [];
  let currentStage = initialState?.currentStage ?? null;
  const extensionOrder: string[] = initialState?.extensionOrder ?? [];
  const extensionsByName: Record<string, ExtensionProgressInfo> = initialState?.extensionsByName ?? {};
  let gapCount = initialState?.gapCount ?? 0;
  let ciFixCount = initialState?.ciFixCount ?? 0;

  // Note: pipeline type is determined dynamically in the loop when pipelineInfo is set

  for (const log of logs) {
    const eventType = log.event_type || "";
    const content = log.content || log.message || "";

    // Pipeline-specific filtering: only show chat.final and chat.error
    // Skip all other chat events (reasoning, delta, tool_call, tool_result, processing_status)
    if (eventType.startsWith("chat.")) {
      if (eventType !== "chat.final" && eventType !== "chat.error") {
        continue;
      }
    }

    switch (eventType) {
      case "chat.final":
        if (content) {
          sections.push({ type: "assistant", content: content });
        }
        break;

      case "chat.error":
        const errorMsg = log.error || content || "未知错误";
        sections.push({ type: "error", content: errorMsg });
        break;

      case "harness.message":
        // Check if this is pipeline info with stages
        if (log.stages && log.pipeline) {
          // Pipeline header - show workflow structure
          pipelineInfo = { pipeline: log.pipeline, stages: log.stages };
          sections.push({
            type: "pipeline",
            content: log.content || "",
            pipeline: log.pipeline,
            stages: log.stages,
          });
          break;
        }

        // Regular stage message
        const stage = log.stage || "";
        if (currentStage !== stage) {
          currentStage = stage;
        }

        sections.push({
          type: "stage",
          content: content,
          stage: stage,
          stages: pipelineInfo?.stages,
          completed_stages: [...completedStages],
        });
        break;

      case "harness.stage_result":
        // Check if this is an extension-level event (scope === 'extension')
        const scope = log.scope || '';
        const extName = log.extension_name;
        const extStage = log.extension_stage || '';

        // Handle merge_ext: merged_extensions is a container, not a design extension
        // Show merge status as standalone info line, not full stage render
        if (extStage === 'merge_ext' && extName === 'merged_extensions') {
          const mergeStatus = log.status || 'pending';
          const mergeText = mergeStatus === 'success' ? '✅ 合并扩展完成' : mergeStatus === 'failed' ? '❌ 合并扩展失败' : '⏳ 合并扩展进行中';
          sections.push({
            type: "info",
            content: mergeText,
          });
          break;
        }

        if (scope === 'extension' && extName) {
          // Extension-level progress update
          const extStatus = (log.status || 'pending') as ExtensionProgressStatus;

          // Skip merged_extensions from extension matrix (it's a merge container)
          // Show activation status as info line, not full stage render
          if (extName === 'merged_extensions') {
            if (extStage === 'activate_ext') {
              const actText = extStatus === 'success' ? '✅ 激活合并扩展完成' : extStatus === 'failed' ? '❌ 激活合并扩展失败' : '⏳ 激活合并扩展进行中';
              sections.push({
                type: "info",
                content: actText,
              });
            }
            break;
          }

          // Add to extension order if new (only design extensions, not merged)
          if (!extensionOrder.includes(extName)) {
            extensionOrder.push(extName);
          }

          // Get or create extension info
          const existing = extensionsByName[extName] || {
            extensionName: extName,
            implementStatus: 'pending',
            verifyStatus: 'pending',
            activateStatus: 'pending',
          };

          // Update specific extension stage status
          if (extStage === 'implement_ext') {
            existing.implementStatus = extStatus;
          } else if (extStage === 'verify_ext') {
            existing.verifyStatus = extStatus;
          } else if (extStage === 'activate_ext' || log.parent_stage === 'activate') {
            existing.activateStatus = extStatus;
          }

          extensionsByName[extName] = existing;

          // Output section showing updated extension status matrix
          // Deep-copy extensionsByName so each section captures the state at that point,
          // not the final state (all sections share the same mutable dict otherwise)
          const extensionsSnapshot: Record<string, ExtensionProgressInfo> = {};
          for (const [key, val] of Object.entries(extensionsByName)) {
            extensionsSnapshot[key] = { ...val };
          }
          sections.push({
            type: "stage",
            content: `扩展 ${extName} ${extStage} ${extStatus}`,
            stage: log.stage || log.parent_stage || currentStage || "",
            status: extStatus,
            stages: pipelineInfo?.stages,
            completed_stages: [...completedStages],
            extension_order: [...extensionOrder],
            extensions_by_name: extensionsSnapshot,
            gap_count: gapCount,
          });
          break;
        }

        // Track completed stage (only for stage-level events, deduplicate)
        if (log.stage && !completedStages.includes(log.stage)) {
          completedStages.push(log.stage);
        }

        // For activate stage: skip "running" status (merge/interaction sub-events handle display)
        // Only show the final "success" completion
        if (log.stage === 'activate' && log.status === 'running' && !scope) {
          break;
        }

        // For meta_evolve_pipeline: track CI fix count from messages
        const stageMessages = log.messages || [];
        if (pipelineInfo?.pipeline === "meta_evolve_pipeline" && log.stage === "verify") {
          for (const msg of stageMessages) {
            if (msg.includes('修复循环') || msg.includes('[修复循环]')) {
              ciFixCount++;
            }
          }
        }

        // Extract gap count from assess stage messages (Gaps: ...)
        if (log.stage === 'assess' && stageMessages.length > 0) {
          const gaps = parseNamedList(stageMessages, 'Gaps:');
          gapCount = gaps.length;
        }

        // Extract extension names from plan stage messages (Designs: ...)
        if (log.stage === 'plan' && stageMessages.length > 0) {
          for (const msg of stageMessages) {
            if (msg.startsWith('Designs:')) {
              const designs = msg.slice('Designs:'.length).trim().split(',');
              for (const design of designs) {
                const name = design.trim();
                if (name && !extensionOrder.includes(name)) {
                  extensionOrder.push(name);
                  extensionsByName[name] = {
                    extensionName: name,
                    implementStatus: 'pending',
                    verifyStatus: 'pending',
                    activateStatus: 'pending',
                  };
                }
              }
            }
          }
        }

        // Deep-copy extension info so each section captures state at that point
        const stageExtSnapshot: Record<string, ExtensionProgressInfo> = {};
        for (const [key, val] of Object.entries(extensionsByName)) {
          stageExtSnapshot[key] = { ...val };
        }
        sections.push({
          type: "stage",
          content: `阶段完成: ${log.stage || "unknown"}`,
          stage: log.stage,
          status: log.status,
          stages: pipelineInfo?.stages,
          completed_stages: [...completedStages],
          // Include extension info for display (snapshot at this point)
          extension_order: [...extensionOrder],
          extensions_by_name: stageExtSnapshot,
          // Include gap count for inline progress bar
          gap_count: gapCount,
          stage_messages: stageMessages.length > 0 ? stageMessages : undefined,
          ci_fix_count: ciFixCount,
        });
        break;

      case "harness.session_finished":
        sections.push({
          type: "session_finished",
          content: log.status === "success" ? "任务执行成功" : `任务执行${log.status || "完成"}`,
          status: log.status,
          pipeline: log.pipeline,
        });
        break;

      case "harness.extension_ready":
        // Extension ready: show directory structure and components summary
        const extReadyName = log.extension_name || "unknown";
        const compSummary = log.components_summary;
        const compParts: string[] = [];
        if (compSummary) {
          if (compSummary.rails && compSummary.rails > 0) compParts.push(`${compSummary.rails} rails`);
          if (compSummary.tools && compSummary.tools > 0) compParts.push(`${compSummary.tools} tools`);
          if (compSummary.skills && compSummary.skills > 0) compParts.push(`${compSummary.skills} skills`);
        }
        const compDisplay = compParts.length > 0 ? compParts.join(', ') : '无组件';
        sections.push({
          type: "extension_ready",
          content: `扩展 ${extReadyName} 已就绪`,
          stage: currentStage || "activate",
          extension_name: extReadyName,
          runtime_path: log.extension_runtime_path || log.runtime_path,
          components_summary: compSummary,
        });
        // Also show components count as info line
        if (compParts.length > 0) {
          sections.push({
            type: "info",
            content: `📦 ${extReadyName}: ${compDisplay}`,
          });
        }
        break;

      case "harness.activate_interaction":
        // Activation interaction prompt — show what's being activated
        const actExtName = log.extension_name || "unknown";
        sections.push({
          type: "activate_interaction",
          content: `等待激活确认: ${actExtName}`,
          stage: currentStage || "activate",
          interaction_id: log.interaction_id,
          extension_name: actExtName,
        });
        break;

      default:
        if (content) {
          sections.push({ type: "info", content: `[${eventType}] ${content.substring(0, 100)}` });
        }
        break;
    }
  }

  return { sections, state: { pipelineInfo, completedStages, currentStage, extensionOrder, extensionsByName, gapCount, ciFixCount } };
}

// Format log section for history display (detailed with colors)
const scheduleCancelCommand: SlashCommand = {
  name: "cancel",
  description: "取消任务",
  usage: "/auto-harness schedule cancel <task_id>",
  kind: CommandKind.BUILT_IN,
  takesArgs: true,
  completion: async (ctx, partial) => {
    // Fetch task list for task_id completion
    try {
      const result = await ctx.request<{ tasks?: Array<{ task_id: string; status?: string }> }>("schedule.list", {}, 5000);
      const tasks = result.tasks || [];
      // Only show non-cancelled tasks
      const activeTasks = tasks.filter((t) => t.status !== "cancelled");
      const prefix = partial.trim().toLowerCase();
      if (!prefix) return activeTasks.map((t) => t.task_id);
      return activeTasks.filter((t) => t.task_id.toLowerCase().startsWith(prefix)).map((t) => t.task_id);
    } catch {
      return [];
    }
  },
  action: async (ctx, args) => {
    const task_id = args.trim();

    if (!task_id) {
      ctx.addItem(
        addError(ctx.sessionId, "用法: /auto-harness schedule cancel <task_id>")
      );
      return;
    }

    const result = await ctx.request<{ error?: string; task_id?: string }>("schedule.cancel", { task_id });

    if (result.error) {
      ctx.addItem(
        addError(ctx.sessionId, `取消失败: ${result.error}`)
      );
      return;
    }

    ctx.addItem(
      addInfo(ctx.sessionId, `\n任务已取消: ${result.task_id}\n`)
    );
  },
};

const scheduleDeleteCommand: SlashCommand = {
  name: "delete",
  description: "删除任务（取消并移除）",
  usage: "/auto-harness schedule delete <task_id>",
  kind: CommandKind.BUILT_IN,
  takesArgs: true,
  completion: async (ctx, partial) => {
    // Fetch task list for task_id completion
    try {
      const result = await ctx.request<{ tasks?: Array<{ task_id: string }> }>("schedule.list", {}, 5000);
      const tasks = result.tasks || [];
      const prefix = partial.trim().toLowerCase();
      if (!prefix) return tasks.map((t) => t.task_id);
      return tasks.filter((t) => t.task_id.toLowerCase().startsWith(prefix)).map((t) => t.task_id);
    } catch {
      return [];
    }
  },
  action: async (ctx, args) => {
    const task_id = args.trim();

    if (!task_id) {
      ctx.addItem(
        addError(ctx.sessionId, "用法: /auto-harness schedule delete <task_id>")
      );
      return;
    }

    const result = await ctx.request<{ error?: string; task_id?: string }>("schedule.delete", { task_id });

    if (result.error) {
      ctx.addItem(
        addError(ctx.sessionId, `删除失败: ${result.error}`)
      );
      return;
    }

    ctx.addItem(
      addInfo(ctx.sessionId, `\n任务已删除: ${result.task_id}\n`)
    );
  },
};

// Schedule parent command

const scheduleCommand: SlashCommand = {
  name: "schedule",
  description: "任务管理",
  usage: "/auto-harness schedule <start|list|status|logs|cancel|delete>",
  kind: CommandKind.BUILT_IN,
  takesArgs: true,
  subCommands: [scheduleStartCommand, scheduleListCommand, scheduleStatusCommand, scheduleLogsCommand, scheduleCancelCommand, scheduleDeleteCommand],
  completion: (_ctx, partial) => {
    const subNames = ["start", "list", "status", "logs", "cancel", "delete"];
    const prefix = partial.trim().toLowerCase();
    if (!prefix) return subNames;
    return subNames.filter((n) => n.startsWith(prefix));
  },
  action: (ctx, args) => {
    const subcommand = args.trim().split(/\s+/)[0];
    if (!subcommand) {
      ctx.addItem(
        addError(ctx.sessionId, "用法: /auto-harness schedule <subcommand>\n子命令: start, list, status, logs, cancel, delete")
      );
    }
  },
};

// Run command - one-time task execution

const runCommand: SlashCommand = {
  name: "run",
  description: "执行一次性 auto_harness 任务",
  usage: "/auto-harness run [--pipeline <pipeline>] <query>",
  example: "/auto-harness run --pipeline extended_evolve_pipeline 优化数据库查询性能",
  kind: CommandKind.BUILT_IN,
  takesArgs: true,
  completion: (_ctx, partial) => {
    const parts = partial.trim().split(/\s+/).filter(Boolean);
    // Check pipeline completions (handles --pipeline/-p and values with preserved args)
    return getPipelineCompletions(partial, parts);
  },
  action: async (ctx, args) => {
    const parsed = parseRunArgs(args);

    if (!parsed.query) {
      ctx.addItem(
        addError(ctx.sessionId, "用法: /auto-harness run [--pipeline <pipeline>] <query>\npipeline: optimize_expert_harness (生成扩展包), optimize_meta_harness (提交 PR)")
      );
      return;
    }

    // Ask user to select pipeline if not specified
    let pipeline = parsed.pipeline;
    if (!pipeline) {
      try {
        const [answer] = await ctx.askQuestions([
          {
            header: "Pipeline",
            question: "请选择 Pipeline 类型:",
            options: [
              { label: "optimize_expert_harness", description: PIPELINE_DISPLAY_NAMES.optimize_expert_harness.desc },
              { label: "optimize_meta_harness", description: PIPELINE_DISPLAY_NAMES.optimize_meta_harness.desc },
            ],
          },
        ]);
        pipeline = answer.selected_options[0];
      } catch {
        // User cancelled
        ctx.addItem(addInfo(ctx.sessionId, "已取消创建任务"));
        return;
      }
    }

    // Validate pipeline value (accept both friendly and backend names)
    const resolvedPipeline = resolvePipelineName(pipeline);
    if (!PIPELINE_BACKEND_VALUES.includes(resolvedPipeline)) {
      ctx.addItem(
        addError(ctx.sessionId, `无效的 pipeline: ${pipeline}\n可选值: ${PIPELINE_DISPLAY_KEYS.join(", ")}`)
      );
      return;
    }

    // For optimize_meta_harness, check git config
    if (resolvedPipeline === "meta_evolve_pipeline") {
      const configCheck = await ctx.request<{ valid: boolean; missing_fields?: Array<{ id: string; prompt: string }> }>("schedule.check_config", {});

      const missingFields = configCheck.missing_fields as Array<{ id: string; prompt: string }> | undefined;
      if (missingFields && missingFields.length > 0) {
        const missingList = missingFields.map(f => `  - ${f.prompt}`).join("\n");
        ctx.addItem(
          addInfo(ctx.sessionId, `optimize_meta_harness 需要配置 git 信息:\n${missingList}\n\n请使用 /config edit 配置这些字段后重试`)
        );
        return;
      }
    }

    ctx.addItem(addInfo(ctx.sessionId, `\n正在创建一次性任务...\nPipeline: ${pipelineDisplayLabel(pipeline)}\n`, "i"));

    // Create and execute one-time task
    const result = await ctx.request<{ error?: string; task_id?: string; status?: string; message?: string }>("schedule.run", {
      query: parsed.query,
      pipeline: resolvedPipeline,
    });

    if (result.error) {
      ctx.addItem(
        addError(ctx.sessionId, `创建失败: ${result.error}`)
      );
      return;
    }

    ctx.addItem(
      addInfo(ctx.sessionId, `\n一次性任务已创建并开始执行\nID: ${result.task_id}\nPipeline: ${pipelineDisplayLabel(pipeline)}\n状态: ${result.status}\n`)
    );

    // Start streaming logs
    if (result.task_id) {
      await streamCurrentLogs(ctx, result.task_id);
    }
  },
};

// Main auto-harness command

export function createAutoHarnessCommand(): SlashCommand {
  return {
    name: "auto-harness",
    description: "Auto-Harness 任务管理",
    hidden: false, // Temporarily hidden from TUI, core functionality preserved for future re-enable
    kind: CommandKind.BUILT_IN,
    takesArgs: true,
    subCommands: [runCommand, scheduleCommand],
    completion: (_ctx, partial) => {
      const subNames = ["run", "schedule"];
      const prefix = partial.trim().toLowerCase();
      if (!prefix) return subNames;
      return subNames.filter((n) => n.startsWith(prefix));
    },
    action: (ctx, args) => {
      const text = args.trim();
      if (!text) {
        ctx.addItem(
          addError(ctx.sessionId, "用法: /auto-harness <run|schedule>\n子命令: run, schedule")
        );
      }
    },
  };
}

function formatLocalTime(isoTime?: string): string {
  if (!isoTime) return "未知";
  try {
    const date = new Date(isoTime);
    // Format as local time: YYYY-MM-DD HH:mm
    const year = date.getFullYear();
    const month = String(date.getMonth() + 1).padStart(2, "0");
    const day = String(date.getDate()).padStart(2, "0");
    const hours = String(date.getHours()).padStart(2, "0");
    const minutes = String(date.getMinutes()).padStart(2, "0");
    return `${year}-${month}-${day} ${hours}:${minutes}`;
  } catch {
    return isoTime;
  }
}

function parseLogArgs(args: string): { task_id: string; log_type: string; history_index: number } {
  const parts = parseArgs(args);

  let log_type = "current";
  let history_index = -1;

  // First, extract --history index if present
  const historyMatch = args.match(/--history\s+(\d+)/);
  const historyIndexValue = historyMatch ? historyMatch[1] : null;

  if (args.includes("--current")) {
    log_type = "current";
    history_index = -1;
  } else if (historyMatch) {
    log_type = "history";
    history_index = parseInt(historyMatch[1], 10);
  }

  // Find task_id: first non-flag argument, excluding history index value
  const task_id = parts.find((p) => {
    return !p.startsWith("-") && p !== historyIndexValue;
  }) || "";

  return { task_id, log_type, history_index };
}