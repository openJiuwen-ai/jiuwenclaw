import { parseArgs } from "../helpers.js";

export type AutoHarnessIssueStageProgress = {
  stage: string;
  name?: string;
  status: string;
  messages?: string[];
};

export type AutoHarnessIssueProgress = {
  summary?: string;
  stages?: AutoHarnessIssueStageProgress[];
  completed_stages?: string[];
  current_stage?: string;
  failed_stage?: string;
  last_message?: string;
  last_error?: string;
  failure_code?: string;
  pr_url?: string;
};

export type IssueFixTaskStatus = {
  error?: string;
  task_id?: string;
  status?: string;
  progress?: AutoHarnessIssueProgress;
};

export type IssueFixWatchItem = {
  issue: number;
  taskId?: string;
  status: string;
  reason?: string;
  lastMessage?: string;
  currentStage?: string;
  failedStage?: string;
  failureCode?: string;
  prUrl?: string;
};

export type IssueWatchArgs = {
  repo: string;
  labels: string[];
  max_issues: number;
  dry_run: boolean;
  comment_on_start: boolean;
  pipeline: string;
  issue_numbers: number[];
  max_auto_difficulty: string;
  concurrency: number;
};

export type IssueFixArgs = {
  repo: string;
  issue_numbers: number[];
  dry_run: boolean;
  comment_on_start: boolean;
  pipeline: string;
  max_auto_difficulty: string;
  concurrency: number;
};

export type IssueWatchResult = {
  fetched?: number;
  started?: Array<{
    number?: number;
    issue?: number;
    task_id?: string;
    status?: string;
    title?: string;
    difficulty?: { level?: string; score?: number; reasons?: string[] };
  }>;
  skipped?: Array<{
    issue?: number;
    reason?: string;
    status?: string;
    human_label?: string;
    difficulty?: { level?: string; score?: number; reasons?: string[] };
  }>;
  reconciled?: Array<{ number?: number; status?: string; pr_url?: string }>;
};

type PipelineNameResolver = (name: string) => string;

export const ISSUE_FIX_WATCH_INTERVAL_MS = 5000;
export const ISSUE_FIX_WATCH_MAX_POLLS = 720;
export const ISSUE_DIFFICULTY_VALUES = new Set(["low", "medium", "high", "unclear"]);

const ISSUE_FIX_TERMINAL_STATUSES = new Set([
  "success",
  "failed",
  "cancelled",
  "skipped",
  "needs_human",
]);

export function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function latestStageWithStatus(
  progress: AutoHarnessIssueProgress | undefined,
  status: string,
): string {
  if (!progress?.stages) return "";
  const stage = progress.stages.find((item) => item.status === status);
  return stage?.stage || "";
}

export function issueFixTaskStatusToWatchItem(
  issue: number,
  taskId: string | undefined,
  task: IssueFixTaskStatus | undefined,
  fallbackStatus = "queued",
  reason = "",
): IssueFixWatchItem {
  if (!taskId) {
    return {
      issue,
      status: fallbackStatus,
      reason,
      lastMessage: reason,
    };
  }
  if (!task || task.error) {
    return {
      issue,
      taskId,
      status: "unknown",
      reason: task?.error || reason || "任务状态暂不可用",
      lastMessage: task?.error || reason || "任务状态暂不可用",
    };
  }
  const progress = task.progress;
  const status = task.status || fallbackStatus;
  return {
    issue,
    taskId,
    status: status === "pending" ? "queued" : status,
    currentStage: progress?.current_stage || latestStageWithStatus(progress, "running"),
    failedStage: progress?.failed_stage || latestStageWithStatus(progress, "failed"),
    failureCode: progress?.failure_code || "",
    prUrl: progress?.pr_url || "",
    lastMessage: progress?.last_message || progress?.last_error || reason,
  };
}

export function formatIssueFixStatusLine(item: IssueFixWatchItem): string {
  const issue = `#${item.issue}`.padEnd(7, " ");
  const status = (item.status || "unknown").padEnd(9, " ");
  let stageText = "";
  if (item.status === "running") {
    stageText = item.currentStage ? `${item.currentStage} …` : "starting …";
  } else if (item.status === "failed") {
    stageText = item.failedStage ? `${item.failedStage} ×` : "failed";
  } else if (item.status === "success") {
    stageText = "done ✓";
  } else if (item.status === "queued") {
    stageText = "";
  } else if (item.status === "skipped") {
    stageText = "skipped";
  } else if (item.status === "needs_human") {
    stageText = "needs-human";
  } else {
    stageText = item.currentStage || item.reason || "";
  }
  const fail = item.failureCode ? `   cause: ${item.failureCode}` : "";
  const pr = item.prUrl ? `   PR: ${item.prUrl}` : "";
  const last = item.lastMessage ? `   last: ${item.lastMessage}` : "";
  return `${issue} ${status}${stageText}${fail}${pr}${last}`;
}

export function formatIssueFixStatusBlock(items: IssueFixWatchItem[]): string {
  return items
    .slice()
    .sort((a, b) => a.issue - b.issue)
    .map(formatIssueFixStatusLine)
    .join("\n");
}

export function isIssueFixWatchDone(items: IssueFixWatchItem[]): boolean {
  return items.every((item) => ISSUE_FIX_TERMINAL_STATUSES.has(item.status));
}

export function issueStartIntervalSeconds(concurrency: number): number {
  if (concurrency <= 1) return 8;
  if (concurrency === 2) return 2;
  return 1.2;
}

export function parseIssueNumbers(value: string): number[] {
  return value
    .split(/[,\s，]+/)
    .map((v) => parseInt(v.trim(), 10))
    .filter((v, index, arr) => Number.isFinite(v) && v > 0 && arr.indexOf(v) === index);
}

export function parseIssueWatchArgs(
  args: string,
  resolvePipelineName: PipelineNameResolver,
): IssueWatchArgs {
  const parts = parseArgs(args);
  let repo = "openJiuwen/jiuwenswarm";
  let labels = ["auto-harness"];
  let max_issues = 1;
  let dry_run = false;
  let comment_on_start = false;
  let pipeline = "meta_evolve_pipeline";
  let issue_numbers: number[] = [];
  let max_auto_difficulty = "medium";
  let concurrency = 1;

  let i = 0;
  while (i < parts.length) {
    const part = parts[i];
    if (part === "--repo" && i + 1 < parts.length) {
      repo = parts[i + 1];
      i += 2;
    } else if ((part === "--label" || part === "--labels") && i + 1 < parts.length) {
      labels = parts[i + 1]
        .split(",")
        .map((v) => v.trim())
        .filter(Boolean);
      i += 2;
    } else if ((part === "--max" || part === "--max-issues") && i + 1 < parts.length) {
      const parsed = parseInt(parts[i + 1], 10);
      max_issues = Number.isFinite(parsed) ? parsed : 1;
      i += 2;
    } else if ((part === "--issue" || part === "--issues") && i + 1 < parts.length) {
      issue_numbers = parseIssueNumbers(parts[i + 1]);
      i += 2;
    } else if (part === "--pipeline" && i + 1 < parts.length) {
      pipeline = resolvePipelineName(parts[i + 1]);
      i += 2;
    } else if ((part === "--max-difficulty" || part === "--difficulty") && i + 1 < parts.length) {
      max_auto_difficulty = parts[i + 1].toLowerCase();
      i += 2;
    } else if (part === "--concurrency" && i + 1 < parts.length) {
      const parsed = parseInt(parts[i + 1], 10);
      concurrency = Number.isFinite(parsed) && parsed > 0 ? parsed : 1;
      i += 2;
    } else if (part === "--dry-run") {
      dry_run = true;
      i += 1;
    } else if (part === "--comment") {
      comment_on_start = true;
      i += 1;
    } else {
      i += 1;
    }
  }

  if (issue_numbers.length > 0) {
    max_issues = issue_numbers.length;
  }

  return {
    repo,
    labels,
    max_issues,
    dry_run,
    comment_on_start,
    pipeline,
    issue_numbers,
    max_auto_difficulty,
    concurrency,
  };
}

export function parseIssueFixArgs(
  args: string,
  resolvePipelineName: PipelineNameResolver,
): IssueFixArgs {
  const parts = parseArgs(args);
  let repo = "openJiuwen/jiuwenswarm";
  let dry_run = false;
  let comment_on_start = false;
  let pipeline = "meta_evolve_pipeline";
  let max_auto_difficulty = "medium";
  let concurrency = 1;
  const numberParts: string[] = [];

  let i = 0;
  while (i < parts.length) {
    const part = parts[i];
    if (part === "--repo" && i + 1 < parts.length) {
      repo = parts[i + 1];
      i += 2;
    } else if (part === "--pipeline" && i + 1 < parts.length) {
      pipeline = resolvePipelineName(parts[i + 1]);
      i += 2;
    } else if ((part === "--max-difficulty" || part === "--difficulty") && i + 1 < parts.length) {
      max_auto_difficulty = parts[i + 1].toLowerCase();
      i += 2;
    } else if (part === "--concurrency" && i + 1 < parts.length) {
      const parsed = parseInt(parts[i + 1], 10);
      concurrency = Number.isFinite(parsed) && parsed > 0 ? parsed : 1;
      i += 2;
    } else if (part === "--dry-run") {
      dry_run = true;
      i += 1;
    } else if (part === "--comment") {
      comment_on_start = true;
      i += 1;
    } else {
      numberParts.push(part);
      i += 1;
    }
  }

  return {
    repo,
    issue_numbers: parseIssueNumbers(numberParts.join(",")),
    dry_run,
    comment_on_start,
    pipeline,
    max_auto_difficulty,
    concurrency,
  };
}

export function formatIssueWatchResult(result: IssueWatchResult): string {
  const lines = ["\nGitCode issue 处理完成", "━━━━━━━━━━━━━━━━━━━━━━"];
  lines.push(`拉取数量: ${result.fetched ?? 0}`);
  lines.push(`新建任务: ${result.started?.length ?? 0}`);
  for (const item of result.started || []) {
    const difficulty = item.difficulty?.level ? ` difficulty=${item.difficulty.level}` : "";
    lines.push(
      `  #${item.number ?? item.issue}: ${item.task_id || item.status || "dry-run"}${difficulty}`,
    );
  }
  if (result.reconciled && result.reconciled.length > 0) {
    lines.push(`状态更新: ${result.reconciled.length}`);
    for (const item of result.reconciled) {
      lines.push(`  #${item.number}: ${item.status}${item.pr_url ? ` ${item.pr_url}` : ""}`);
    }
  }
  if (result.skipped && result.skipped.length > 0) {
    lines.push(`跳过: ${result.skipped.length}`);
    for (const item of result.skipped.slice(0, 10)) {
      const difficulty = item.difficulty?.level ? ` difficulty=${item.difficulty.level}` : "";
      const human = item.human_label ? ` label=${item.human_label}` : "";
      lines.push(
        `  #${item.issue}: ${item.reason || item.status || "skipped"}${difficulty}${human}`,
      );
    }
  }
  lines.push("━━━━━━━━━━━━━━━━━━━━━━");
  return lines.join("\n");
}
