export interface CodexAuthHandoffLifecycle {
  enabled?: boolean;
  available?: boolean;
  connected: boolean;
  state: string;
  operation_id?: string;
}

const ACTIVE_HANDOFF_STATES = new Set(["waiting_for_user", "reconciling"]);
const ACTIVE_OBSERVATION_STATES = new Set([
  "waiting_for_user",
  "reconciling",
  "canceling",
]);

export function shouldRetainCodexAuthHandoff(
  handoff: CodexAuthHandoffLifecycle | null,
  refreshedStatus: CodexAuthHandoffLifecycle,
): boolean {
  return Boolean(
    handoff?.operation_id &&
      refreshedStatus.enabled !== false &&
      refreshedStatus.available !== false &&
      !refreshedStatus.connected &&
      ACTIVE_HANDOFF_STATES.has(refreshedStatus.state) &&
      refreshedStatus.operation_id === handoff.operation_id,
  );
}

export function shouldObserveCodexAuth(
  hasCodexDraft: boolean,
  status: CodexAuthHandoffLifecycle | null,
  handoff: CodexAuthHandoffLifecycle | null,
): boolean {
  return Boolean(
    hasCodexDraft ||
      handoff?.operation_id ||
      (status && ACTIVE_OBSERVATION_STATES.has(status.state)),
  );
}
