export type ClientMode =
  // 旧串（兼容旧后端推送 / 旧 snapshot）
  | "agent.plan"
  | "agent.fast"
  | "code.plan"
  | "code.normal"
  | "code.team"
  | "team"
  | "team.plan"
  | "team.plan.normal"
  | "team.plan.code"
  // P5.1：新三段 canonical（后端经 P3 resolve_channel_mode 产出）
  | "agent.work.normal"
  | "agent.work.plan"
  | "agent.code.normal"
  | "agent.code.plan"
  | "team.work.normal"
  | "team.work.plan"
  | "team.code.normal"
  | "team.code.plan";

const CLIENT_MODE_VALUES: ReadonlySet<string> = new Set<string>([
  "agent.plan",
  "agent.fast",
  "code.plan",
  "code.normal",
  "code.team",
  "team",
  "team.plan",
  "team.plan.normal",
  "team.plan.code",
  "agent.work.normal",
  "agent.work.plan",
  "agent.code.normal",
  "agent.code.plan",
  "team.work.normal",
  "team.work.plan",
  "team.code.normal",
  "team.code.plan",
]);

export function isClientMode(value: string): value is ClientMode {
  return CLIENT_MODE_VALUES.has(value);
}

const TEAM_MODE_VALUES: ReadonlySet<string> = new Set<string>([
  // 旧 team 串
  "team",
  "team.plan",
  "team.plan.normal",
  "team.plan.code",
  "code.team",
  // 新 team 串
  "team.work.normal",
  "team.work.plan",
  "team.code.normal",
  "team.code.plan",
]);

export function isTeamMode(mode: ClientMode): boolean {
  return TEAM_MODE_VALUES.has(mode);
}

/** Keep runtime identifiers canonical while presenting the public TUI hierarchy. */
export function formatModeForDisplay(mode: string): string {
  return mode === "code.team" ? "team.code" : mode;
}
