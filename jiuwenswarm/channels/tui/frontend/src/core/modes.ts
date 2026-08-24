export type ClientMode =
  | "agent.plan"
  | "agent.fast"
  | "auto"
  | "code.plan"
  | "code.normal"
  | "code.team"
  | "team"
  | "team.plan"
  | "team.plan.normal"
  | "team.plan.code";

/** Concrete MACRO lane after Auto classifies (Web Agent vs Cluster). */
export type MacroLaneMode = "agent" | "team";

export function isClientMode(value: string): value is ClientMode {
  return (
    value === "agent.plan" ||
    value === "agent.fast" ||
    value === "auto" ||
    value === "code.plan" ||
    value === "code.normal" ||
    value === "code.team" ||
    value === "team" ||
    value === "team.plan" ||
    value === "team.plan.normal" ||
    value === "team.plan.code"
  );
}

export function isTeamMode(mode: ClientMode): boolean {
  return (
    mode === "team" ||
    mode === "team.plan" ||
    mode === "team.plan.normal" ||
    mode === "team.plan.code" ||
    mode === "code.team"
  );
}

/** Team stream UX while Auto stays selected (do not rewrite local mode). */
export function isEffectiveTeamMode(
  mode: ClientMode,
  lastMacroRoutedMode?: MacroLaneMode | null,
): boolean {
  if (mode === "auto") {
    return lastMacroRoutedMode === "team";
  }
  return isTeamMode(mode);
}

export function normalizeMacroLaneMode(raw: unknown): MacroLaneMode | null {
  if (typeof raw !== "string") return null;
  const normalized = raw.trim().toLowerCase();
  if (normalized === "team" || normalized === "cluster" || normalized === "agent.team") {
    return "team";
  }
  if (
    normalized === "agent" ||
    normalized === "agent.fast" ||
    normalized === "fast" ||
    normalized === "performance" ||
    normalized === "agent.plan" ||
    normalized === "plan" ||
    normalized === "planning"
  ) {
    return "agent";
  }
  return null;
}

/** Keep runtime identifiers canonical while presenting the public TUI hierarchy. */
export function formatModeForDisplay(mode: string): string {
  return mode === "code.team" ? "team.code" : mode;
}
