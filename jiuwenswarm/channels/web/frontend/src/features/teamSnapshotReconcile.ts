/**
 * Team snapshot reconciliation — self-healing for the swarm sidebar.
 *
 * Live `team.member` / `team.task` events can be lost when the backend
 * monitor mounts late or its event stream stalls, leaving the sidebar blank
 * while the chat keeps streaming. The `team.snapshot` RPC has a DB-direct
 * fallback on the server, so it returns the authoritative board even when
 * the monitor is down. While a team round is processing we periodically
 * merge that snapshot into the sidebar stores (upsert-only, never delete),
 * and once more when the round completes.
 */
import { webClient } from '../services/webClient';
import { useChatStore } from '../stores/chatStore';
import { useSessionStore } from '../stores/sessionStore';
import { snapshotItemToTask } from './teamHistoryPanelRestore';

const RECONCILE_INTERVAL_MS = 30_000;
// After a page refresh mid-round the new page never receives the
// chat.processing_status(true) frame, so isProcessing stays false even
// though the round is running. Team events keep the timer alive for this
// window past the last observed activity instead.
const ACTIVITY_KEEPALIVE_MS = 90_000;
// Mirror shouldKeepMember in teamHistoryPanelRestore: never surface the
// virtual user or the leader as teammates on the panel.
const EXCLUDED_MEMBER_IDS = new Set(['user', 'team_leader']);

interface TeamSnapshotResponse {
  members?: unknown;
  tasks?: unknown;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
}

function pickNonEmptyString(value: unknown): string | undefined {
  return typeof value === 'string' && value.trim() ? value : undefined;
}

const reconcileTimers = new Map<string, number>();
const inflightSessions = new Set<string>();
const lastTeamActivityAt = new Map<string, number>();

/**
 * Record team-round activity for a session and make sure the periodic
 * reconcile is running. Called from the team.* event handlers so the timer
 * also starts when chat.processing_status(true) was never seen (e.g. the
 * page was refreshed mid-round). Idempotent and cheap.
 */
export function noteTeamActivity(sessionId: string): void {
  if (!sessionId) {
    return;
  }
  lastTeamActivityAt.set(sessionId, Date.now());
  if (useSessionStore.getState().getRuntime(sessionId)?.mode === 'team') {
    startTeamSnapshotReconcile(sessionId);
  }
}

/**
 * Fetch the authoritative board once and merge it into the sidebar stores.
 * Merge-only: existing members/tasks are updated, unknown ones are added,
 * nothing is removed — deletion stays the responsibility of live events and
 * the history-restore path.
 */
export async function reconcileTeamPanelFromSnapshot(sessionId: string): Promise<void> {
  if (!sessionId || inflightSessions.has(sessionId)) {
    return;
  }
  inflightSessions.add(sessionId);
  try {
    const response = await webClient.request<TeamSnapshotResponse>(
      'team.snapshot',
      { session_id: sessionId },
      { timeoutMs: 5000 }
    );
    const store = useSessionStore.getState();
    if (!store.runtimes[sessionId]) {
      return;
    }

    const members = Array.isArray(response?.members) ? response.members : [];
    for (const raw of members) {
      if (!isRecord(raw)) {
        continue;
      }
      const memberId = pickNonEmptyString(raw.member_id);
      const status = pickNonEmptyString(raw.status) ?? '';
      if (!memberId || EXCLUDED_MEMBER_IDS.has(memberId) || status === 'shut_down') {
        continue;
      }
      store.addTeamMember(sessionId, {
        id: `snap-member-${memberId}`,
        member_id: memberId,
        status,
        timestamp: Date.now(),
        ...(pickNonEmptyString(raw.name) ? { name: pickNonEmptyString(raw.name) } : {}),
        ...(pickNonEmptyString(raw.execution_status)
          ? { execution_status: pickNonEmptyString(raw.execution_status) }
          : {}),
        ...(pickNonEmptyString(raw.mode) ? { mode: pickNonEmptyString(raw.mode) } : {}),
      });
    }

    const tasks = Array.isArray(response?.tasks) ? response.tasks : [];
    const now = Date.now();
    const existingTasks = useSessionStore.getState().runtimes[sessionId]?.teamTasks ?? [];
    const completedTaskIds = new Set(
      existingTasks.filter((task) => task.status === 'completed').map((task) => task.task_id)
    );
    for (const raw of tasks) {
      if (!isRecord(raw)) {
        continue;
      }
      const task = snapshotItemToTask(raw, now);
      if (!task) {
        continue;
      }
      // Completed is sticky: a snapshot response races live events, so a
      // slightly stale board must not resurrect a finished card.
      if (completedTaskIds.has(task.task_id) && task.status !== 'completed') {
        task.status = 'completed';
      }
      useSessionStore.getState().upsertTeamTask(sessionId, task);
    }
  } catch {
    // Best effort — live events remain the primary channel; the next tick
    // (or the round-end reconcile) will retry.
  } finally {
    inflightSessions.delete(sessionId);
  }
}

/** Start the in-round periodic reconcile for a team session (idempotent). */
export function startTeamSnapshotReconcile(sessionId: string): void {
  if (!sessionId || reconcileTimers.has(sessionId)) {
    return;
  }
  const timer = window.setInterval(() => {
    // Self-stop guard for a lost processing_status(false) frame or a mode
    // switch — never poll forever. Recent team events keep the timer alive
    // even when isProcessing is false (page refreshed mid-round).
    const isProcessing = useChatStore.getState().getRuntime(sessionId)?.isProcessing ?? false;
    const mode = useSessionStore.getState().getRuntime(sessionId)?.mode;
    const lastActivity = lastTeamActivityAt.get(sessionId) ?? 0;
    const recentlyActive = Date.now() - lastActivity <= ACTIVITY_KEEPALIVE_MS;
    if (mode !== 'team' || (!isProcessing && !recentlyActive)) {
      stopTeamSnapshotReconcile(sessionId);
      return;
    }
    void reconcileTeamPanelFromSnapshot(sessionId);
  }, RECONCILE_INTERVAL_MS);
  reconcileTimers.set(sessionId, timer);
}

/** Stop the periodic reconcile; optionally run one last catch-up pass. */
export function stopTeamSnapshotReconcile(
  sessionId: string,
  options?: { finalReconcile?: boolean }
): void {
  const timer = reconcileTimers.get(sessionId);
  if (timer !== undefined) {
    window.clearInterval(timer);
    reconcileTimers.delete(sessionId);
  }
  if (options?.finalReconcile) {
    void reconcileTeamPanelFromSnapshot(sessionId);
  }
}
