export type WorkflowStatus = "planned" | "pending" | "running" | "completed" | "failed" | "stopped";

export interface WorkflowAgentActivity {
  timestamp: string;
  type: "tool_call" | "tool_result";
  content: string;
}

export interface WorkflowAgent {
  id: string;
  name: string;
  status: WorkflowStatus;
  model?: string;
  prompt?: string;
  activity?: WorkflowAgentActivity[];
  outcome?: string;
  error?: string;
  started_at?: string;
  completed_at?: string;
  token_count?: number | null;
  duration_ms?: number | null;
}

export interface WorkflowPhase {
  id: string;
  name: string;
  description?: string;
  status: WorkflowStatus;
  agent_count?: number;
  completed_agent_count?: number;
  agents: WorkflowAgent[];
}

export interface WorkflowRun {
  id: string;
  name: string;
  summary: string;
  status: WorkflowStatus;
  agent_count?: number;
  completed_agent_count?: number;
  started_at?: string;
  completed_at?: string;
  script?: string;
  result?: string;
  error?: string;
  logs?: string[];
  token_count?: number | null;
  duration_ms?: number | null;
  estimated_token_count?: number | null;
  phases: WorkflowPhase[];
}

export interface WorkflowAgentLookup {
  workflow: WorkflowRun;
  phase: WorkflowPhase;
  agent: WorkflowAgent;
}

export function workflowStatusIcon(status: WorkflowStatus): string {
  switch (status) {
    case "planned":
      return "◇";
    case "completed":
      return "✓";
    case "failed":
      return "×";
    case "running":
      return "◐";
    case "pending":
      return "○";
    case "stopped":
      return "■";
  }
}

/** Fixed user-facing status lines — avoid showing raw engine narration (e.g. result payload). */
export const WORKFLOW_STATUS_BANNER: Partial<Record<WorkflowStatus, string>> = {
  running: "Workflow running",
  completed: "Workflow completed",
};

export function runningWorkflowsBannerText(count: number): string {
  if (count <= 0) return "";
  return count === 1 ? "1 workflow running" : `${count} workflows running`;
}

/** Format an ISO timestamp for workflow started-at display (local time). */
export function formatWorkflowLocalTime(iso?: string): string {
  if (!iso) return "—";
  const ms = Date.parse(iso);
  if (!Number.isFinite(ms)) return "—";
  const date = new Date(ms);
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  const hours = String(date.getHours()).padStart(2, "0");
  const minutes = String(date.getMinutes()).padStart(2, "0");
  const seconds = String(date.getSeconds()).padStart(2, "0");
  return `${year}-${month}-${day} ${hours}:${minutes}:${seconds}`;
}

export function formatWorkflowStartedText(workflow: WorkflowRun): string {
  return `started ${formatWorkflowLocalTime(workflow.started_at)}`;
}

function formatDurationMs(durationMs: number): string {
  if (durationMs < 1000) {
    return `${Math.round(durationMs)}ms`;
  }
  const totalSeconds = Math.floor(durationMs / 1000);
  if (totalSeconds >= 60) {
    const minutes = Math.floor(totalSeconds / 60);
    const seconds = totalSeconds % 60;
    return `${minutes}m ${seconds}s`;
  }
  return `${(durationMs / 1000).toFixed(1)}s`;
}

/** Elapsed or total runtime — never a completed-at timestamp. */
export function formatWorkflowRunningTime(workflow: WorkflowRun, now = Date.now()): string {
  if (
    typeof workflow.duration_ms === "number" &&
    Number.isFinite(workflow.duration_ms) &&
    workflow.duration_ms >= 0 &&
    workflow.status !== "running" &&
    workflow.status !== "pending" &&
    workflow.status !== "planned"
  ) {
    return formatDurationMs(workflow.duration_ms);
  }
  const startedMs = Date.parse(workflow.started_at ?? "");
  if (!Number.isFinite(startedMs)) return "—";
  if (workflow.completed_at && workflow.status !== "running") {
    const completedMs = Date.parse(workflow.completed_at);
    if (Number.isFinite(completedMs)) {
      return formatDurationMs(Math.max(0, completedMs - startedMs));
    }
  }
  return formatDurationMs(Math.max(0, now - startedMs));
}

function formatWorkflowDurationLabel(status: WorkflowStatus): string {
  switch (status) {
    case "completed":
      return "completed";
    case "failed":
      return "failed";
    case "stopped":
      return "stopped";
    case "running":
    case "pending":
    case "planned":
    default:
      return "running";
  }
}

export function formatWorkflowRunningText(workflow: WorkflowRun, now = Date.now()): string {
  return `${formatWorkflowDurationLabel(workflow.status)} ${formatWorkflowRunningTime(workflow, now)}`;
}

export function formatWorkflowTimingText(workflow: WorkflowRun, now = Date.now()): string {
  return `${formatWorkflowStartedText(workflow)} · ${formatWorkflowRunningText(workflow, now)}`;
}

export function workflowStatusBannerText(status: WorkflowStatus): string | null {
  return WORKFLOW_STATUS_BANNER[status] ?? null;
}

export function countWorkflowAgents(workflow: WorkflowRun): number {
  return (workflow.phases ?? []).reduce((total, phase) => total + (phase.agents ?? []).length, 0);
}

export function countCompletedWorkflowAgents(workflow: WorkflowRun): number {
  return (workflow.phases ?? []).reduce(
    (total, phase) =>
      total + (phase.agents ?? []).filter((agent) => agent.status === "completed").length,
    0,
  );
}

export function findWorkflowAgent(
  workflows: WorkflowRun[],
  workflowId: string,
  agentId: string,
): WorkflowAgentLookup | null {
  const workflow = workflows.find((item) => item.id === workflowId);
  if (!workflow) return null;
  for (const phase of workflow.phases ?? []) {
    const agent = (phase.agents ?? []).find((item) => item.id === agentId);
    if (agent) return { workflow, phase, agent };
  }
  return null;
}

export function normalizeWorkflowRun(workflow: WorkflowRun): WorkflowRun {
  return {
    ...workflow,
    logs: Array.isArray(workflow.logs)
      ? workflow.logs.filter((log): log is string => typeof log === "string")
      : undefined,
    phases: Array.isArray(workflow.phases)
      ? workflow.phases.map((phase) => ({
          ...phase,
          agents: Array.isArray(phase.agents)
            ? phase.agents.map((agent) => ({
                ...agent,
                activity: Array.isArray(agent.activity)
                  ? agent.activity.filter(
                      (activity): activity is WorkflowAgentActivity =>
                        Boolean(
                          activity && typeof activity === "object" && !Array.isArray(activity),
                        ),
                    )
                  : undefined,
              }))
            : [],
        }))
      : [],
  };
}

function mergeWorkflowAgent(
  existing: WorkflowAgent | undefined,
  incoming: WorkflowAgent,
): WorkflowAgent {
  return {
    ...existing,
    ...incoming,
    activity: incoming.activity ?? existing?.activity,
  };
}

function mergeWorkflowPhase(
  existing: WorkflowPhase | undefined,
  incoming: WorkflowPhase,
): WorkflowPhase {
  const existingAgents = existing?.agents ?? [];
  const mergedAgents = [...existingAgents];

  for (const incomingAgent of incoming.agents ?? []) {
    const index = mergedAgents.findIndex((agent) => agent.id === incomingAgent.id);
    const nextAgent = mergeWorkflowAgent(
      index === -1 ? undefined : mergedAgents[index],
      incomingAgent,
    );
    if (index === -1) {
      mergedAgents.push(nextAgent);
    } else {
      mergedAgents[index] = nextAgent;
    }
  }

  return {
    ...existing,
    ...incoming,
    agents: mergedAgents,
  };
}

export function mergeWorkflowRun(
  existing: WorkflowRun | undefined,
  incoming: WorkflowRun,
): WorkflowRun {
  const existingPhases = existing?.phases ?? [];
  const mergedPhases = [...existingPhases];
  const incomingHasPhases = Object.prototype.hasOwnProperty.call(incoming, "phases");
  const incomingLogs = Array.isArray(incoming.logs)
    ? incoming.logs.filter((log): log is string => typeof log === "string")
    : undefined;

  const incomingPhases = Array.isArray(incoming.phases) ? incoming.phases : [];
  for (const incomingPhase of incomingPhases) {
    const index = mergedPhases.findIndex((phase) => phase.id === incomingPhase.id);
    const nextPhase = mergeWorkflowPhase(
      index === -1 ? undefined : mergedPhases[index],
      incomingPhase,
    );
    if (index === -1) {
      mergedPhases.push(nextPhase);
    } else {
      mergedPhases[index] = nextPhase;
    }
  }

  const merged: WorkflowRun = {
    ...existing,
    ...incoming,
    phases: mergedPhases,
  };
  if (incomingLogs) {
    merged.logs =
      existing && !incomingHasPhases ? [...(existing.logs ?? []), ...incomingLogs] : incomingLogs;
  } else if (existing?.logs && !Object.prototype.hasOwnProperty.call(incoming, "logs")) {
    merged.logs = existing.logs;
  }
  return merged;
}

export function applyWorkflowUpdate(
  workflows: WorkflowRun[],
  incoming: WorkflowRun,
): WorkflowRun[] {
  const index = workflows.findIndex((workflow) => workflow.id === incoming.id);
  if (index === -1) {
    return [normalizeWorkflowRun(incoming), ...workflows];
  }
  return workflows.map((workflow, itemIndex) =>
    itemIndex === index ? normalizeWorkflowRun(mergeWorkflowRun(workflow, incoming)) : workflow,
  );
}
