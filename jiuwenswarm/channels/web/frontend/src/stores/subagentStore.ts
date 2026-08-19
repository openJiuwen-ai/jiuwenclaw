import { create } from 'zustand';
import type { Subagent, SubagentActivity, SubagentEvent, SubagentResult, SubagentUpdatedEvent } from '../types/subagent';

export interface SubagentRuntime {
  sessionId: string;
  revision: number;
  subagentsById: Record<string, Subagent>;
  activitiesBySubagentId: Record<string, Record<string, SubagentActivity>>;
  resultsBySubagentId: Record<string, SubagentResult>;
  cacheOnlySubagentIds: Record<string, true>;
  selectedSubagentId: string | null;
}

const PERSISTED_RUNTIME_PREFIX = 'jiuwen.subagent.runtime.v1:';
const MAX_PERSISTED_ACTIVITIES = 500;

interface PersistedSubagentRuntime {
  subagents: Subagent[];
  activities: SubagentActivity[];
  results: SubagentResult[];
  selectedSubagentId: string | null;
}

interface SubagentState {
  runtimes: Record<string, SubagentRuntime>;
  ensureRuntime: (sessionId: string) => SubagentRuntime;
  getRuntime: (sessionId: string | null | undefined) => SubagentRuntime | undefined;
  removeRuntime: (sessionId: string) => void;
  hydrateRuntime: (sessionId: string) => void;
  dropCachedSubagent: (sessionId: string, subagentId: string, revision: number, updatedAt: number) => void;
  applyEvent: (sessionId: string, event: SubagentEvent) => void;
  applyHistoryEvent: (sessionId: string, event: SubagentEvent) => void;
  markRunningSubagentsCancelled: (sessionId: string) => void;
  applyResult: (sessionId: string, result: SubagentResult) => void;
  applyTranscript: (sessionId: string, result: SubagentResult) => void;
  setSelectedSubagent: (sessionId: string, subagentId: string | null) => void;
}

export function createEmptySubagentRuntime(sessionId: string): SubagentRuntime {
  const runtime = {
    sessionId,
    revision: 0,
    subagentsById: {},
    activitiesBySubagentId: {},
    resultsBySubagentId: {},
    cacheOnlySubagentIds: {},
    selectedSubagentId: null,
  };
  return mergePersistedRuntime(runtime, readPersistedRuntime(sessionId));
}

function getStorage(): Storage | null {
  if (typeof window === 'undefined') return null;
  try {
    return window.sessionStorage;
  } catch {
    return null;
  }
}

function storageKey(sessionId: string): string {
  return `${PERSISTED_RUNTIME_PREFIX}${encodeURIComponent(sessionId)}`;
}

function readPersistedRuntime(sessionId: string): PersistedSubagentRuntime | null {
  const storage = getStorage();
  if (!storage) return null;
  try {
    const raw = JSON.parse(storage.getItem(storageKey(sessionId)) ?? 'null') as Partial<PersistedSubagentRuntime> | null;
    if (!raw || !Array.isArray(raw.subagents) || !Array.isArray(raw.activities) || !Array.isArray(raw.results)) {
      return null;
    }
    return {
      subagents: raw.subagents.filter((item): item is Subagent => Boolean(item && typeof item === 'object' && typeof item.subagent_id === 'string')),
      activities: raw.activities.filter((item): item is SubagentActivity => Boolean(item && typeof item === 'object' && typeof item.activity_id === 'string')),
      results: raw.results.filter((item): item is SubagentResult => Boolean(item && typeof item === 'object' && typeof item.subagent_id === 'string' && typeof item.content === 'string')),
      selectedSubagentId: typeof raw.selectedSubagentId === 'string' ? raw.selectedSubagentId : null,
    };
  } catch {
    return null;
  }
}

function persistRuntime(runtime: SubagentRuntime): void {
  const storage = getStorage();
  if (!storage) return;
  const activities = Object.values(runtime.activitiesBySubagentId)
    .flatMap((items) => Object.values(items))
    .sort(compareBySequence)
    .slice(-MAX_PERSISTED_ACTIVITIES);
  const payload: PersistedSubagentRuntime = {
    subagents: Object.values(runtime.subagentsById),
    activities,
    results: Object.values(runtime.resultsBySubagentId),
    selectedSubagentId: runtime.selectedSubagentId,
  };
  try {
    storage.setItem(storageKey(runtime.sessionId), JSON.stringify(payload));
  } catch {
    // Browser storage is best-effort; live state remains authoritative.
  }
}

function mergePersistedRuntime(runtime: SubagentRuntime, persisted: PersistedSubagentRuntime | null): SubagentRuntime {
  if (!persisted) return runtime;
  const subagentsById = { ...runtime.subagentsById };
  const cacheOnlySubagentIds = { ...runtime.cacheOnlySubagentIds };
  for (const subagent of persisted.subagents) {
    if (subagent.parent_session_id !== runtime.sessionId) continue;
    const current = subagentsById[subagent.subagent_id];
    if (current?.status === 'closed' && subagent.status !== 'closed') continue;
    if (!current || subagent.revision > current.revision) {
      subagentsById[subagent.subagent_id] = subagent;
      if (!current) cacheOnlySubagentIds[subagent.subagent_id] = true;
    }
  }
  const activitiesBySubagentId = { ...runtime.activitiesBySubagentId };
  for (const activity of persisted.activities) {
    if (activity.parent_session_id && activity.parent_session_id !== runtime.sessionId) continue;
    const scopedActivity = activity.parent_session_id
      ? activity
      : { ...activity, parent_session_id: runtime.sessionId };
    const existing = activitiesBySubagentId[scopedActivity.subagent_id] ?? {};
    activitiesBySubagentId[scopedActivity.subagent_id] = {
      ...existing,
      [scopedActivity.activity_id]: scopedActivity,
    };
  }
  const resultsBySubagentId = { ...runtime.resultsBySubagentId };
  for (const result of persisted.results) {
    if (result.parent_session_id && result.parent_session_id !== runtime.sessionId) continue;
    if (!resultsBySubagentId[result.subagent_id]) {
      resultsBySubagentId[result.subagent_id] = result.parent_session_id
        ? result
        : { ...result, parent_session_id: runtime.sessionId };
    }
  }
  return {
    ...runtime,
    revision: Math.max(runtime.revision, ...Object.values(subagentsById).map((item) => item.revision)),
    subagentsById,
    activitiesBySubagentId,
    resultsBySubagentId,
    cacheOnlySubagentIds,
    selectedSubagentId: runtime.selectedSubagentId ?? persisted.selectedSubagentId ?? chooseDefaultSubagentId(subagentsById, null),
  };
}

function compareBySequence(left: SubagentActivity, right: SubagentActivity): number {
  return left.sequence - right.sequence || left.at_ms - right.at_ms || left.activity_id.localeCompare(right.activity_id);
}

function compareByUpdatedAt(left: Subagent, right: Subagent): number {
  return right.updated_at - left.updated_at || left.display_name.localeCompare(right.display_name) || left.subagent_id.localeCompare(right.subagent_id);
}

function statusRank(status: Subagent['status']): number {
  if (status === 'running') return 0;
  if (status === 'idle') return 1;
  return 2;
}

function chooseDefaultSubagentId(subagentsById: Record<string, Subagent>, preferredId: string | null): string | null {
  if (preferredId && subagentsById[preferredId]) return preferredId;
  const subagents = Object.values(subagentsById);
  return subagents
    .filter(subagent => subagent.status === 'running')
    .sort(compareByUpdatedAt)[0]?.subagent_id
    ?? subagents.sort(compareByUpdatedAt)[0]?.subagent_id
    ?? null;
}

function replaceSubagent(runtime: SubagentRuntime, incoming: Subagent, allowSameRevisionNewerHistory = false): SubagentRuntime {
  const current = runtime.subagentsById[incoming.subagent_id];
  if (current) {
    if (current.status === 'closed' && incoming.status !== 'closed' && !allowSameRevisionNewerHistory) return runtime;
    const incomingIsOlder = incoming.revision < current.revision
      || (
        incoming.revision === current.revision
        && (!allowSameRevisionNewerHistory || incoming.updated_at <= current.updated_at)
      );
    if (incomingIsOlder) return runtime;
  }

  // Some recovery sources only carry the status transition. Keep the canonical
  // assignment metadata when a newer sparse snapshot omits it.
  const mergedIncoming = current
    ? {
      ...incoming,
      role: incoming.role.trim() ? incoming.role : current.role,
      task_description: incoming.task_description.trim() ? incoming.task_description : current.task_description,
    }
    : incoming;

  const subagentsById = {
    ...runtime.subagentsById,
    [incoming.subagent_id]: mergedIncoming,
  };
  return {
    ...runtime,
    revision: Math.max(runtime.revision, incoming.revision),
    subagentsById,
    cacheOnlySubagentIds: Object.fromEntries(
      Object.entries(runtime.cacheOnlySubagentIds).filter(([subagentId]) => subagentId !== incoming.subagent_id),
    ),
    selectedSubagentId: chooseDefaultSubagentId(subagentsById, runtime.selectedSubagentId),
  };
}

function replaceActivity(runtime: SubagentRuntime, incoming: SubagentActivity): SubagentRuntime {
  if (incoming.parent_session_id !== runtime.sessionId) return runtime;
  const existingById = runtime.activitiesBySubagentId[incoming.subagent_id] ?? {};
  if (existingById[incoming.activity_id]) return runtime;

  return {
    ...runtime,
    activitiesBySubagentId: {
      ...runtime.activitiesBySubagentId,
      [incoming.subagent_id]: {
        ...existingById,
        [incoming.activity_id]: incoming,
      },
    },
    cacheOnlySubagentIds: Object.fromEntries(
      Object.entries(runtime.cacheOnlySubagentIds).filter(([subagentId]) => subagentId !== incoming.subagent_id),
    ),
  };
}

function replaceResult(runtime: SubagentRuntime, incoming: SubagentResult): SubagentRuntime {
  const current = runtime.resultsBySubagentId[incoming.subagent_id];
  if (current && current.content === incoming.content && current.output_file === incoming.output_file && current.source === incoming.source) return runtime;
  return {
    ...runtime,
    resultsBySubagentId: {
      ...runtime.resultsBySubagentId,
      [incoming.subagent_id]: incoming,
    },
    cacheOnlySubagentIds: Object.fromEntries(
      Object.entries(runtime.cacheOnlySubagentIds).filter(([subagentId]) => subagentId !== incoming.subagent_id),
    ),
  };
}

export function applySubagentUpdated(runtime: SubagentRuntime, event: SubagentUpdatedEvent): SubagentRuntime {
  if (event.session_id !== runtime.sessionId || event.subagent.parent_session_id !== runtime.sessionId) return runtime;
  return replaceSubagent(runtime, event.subagent);
}

export function applySubagentHistoryUpdated(runtime: SubagentRuntime, event: SubagentUpdatedEvent): SubagentRuntime {
  if (event.session_id !== runtime.sessionId || event.subagent.parent_session_id !== runtime.sessionId) return runtime;
  return replaceSubagent(runtime, event.subagent, true);
}

export function applySubagentActivity(
  runtime: SubagentRuntime,
  event: Extract<SubagentEvent, { event_type: 'chat.subagent_activity' }>,
): SubagentRuntime {
  if (event.session_id !== runtime.sessionId) return runtime;
  const activity = event.activity.parent_session_id
    ? event.activity
    : { ...event.activity, parent_session_id: runtime.sessionId };
  return replaceActivity(runtime, activity);
}

export function applySubagentResult(runtime: SubagentRuntime, result: SubagentResult): SubagentRuntime {
  if (!result.subagent_id) return runtime;
  if (result.parent_session_id && result.parent_session_id !== runtime.sessionId) return runtime;
  return replaceResult(runtime, {
    ...result,
    parent_session_id: result.parent_session_id ?? runtime.sessionId,
    source: 'wait',
  });
}

export function dropCachedSubagent(
  runtime: SubagentRuntime,
  subagentId: string,
  expectedRevision: number,
  expectedUpdatedAt: number,
): SubagentRuntime {
  const current = runtime.subagentsById[subagentId];
  if (!runtime.cacheOnlySubagentIds[subagentId] || !current) return runtime;
  if (current.revision !== expectedRevision || current.updated_at !== expectedUpdatedAt) return runtime;

  const subagentsById = { ...runtime.subagentsById };
  const activitiesBySubagentId = { ...runtime.activitiesBySubagentId };
  const resultsBySubagentId = { ...runtime.resultsBySubagentId };
  const cacheOnlySubagentIds = { ...runtime.cacheOnlySubagentIds };
  delete subagentsById[subagentId];
  delete activitiesBySubagentId[subagentId];
  delete resultsBySubagentId[subagentId];
  delete cacheOnlySubagentIds[subagentId];

  return {
    ...runtime,
    subagentsById,
    activitiesBySubagentId,
    resultsBySubagentId,
    cacheOnlySubagentIds,
    selectedSubagentId: chooseDefaultSubagentId(subagentsById, runtime.selectedSubagentId),
  };
}

export function markRunningSubagentsCancelled(runtime: SubagentRuntime, updatedAt = Date.now()): SubagentRuntime {
  const running = Object.values(runtime.subagentsById).filter(subagent => subagent.status === 'running');
  if (running.length === 0) return runtime;

  const revision = runtime.revision + 1;
  const subagentsById = { ...runtime.subagentsById };
  for (const subagent of running) {
    subagentsById[subagent.subagent_id] = {
      ...subagent,
      status: 'idle',
      turn_outcome: 'cancelled',
      lifecycle: 'live',
      can_send_input: true,
      needs_resume: false,
      closed_at: null,
      closed_reason: null,
      error: null,
      updated_at: Math.max(updatedAt, subagent.updated_at + 1),
      revision: Math.max(revision, subagent.revision + 1),
    };
  }
  return {
    ...runtime,
    revision: Math.max(revision, ...running.map(subagent => subagent.revision + 1)),
    subagentsById,
    selectedSubagentId: chooseDefaultSubagentId(subagentsById, runtime.selectedSubagentId),
  };
}

export function applySubagentTranscript(runtime: SubagentRuntime, result: SubagentResult): SubagentRuntime {
  if (!result.subagent_id || !result.content.trim()) return runtime;
  if (result.parent_session_id && result.parent_session_id !== runtime.sessionId) return runtime;
  const current = runtime.resultsBySubagentId[result.subagent_id];
  if (current?.source === 'wait') return runtime;
  if (current?.source === 'transcript' && current.content.split('\n\n').includes(result.content)) return runtime;
  const content = current?.source === 'transcript' && current.content.trim()
    ? `${current.content}\n\n${result.content}`
    : result.content;
  return replaceResult(runtime, {
    ...result,
    parent_session_id: result.parent_session_id ?? runtime.sessionId,
    content,
    source: 'transcript',
  });
}

export function selectSubagents(runtime: SubagentRuntime | undefined): Subagent[] {
  if (!runtime) return [];
  return Object.values(runtime.subagentsById).sort((left, right) => {
    if (left.status !== right.status) return statusRank(left.status) - statusRank(right.status);
    return compareByUpdatedAt(left, right);
  });
}

export function selectSubagentActivities(runtime: SubagentRuntime | undefined, subagentId: string | null | undefined): SubagentActivity[] {
  if (!runtime || !subagentId) return [];
  return Object.values(runtime.activitiesBySubagentId[subagentId] ?? {}).sort(compareBySequence);
}

export function selectSubagentResult(runtime: SubagentRuntime | undefined, subagentId: string | null | undefined): SubagentResult | undefined {
  if (!runtime || !subagentId) return undefined;
  return runtime.resultsBySubagentId[subagentId];
}

export const useSubagentStore = create<SubagentState>((set, get) => ({
  runtimes: {},

  ensureRuntime: sessionId => {
    const existing = get().runtimes[sessionId];
    if (existing) return existing;
    const runtime = createEmptySubagentRuntime(sessionId);
    set(state => ({ runtimes: { ...state.runtimes, [sessionId]: runtime } }));
    return runtime;
  },

  getRuntime: sessionId => (sessionId ? get().runtimes[sessionId] : undefined),

  removeRuntime: sessionId => {
    set(state => {
      const runtimes = { ...state.runtimes };
      delete runtimes[sessionId];
      getStorage()?.removeItem(storageKey(sessionId));
      return { runtimes };
    });
  },

  hydrateRuntime: sessionId => {
    set(state => {
      const current = state.runtimes[sessionId] ?? createEmptySubagentRuntime(sessionId);
      const next = mergePersistedRuntime(current, readPersistedRuntime(sessionId));
      if (next === current) return state;
      return { runtimes: { ...state.runtimes, [sessionId]: next } };
    });
  },

  dropCachedSubagent: (sessionId, subagentId, revision, updatedAt) => {
    set(state => {
      const runtime = state.runtimes[sessionId];
      if (!runtime) return state;
      const next = dropCachedSubagent(runtime, subagentId, revision, updatedAt);
      if (next === runtime) return state;
      persistRuntime(next);
      return { runtimes: { ...state.runtimes, [sessionId]: next } };
    });
  },

  applyEvent: (sessionId, event) => {
    set(state => {
      const runtime = state.runtimes[sessionId] ?? createEmptySubagentRuntime(sessionId);
      const next = event.event_type === 'chat.subtask_update'
        ? applySubagentUpdated(runtime, event)
        : applySubagentActivity(runtime, event);
      if (next === runtime) return state;
      persistRuntime(next);
      return { runtimes: { ...state.runtimes, [sessionId]: next } };
    });
  },

  applyHistoryEvent: (sessionId, event) => {
    set(state => {
      const runtime = state.runtimes[sessionId] ?? createEmptySubagentRuntime(sessionId);
      const next = event.event_type === 'chat.subtask_update'
        ? applySubagentHistoryUpdated(runtime, event)
        : applySubagentActivity(runtime, event);
      if (next === runtime) return state;
      persistRuntime(next);
      return { runtimes: { ...state.runtimes, [sessionId]: next } };
    });
  },

  markRunningSubagentsCancelled: sessionId => {
    set(state => {
      const runtime = state.runtimes[sessionId];
      if (!runtime) return state;
      const next = markRunningSubagentsCancelled(runtime);
      if (next === runtime) return state;
      persistRuntime(next);
      return { runtimes: { ...state.runtimes, [sessionId]: next } };
    });
  },

  applyResult: (sessionId, result) => {
    set(state => {
      const runtime = state.runtimes[sessionId] ?? createEmptySubagentRuntime(sessionId);
      const next = applySubagentResult(runtime, result);
      if (next === runtime) return state;
      persistRuntime(next);
      return { runtimes: { ...state.runtimes, [sessionId]: next } };
    });
  },

  applyTranscript: (sessionId, result) => {
    set(state => {
      const runtime = state.runtimes[sessionId] ?? createEmptySubagentRuntime(sessionId);
      const next = applySubagentTranscript(runtime, result);
      if (next === runtime) return state;
      persistRuntime(next);
      return { runtimes: { ...state.runtimes, [sessionId]: next } };
    });
  },

  setSelectedSubagent: (sessionId, subagentId) => {
    set(state => {
      const runtime = state.runtimes[sessionId];
      if (!runtime || (subagentId !== null && !runtime.subagentsById[subagentId])) return state;
      const next = { ...runtime, selectedSubagentId: subagentId };
      persistRuntime(next);
      return { runtimes: { ...state.runtimes, [sessionId]: next } };
    });
  },
}));
