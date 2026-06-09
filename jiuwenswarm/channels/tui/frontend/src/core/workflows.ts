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

export function countWorkflowAgents(workflow: WorkflowRun): number {
  return workflow.phases.reduce((total, phase) => total + phase.agents.length, 0);
}

export function countCompletedWorkflowAgents(workflow: WorkflowRun): number {
  return workflow.phases.reduce(
    (total, phase) => total + phase.agents.filter((agent) => agent.status === "completed").length,
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
  for (const phase of workflow.phases) {
    const agent = phase.agents.find((item) => item.id === agentId);
    if (agent) return { workflow, phase, agent };
  }
  return null;
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

  for (const incomingPhase of incoming.phases ?? []) {
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
    return [incoming, ...workflows];
  }
  return workflows.map((workflow, itemIndex) =>
    itemIndex === index ? mergeWorkflowRun(workflow, incoming) : workflow,
  );
}
