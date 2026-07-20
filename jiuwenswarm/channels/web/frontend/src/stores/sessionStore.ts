/**
 * 会话状态管理（多 session 版本）
 *
 * 全局字段保持不变，session 级字段按 session 隔离存储在 runtimes 中。
 */

import { create } from 'zustand';
import {
  Session,
  AgentMode,
  WebConnectionState,
  ModelEntry,
  Message,
  ContextCompressionRuntime,
  ContextCompressionSummary,
  TeamMemberContextCompressionState,
} from '../types';

const MODE_STORAGE_KEY = 'jiuwenclaw_mode';
const MODEL_STORAGE_KEY = 'jiuwenclaw_selected_model';

function loadModeFromStorage(): AgentMode {
  if (typeof localStorage === 'undefined') return DEFAULT_MODE;
  try {
    const stored = localStorage.getItem(MODE_STORAGE_KEY);
    if (stored) {
      return normalizeAgentMode(stored);
    }
  } catch (error) {
    console.error('Error loading mode from storage:', error);
  }
  return DEFAULT_MODE;
}

function saveModeToStorage(mode: AgentMode) {
  if (typeof localStorage === 'undefined') return;
  try {
    localStorage.setItem(MODE_STORAGE_KEY, mode);
  } catch (error) {
    console.error('Error saving mode to storage:', error);
  }
}

const DEFAULT_MODE: AgentMode = 'agent';

function normalizeAgentMode(mode: unknown): AgentMode {
  if (typeof mode !== 'string') return DEFAULT_MODE;
  const normalized = mode.trim().toLowerCase();
  if (normalized === 'team') return 'team';
  if (normalized === 'auto_harness') return 'auto_harness';
  // plan / fast 已合并为单一 agent（历史 agent.plan / agent.fast 归一）。
  return 'agent';
}

function normalizeSession(session: Session): Session {
  return {
    ...session,
    mode: normalizeAgentMode(session.mode),
  };
}

const FINAL_EVENT_DUPLICATE_WINDOW_MS = 60_000;

function normalizeExecutionContent(content?: string): string {
  return (content || '').replace(/\s+/g, ' ').trim();
}

function isDuplicateFinalExecutionEvent(
  existing: TeamMemberExecutionEvent,
  next: TeamMemberExecutionEvent
): boolean {
  if (existing.kind !== 'final' || next.kind !== 'final') {
    return false;
  }
  if (existing.member_id !== next.member_id) {
    return false;
  }
  if (!normalizeExecutionContent(existing.content)) {
    return false;
  }
  if (normalizeExecutionContent(existing.content) !== normalizeExecutionContent(next.content)) {
    return false;
  }
  return Math.abs((existing.timestamp || 0) - (next.timestamp || 0)) <= FINAL_EVENT_DUPLICATE_WINDOW_MS;
}

function dedupeTeamMemberExecutionEvents(
  events: TeamMemberExecutionEvent[]
): TeamMemberExecutionEvent[] {
  const deduped: TeamMemberExecutionEvent[] = [];
  for (const event of events) {
    const duplicateIndex = deduped.findIndex((item) => isDuplicateFinalExecutionEvent(item, event));
    if (duplicateIndex >= 0) {
      deduped[duplicateIndex] = {
        ...deduped[duplicateIndex],
        ...event,
        id: deduped[duplicateIndex].id,
        timestamp: Math.min(deduped[duplicateIndex].timestamp || event.timestamp, event.timestamp),
      };
      continue;
    }
    deduped.push(event);
  }
  return deduped;
}

interface ConnectionStats {
  state: WebConnectionState;
  inflight: number;
  lastError: string | null;
}

type HeartbeatState = 'unknown' | 'ok' | 'alert';

interface HeartbeatHistoryItem {
  message: string;
  updatedAt: string;
  status: HeartbeatState;
}

interface MemoryUsage {
  rssMb: number | null;
  usedPercent: number | null;
}

interface ContextCompressionStats {
  rate: number;
  beforeCompressed: number | null;
  afterCompressed: number | null;
}

export interface TeamTaskEvent {
  id: string;
  type: string;
  team_id: string;
  task_id: string;
  status: string;
  timestamp: number;
  member_id?: string;
  assignee?: string;
  team_name?: string;
  title?: string;
  content?: string;
  updated_at?: number | string | null;
}

export type TeamTaskStatus =
  | 'pending'
  | 'blocked'
  | 'planning'
  | 'in_progress'
  | 'in_review'
  | 'completed'
  | 'cancelled';

export interface TeamTask {
  task_id: string;
  title?: string;
  content?: string;
  status: TeamTaskStatus;
  assignee?: string;
  team_id?: string;
  timestamp?: number;
  skills?: string[];
  files?: string[];
}

// Upsert input: a task event may omit status (e.g. a content-only update).
// The store then preserves the task's existing status instead of resetting it.
export type TeamTaskUpsert = Omit<TeamTask, 'status'> & { status?: TeamTaskStatus };

interface TeamMember {
  id: string;
  member_id: string;
  status: string;
  timestamp: number;
  name?: string;
  execution_status?: string | null;
  mode?: string;
}

export type HumanShareStatus = 'pending' | 'joined' | 'left';

export interface HumanShareCommand {
  memberName: string;
  displayName?: string;
  sessionId: string;
  teamName: string;
  sessionRef: string;
  joinCommand: string;
  exitCommand: string;
  status: HumanShareStatus;
  sourceChannel?: string;
  userId?: string;
  updatedAt: number;
}

export type TeamMemberExecutionEventKind =
  | 'final'
  | 'tool_call'
  | 'tool_result'
  | 'file';

export interface TeamMemberExecutionEvent {
  id: string;
  member_id: string;
  kind: TeamMemberExecutionEventKind;
  timestamp: number;
  title: string;
  content?: string;
  tool_name?: string;
  tool_call_id?: string;
  files?: Array<{
    name: string;
    size?: number;
    mime_type?: string;
    download_url?: string;
  }>;
}

/**
 * 单个 session 的运行态。
 * 原 B 类全局字段全部迁移到这里，按 session 隔离。
 */
export interface SessionRuntime {
  mode: AgentMode;
  selectedModelName: string | null;
  projectDirectory: string | null;
  contextCompressionRate: number;
  contextCompressionBefore: number | null;
  contextCompressionAfter: number | null;
  teamTaskEvents: TeamTaskEvent[];
  teamTasks: TeamTask[];
  teamMembers: TeamMember[];
  teamLeaderMemberIds: string[];
  teamHumanShareCommands: HumanShareCommand[];
  teamMemberExecutionEvents: TeamMemberExecutionEvent[];
  teamMemberContextCompression: Record<string, TeamMemberContextCompressionState>;
  teamHistoryMessages: Message[];
  /** 当前会话输入栏已选中的技能名（用于随消息发送） */
  selectedSkills: string[];
}

function createEmptyRuntime(): SessionRuntime {
  return {
    mode: loadModeFromStorage(),
    selectedModelName: (() => {
      if (typeof localStorage === 'undefined') return null;
      try { return localStorage.getItem(MODEL_STORAGE_KEY); } catch { return null; }
    })(),
    projectDirectory: null,
    contextCompressionRate: 0,
    contextCompressionBefore: null,
    contextCompressionAfter: null,
    teamTaskEvents: [],
    teamTasks: [],
    teamMembers: [],
    teamLeaderMemberIds: [],
    teamHumanShareCommands: [],
    teamMemberExecutionEvents: [],
    teamMemberContextCompression: {},
    teamHistoryMessages: [],
    selectedSkills: [],
  };
}

interface SessionState {
  // A 类全局字段
  currentSession: Session | null;
  sessions: Session[];
  isConnected: boolean;
  availableTools: string[];
  connectionStats: ConnectionStats;
  memoryUsage: MemoryUsage;
  heartbeatState: HeartbeatState;
  heartbeatMessage: string | null;
  heartbeatUpdatedAt: string | null;
  heartbeatHistory: HeartbeatHistoryItem[];
  availableModels: ModelEntry[];
  /** 过滤 is_default=true 的模型，供聊天窗口 ModelSelector 使用 */
  chatAvailableModels: ModelEntry[];

  // B 类 session 级字段
  runtimes: Record<string, SessionRuntime>;

  // Runtime 管理方法
  ensureRuntime: (sessionId: string) => SessionRuntime;
  getRuntime: (sessionId: string | null) => SessionRuntime | undefined;
  removeRuntime: (sessionId: string) => void;

  // A 类 actions（不加 sessionId）
  setCurrentSession: (session: Session | null) => void;
  setSessions: (sessions: Session[]) => void;
  addSession: (session: Session) => void;
  updateSession: (sessionId: string, updates: Partial<Session>) => void;
  removeSession: (sessionId: string) => void;
  setConnected: (connected: boolean) => void;
  setAvailableTools: (tools: string[]) => void;
  setConnectionStats: (stats: Partial<ConnectionStats>) => void;
  setContextCompressionStats: (sessionId: string, stats: Partial<ContextCompressionStats> | null) => void;
  setMemoryUsage: (memoryUsage: Partial<MemoryUsage> | null) => void;
  setHeartbeatStatus: (
    status: HeartbeatState,
    message?: string | null,
    updatedAt?: string | null
  ) => void;
  setAvailableModels: (models: ModelEntry[], activeModel?: string) => void;
  setSelectedModelName: (sessionId: string, name: string) => void;

  // B 类 actions（加 sessionId）
  setMode: (sessionId: string, mode: AgentMode) => void;
  setProjectDirectory: (sessionId: string, directory: string | null) => void;
  setTeamTaskEvents: (sessionId: string, events: TeamTaskEvent[]) => void;
  addTeamTaskEvent: (sessionId: string, event: TeamTaskEvent) => void;
  setTeamTasks: (sessionId: string, tasks: TeamTask[]) => void;
  upsertTeamTask: (sessionId: string, task: TeamTaskUpsert) => void;
  updateTeamTask: (sessionId: string, taskId: string, patch: Partial<TeamTask>) => void;
  setTeamMembers: (sessionId: string, members: TeamMember[]) => void;
  setTeamLeaderMemberIds: (sessionId: string, memberIds: string[]) => void;
  addTeamLeaderMemberId: (sessionId: string, memberId: string) => void;
  /** 输入栏已选技能：追加（去重） */
  addSelectedSkill: (sessionId: string, skill: string) => void;
  /** 输入栏已选技能：移除指定项 */
  removeSelectedSkill: (sessionId: string, skill: string) => void;
  /** 输入栏已选技能：清空 */
  clearSelectedSkills: (sessionId: string) => void;
  addTeamMember: (sessionId: string, member: TeamMember) => void;
  updateTeamMemberStatus: (sessionId: string, memberId: string, newStatus: string, timestamp?: number) => void;
  setTeamHumanShareCommands: (sessionId: string, commands: HumanShareCommand[]) => void;
  upsertTeamHumanShareCommand: (sessionId: string, command: HumanShareCommand) => void;
  updateTeamHumanShareStatus: (
    sessionId: string,
    memberName: string,
    status: HumanShareStatus,
    patch?: Partial<HumanShareCommand>
  ) => void;
  setTeamMemberExecutionEvents: (sessionId: string, events: TeamMemberExecutionEvent[]) => void;
  addTeamMemberExecutionEvent: (sessionId: string, event: TeamMemberExecutionEvent) => void;
  setTeamMemberContextCompressionStatus: (
    sessionId: string,
    memberId: string,
    runtime?: ContextCompressionRuntime,
    summary?: ContextCompressionSummary
  ) => void;
  clearTeamMemberContextCompressionStatus: (sessionId: string, memberId: string) => void;
  clearAllTeamMemberContextCompressionStatus: (sessionId: string) => void;
  setTeamHistoryMessages: (sessionId: string, messages: Message[]) => void;
}

export const useSessionStore = create<SessionState>((set, get) => ({
  currentSession: null,
  sessions: [],
  isConnected: false,
  availableTools: [],
  connectionStats: {
    state: 'idle',
    inflight: 0,
    lastError: null,
  },
  memoryUsage: {
    rssMb: null,
    usedPercent: null,
  },
  heartbeatState: 'unknown',
  heartbeatMessage: null,
  heartbeatUpdatedAt: null,
  heartbeatHistory: [],
  availableModels: [],
  chatAvailableModels: [],
  runtimes: {},

  ensureRuntime: (sessionId) => {
    const existing = get().runtimes[sessionId];
    if (existing) return existing;
    const runtime = createEmptyRuntime();
    set((state) => ({
      runtimes: { ...state.runtimes, [sessionId]: runtime },
    }));
    return runtime;
  },

  getRuntime: (sessionId) => {
    if (!sessionId) return undefined;
    return get().runtimes[sessionId];
  },

  removeRuntime: (sessionId) => {
    set((state) => {
      const next = { ...state.runtimes };
      delete next[sessionId];
      return { runtimes: next };
    });
  },

  setCurrentSession: (session) => {
    const normalizedSession = session ? normalizeSession(session) : null;
    set((state) => {
      if (!normalizedSession) {
        return { currentSession: null };
      }
      const sessionId = normalizedSession.session_id;
      const existingRuntime = state.runtimes[sessionId];
      const baseRuntime = existingRuntime || createEmptyRuntime();
      const nextRuntime: SessionRuntime = {
        ...baseRuntime,
        mode: normalizedSession.mode || baseRuntime.mode,
        teamHistoryMessages: baseRuntime.teamHistoryMessages,
      };
      return {
        currentSession: normalizedSession,
        runtimes: { ...state.runtimes, [sessionId]: nextRuntime },
      };
    });
  },

  setSessions: (sessions) => {
    set({ sessions: sessions.map(normalizeSession) });
  },

  addSession: (session) => {
    set((state) => ({
      sessions: [normalizeSession(session), ...state.sessions],
    }));
  },

  updateSession: (sessionId, updates) => {
    const normalizedUpdates =
      Object.prototype.hasOwnProperty.call(updates, 'mode')
        ? { ...updates, mode: normalizeAgentMode((updates as { mode?: unknown }).mode) }
        : updates;
    set((state) => ({
      sessions: state.sessions.map((s) =>
        s.session_id === sessionId ? normalizeSession({ ...s, ...normalizedUpdates }) : s
      ),
      currentSession:
        state.currentSession?.session_id === sessionId
          ? normalizeSession({ ...state.currentSession, ...normalizedUpdates })
          : state.currentSession,
    }));
  },

  removeSession: (sessionId) => {
    set((state) => ({
      sessions: state.sessions.filter((s) => s.session_id !== sessionId),
      currentSession:
        state.currentSession?.session_id === sessionId
          ? null
          : state.currentSession,
    }));
  },

  setMode: (sessionId, mode) => {
    const normalizedMode = normalizeAgentMode(mode);
    saveModeToStorage(normalizedMode);
    set((state) => {
      const runtime = state.runtimes[sessionId];
      if (!runtime) return state;
      return {
        runtimes: {
          ...state.runtimes,
          [sessionId]: { ...runtime, mode: normalizedMode },
        },
      };
    });
  },

  setProjectDirectory: (sessionId, directory) => {
    set((state) => {
      const runtime = state.runtimes[sessionId];
      if (!runtime) return state;
      return {
        runtimes: {
          ...state.runtimes,
          [sessionId]: { ...runtime, projectDirectory: directory },
        },
      };
    });
  },

  setConnected: (connected) => {
    set({ isConnected: connected });
  },

  setAvailableTools: (tools) => {
    set({ availableTools: tools });
  },

  setConnectionStats: (stats) => {
    set((state) => ({
      connectionStats: {
        ...state.connectionStats,
        ...stats,
      },
    }));
  },

  setContextCompressionStats: (sessionId, stats) => {
    if (!stats) {
      set((state) => {
        const runtime = state.runtimes[sessionId];
        if (!runtime) return state;
        return { runtimes: { ...state.runtimes, [sessionId]: {
          ...runtime, contextCompressionRate: 0, contextCompressionBefore: null, contextCompressionAfter: null,
        } } };
      });
      return;
    }

    const normalizedRate =
      typeof stats.rate === 'number' && Number.isFinite(stats.rate)
        ? Number(Math.min(Math.max(stats.rate, 0), 100).toFixed(1))
        : 0;
    const normalizedBefore =
      typeof stats.beforeCompressed === 'number' && Number.isFinite(stats.beforeCompressed)
        ? Math.max(Math.round(stats.beforeCompressed), 0)
        : null;
    const normalizedAfter =
      typeof stats.afterCompressed === 'number' && Number.isFinite(stats.afterCompressed)
        ? Math.max(Math.round(stats.afterCompressed), 0)
        : null;

    set((state) => {
      const runtime = state.runtimes[sessionId];
      if (!runtime) return state;
      return { runtimes: { ...state.runtimes, [sessionId]: {
        ...runtime,
        contextCompressionRate: normalizedRate,
        contextCompressionBefore: normalizedBefore,
        contextCompressionAfter: normalizedAfter,
      } } };
    });
  },

  setMemoryUsage: (memoryUsage) => {
    if (!memoryUsage) {
      set({
        memoryUsage: {
          rssMb: null,
          usedPercent: null,
        },
      });
      return;
    }

    const normalizedRssMb =
      typeof memoryUsage.rssMb === 'number' && Number.isFinite(memoryUsage.rssMb)
        ? Number(Math.max(memoryUsage.rssMb, 0).toFixed(1))
        : null;
    const normalizedUsedPercent =
      typeof memoryUsage.usedPercent === 'number' && Number.isFinite(memoryUsage.usedPercent)
        ? Number(Math.min(Math.max(memoryUsage.usedPercent, 0), 100).toFixed(1))
        : null;

    set({
      memoryUsage: {
        rssMb: normalizedRssMb,
        usedPercent: normalizedUsedPercent,
      },
    });
  },

  setHeartbeatStatus: (status, message = null, updatedAt) => {
    set((state) => {
      const resolvedUpdatedAt = updatedAt === undefined ? new Date().toISOString() : updatedAt;
      const shouldClearHistory = message == null && updatedAt === null;
      const nextHistory = shouldClearHistory
        ? []
        : (message
          ? [{ message, updatedAt: resolvedUpdatedAt ?? new Date().toISOString(), status }, ...state.heartbeatHistory]
              .slice(0, 20)
          : state.heartbeatHistory);

      return {
        heartbeatState: status,
        heartbeatMessage: message,
        heartbeatUpdatedAt: resolvedUpdatedAt,
        heartbeatHistory: nextHistory,
      };
    });
  },

  setTeamTaskEvents: (sessionId, events) => {
    set((state) => {
      const runtime = state.runtimes[sessionId];
      if (!runtime) return state;
      return {
        runtimes: {
          ...state.runtimes,
          [sessionId]: { ...runtime, teamTaskEvents: events },
        },
      };
    });
  },

  addTeamTaskEvent: (sessionId, event) => {
    set((state) => {
      const runtime = state.runtimes[sessionId];
      if (!runtime) return state;
      const existingIndex = runtime.teamTaskEvents.findIndex(
        (e) => e.task_id === event.task_id
      );
      if (existingIndex >= 0) {
        const updatedEvents = [...runtime.teamTaskEvents];
        updatedEvents[existingIndex] = {
          ...updatedEvents[existingIndex],
          ...event,
        };
        return {
          runtimes: {
            ...state.runtimes,
            [sessionId]: { ...runtime, teamTaskEvents: updatedEvents },
          },
        };
      }
      return {
        runtimes: {
          ...state.runtimes,
          [sessionId]: { ...runtime, teamTaskEvents: [event, ...runtime.teamTaskEvents] },
        },
      };
    });
  },

  setTeamTasks: (sessionId, tasks) => {
    set((state) => {
      const runtime = state.runtimes[sessionId];
      if (!runtime) return state;
      return {
        runtimes: {
          ...state.runtimes,
          [sessionId]: { ...runtime, teamTasks: tasks },
        },
      };
    });
  },

  upsertTeamTask: (sessionId, task) => {
    set((state) => {
      const runtime = state.runtimes[sessionId];
      if (!runtime) return state;
      const existingIndex = runtime.teamTasks.findIndex(
        (item) => item.task_id === task.task_id
      );
      if (existingIndex >= 0) {
        const existing = runtime.teamTasks[existingIndex];
        const updatedTasks = [...runtime.teamTasks];
        updatedTasks[existingIndex] = {
          ...existing,
          ...task,
          // An event without an explicit status (e.g. a content-only update)
          // must not reset the task; keep the existing status.
          status: task.status ?? existing.status,
          title: task.title ?? existing.title,
          content: task.content ?? existing.content,
          assignee: task.assignee ?? existing.assignee,
          team_id: task.team_id ?? existing.team_id,
          skills: task.skills ?? existing.skills,
          files: task.files ?? existing.files,
        };
        return {
          runtimes: {
            ...state.runtimes,
            [sessionId]: { ...runtime, teamTasks: updatedTasks },
          },
        };
      }
      return {
       runtimes: {
          ...state.runtimes,
          [sessionId]: { ...runtime, teamTasks: [{ ...task, status: task.status ?? 'pending' }, ...runtime.teamTasks],
      },
        },
      };
    });
  },

  updateTeamTask: (sessionId, taskId, patch) => {
    set((state) => {
      const runtime = state.runtimes[sessionId];
      if (!runtime) return state;
      const existingIndex = runtime.teamTasks.findIndex(
        (task) => task.task_id === taskId
      );
      if (existingIndex < 0) {
        return state;
      }
      const updatedTasks = [...runtime.teamTasks];
      updatedTasks[existingIndex] = {
        ...updatedTasks[existingIndex],
        ...patch,
        title: patch.title ?? updatedTasks[existingIndex].title,
        content: patch.content ?? updatedTasks[existingIndex].content,
        assignee: patch.assignee ?? updatedTasks[existingIndex].assignee,
        team_id: patch.team_id ?? updatedTasks[existingIndex].team_id,
        skills: patch.skills ?? updatedTasks[existingIndex].skills,
        files: patch.files ?? updatedTasks[existingIndex].files,
      };
      return {
        runtimes: {
          ...state.runtimes,
          [sessionId]: { ...runtime, teamTasks: updatedTasks },
        },
      };
    });
  },

  setTeamMembers: (sessionId, members) => {
    set((state) => {
      const runtime = state.runtimes[sessionId];
      if (!runtime) return state;
      const memberIds = new Set(members.map((member) => member.member_id));
      const nextCompression = Object.fromEntries(
        Object.entries(runtime.teamMemberContextCompression).filter(([memberId]) => memberIds.has(memberId))
      );
      return {
        runtimes: {
          ...state.runtimes,
          [sessionId]: {
            ...runtime,
            teamMembers: members,
            teamMemberContextCompression: nextCompression,
          },
        },
      };
    });
  },

  setTeamLeaderMemberIds: (sessionId, memberIds) => {
    const normalized = Array.from(
      new Set(memberIds.map((memberId) => memberId.trim()).filter(Boolean))
    );
    set((state) => {
      const runtime = state.runtimes[sessionId];
      if (!runtime) return state;
      return {
        runtimes: {
          ...state.runtimes,
          [sessionId]: { ...runtime, teamLeaderMemberIds: normalized },
        },
      };
    });
  },

  addTeamLeaderMemberId: (sessionId, memberId) => {
    const normalized = memberId.trim();
    if (!normalized) return;
    set((state) => {
      const runtime = state.runtimes[sessionId];
      if (!runtime) return state;
      if (runtime.teamLeaderMemberIds.includes(normalized)) {
        return state;
      }
      return {
        runtimes: {
          ...state.runtimes,
          [sessionId]: { ...runtime, teamLeaderMemberIds: [...runtime.teamLeaderMemberIds, normalized] },
        },
      };
    });
  },

  addSelectedSkill: (sessionId, skill) => {
    const normalized = skill.trim();
    if (!normalized) return;
    set((state) => {
      const runtime = state.runtimes[sessionId] ?? createEmptyRuntime();
      if (runtime.selectedSkills.includes(normalized)) return state;
      return {
        runtimes: {
          ...state.runtimes,
          [sessionId]: { ...runtime, selectedSkills: [...runtime.selectedSkills, normalized] },
        },
      };
    });
  },

  removeSelectedSkill: (sessionId, skill) => {
    const normalized = skill.trim();
    if (!normalized) return;
    set((state) => {
      const runtime = state.runtimes[sessionId];
      if (!runtime) return state;
      if (!runtime.selectedSkills.includes(normalized)) return state;
      return {
        runtimes: {
          ...state.runtimes,
          [sessionId]: { ...runtime, selectedSkills: runtime.selectedSkills.filter((s) => s !== normalized) },
        },
      };
    });
  },

  clearSelectedSkills: (sessionId) => {
    set((state) => {
      const runtime = state.runtimes[sessionId];
      if (!runtime) return state;
      if (runtime.selectedSkills.length === 0) return state;
      return {
        runtimes: {
          ...state.runtimes,
          [sessionId]: { ...runtime, selectedSkills: [] },
        },
      };
    });
  },

  addTeamMember: (sessionId, member) => {
    set((state) => {
      const runtime = state.runtimes[sessionId];
      if (!runtime) return state;
      const existingIndex = runtime.teamMembers.findIndex(
        (m) => m.member_id === member.member_id
      );
      if (existingIndex >= 0) {
        const updatedMembers = [...runtime.teamMembers];
        const existingMember = updatedMembers[existingIndex];
        updatedMembers[existingIndex] = {
          ...existingMember,
          ...member,
          status:
            typeof member.status === 'string' && member.status.trim() !== ''
              ? member.status
              : existingMember.status,
        };
        return {
          runtimes: {
            ...state.runtimes,
            [sessionId]: { ...runtime, teamMembers: updatedMembers },
          },
        };
      }
      return {
        runtimes: {
          ...state.runtimes,
          [sessionId]: { ...runtime, teamMembers: [member, ...runtime.teamMembers] },
        },
      };
    });
  },

  updateTeamMemberStatus: (sessionId, memberId, newStatus, timestamp) => {
    set((state) => {
      const runtime = state.runtimes[sessionId];
      if (!runtime) return state;
      const existingIndex = runtime.teamMembers.findIndex(
        (m) => m.member_id === memberId
      );
      if (existingIndex >= 0) {
        const updatedMembers = [...runtime.teamMembers];
        updatedMembers[existingIndex] = {
          ...updatedMembers[existingIndex],
          status: newStatus,
          timestamp: timestamp || Date.now(),
        };
        return {
          runtimes: {
            ...state.runtimes,
            [sessionId]: { ...runtime, teamMembers: updatedMembers },
          },
        };
      }
      return state;
    });
  },

  setTeamHumanShareCommands: (sessionId, commands) => {
    set((state) => {
      const runtime = state.runtimes[sessionId];
      if (!runtime) return state;
      return {
        runtimes: {
          ...state.runtimes,
          [sessionId]: { ...runtime, teamHumanShareCommands: commands },
        },
      };
    });
  },

  upsertTeamHumanShareCommand: (sessionId, command) => {
    set((state) => {
      const runtime = state.runtimes[sessionId];
      if (!runtime) return state;
      const existingIndex = runtime.teamHumanShareCommands.findIndex(
        (item) => item.memberName === command.memberName && item.sessionId === command.sessionId
      );
      if (existingIndex >= 0) {
        const updated = [...runtime.teamHumanShareCommands];
        const existing = updated[existingIndex];
        updated[existingIndex] = {
          ...existing,
          ...command,
          displayName: command.displayName || existing.displayName,
          teamName: command.teamName || existing.teamName,
          sessionRef: command.sessionRef || existing.sessionRef,
          joinCommand: command.joinCommand || existing.joinCommand,
          exitCommand: command.exitCommand || existing.exitCommand,
          status:
            command.status === 'pending' && existing.status !== 'pending'
              ? existing.status
              : command.status,
        };
        return {
          runtimes: {
            ...state.runtimes,
            [sessionId]: { ...runtime, teamHumanShareCommands: updated },
          },
        };
      }
      return {
        runtimes: {
          ...state.runtimes,
          [sessionId]: {
            ...runtime,
            teamHumanShareCommands: [...runtime.teamHumanShareCommands, command],
          },
        },
      };
    });
  },

  updateTeamHumanShareStatus: (sessionId, memberName, status, patch = {}) => {
    const normalizedMemberName = memberName.trim();
    if (!normalizedMemberName) return;
    set((state) => {
      const runtime = state.runtimes[sessionId];
      if (!runtime) return state;
      return {
        runtimes: {
          ...state.runtimes,
          [sessionId]: {
            ...runtime,
            teamHumanShareCommands: runtime.teamHumanShareCommands.map((command) =>
              command.memberName === normalizedMemberName
                ? {
                    ...command,
                    ...patch,
                    status,
                    updatedAt: Date.now(),
                  }
                : command
            ),
          },
        },
      };
    });
  },

  setTeamMemberExecutionEvents: (sessionId, events) => {
    set((state) => {
      const runtime = state.runtimes[sessionId];
      if (!runtime) return state;
      return {
        runtimes: {
          ...state.runtimes,
          [sessionId]: { ...runtime, teamMemberExecutionEvents: dedupeTeamMemberExecutionEvents(events).slice(0, 300) },
        },
      };
    });
  },

  addTeamMemberExecutionEvent: (sessionId, event) => {
    set((state) => {
      const runtime = state.runtimes[sessionId];
      if (!runtime) return state;
      const eventPatch = Object.fromEntries(
        Object.entries(event).filter(([, value]) => value !== undefined)
      ) as TeamMemberExecutionEvent;
      const duplicateIndex = runtime.teamMemberExecutionEvents.findIndex(
        (item) => isDuplicateFinalExecutionEvent(item, eventPatch)
      );
      if (duplicateIndex >= 0) {
        const updatedEvents = [...runtime.teamMemberExecutionEvents];
        updatedEvents[duplicateIndex] = {
          ...updatedEvents[duplicateIndex],
          ...eventPatch,
          id: updatedEvents[duplicateIndex].id,
          timestamp: Math.min(updatedEvents[duplicateIndex].timestamp || eventPatch.timestamp, eventPatch.timestamp),
        };
        return {
          runtimes: {
            ...state.runtimes,
            [sessionId]: { ...runtime, teamMemberExecutionEvents: updatedEvents },
          },
        };
      }
      const existingIndex = runtime.teamMemberExecutionEvents.findIndex(
        (item) => item.id === event.id
      );
      if (existingIndex >= 0) {
        const updatedEvents = [...runtime.teamMemberExecutionEvents];
        updatedEvents[existingIndex] = {
          ...updatedEvents[existingIndex],
          ...eventPatch,
        };
        return {
          runtimes: {
            ...state.runtimes,
            [sessionId]: { ...runtime, teamMemberExecutionEvents: updatedEvents },
          },
        };
      }
      return {
        runtimes: {
          ...state.runtimes,
          [sessionId]: { ...runtime, teamMemberExecutionEvents: [eventPatch, ...runtime.teamMemberExecutionEvents].slice(0, 300) },
        },
      };
    });
  },

  setTeamMemberContextCompressionStatus: (sessionId, memberId, runtimeState, summary) => {
    const normalizedMemberId = memberId.trim();
    if (!normalizedMemberId) return;
    set((state) => {
      const runtime = state.runtimes[sessionId];
      if (!runtime) return state;
      const next = { ...runtime.teamMemberContextCompression };
      if (!runtimeState && !summary) {
        delete next[normalizedMemberId];
      } else {
        const existing = next[normalizedMemberId];
        next[normalizedMemberId] = { runtime: runtimeState, summary: summary ?? existing?.summary };
      }
      return {
        runtimes: {
          ...state.runtimes,
          [sessionId]: { ...runtime, teamMemberContextCompression: next },
        },
      };
    });
  },

  clearTeamMemberContextCompressionStatus: (sessionId, memberId) => {
    const normalizedMemberId = memberId.trim();
    if (!normalizedMemberId) return;
    set((state) => {
      const runtime = state.runtimes[sessionId];
      if (!runtime?.teamMemberContextCompression[normalizedMemberId]) {
        return state;
      }
      const next = { ...runtime.teamMemberContextCompression };
      delete next[normalizedMemberId];
      return {
        runtimes: {
          ...state.runtimes,
          [sessionId]: { ...runtime, teamMemberContextCompression: next },
        },
      };
    });
  },

  clearAllTeamMemberContextCompressionStatus: (sessionId) => {
    set((state) => {
      const runtime = state.runtimes[sessionId];
      if (!runtime) return state;
      return {
        runtimes: {
          ...state.runtimes,
          [sessionId]: { ...runtime, teamMemberContextCompression: {} },
        },
      };
    });
  },

  setTeamHistoryMessages: (sessionId, messages) => {
    set((state) => {
      const runtime = state.runtimes[sessionId];
      if (!runtime) return state;
      return {
        runtimes: {
          ...state.runtimes,
          [sessionId]: { ...runtime, teamHistoryMessages: messages },
        },
      };
    });
  },

  setAvailableModels: (models, activeModel) => {
    set(() => {
      const chatModels = models.filter((m) => m.is_default !== false);
      // 优先使用后端返回的 activeModel（默认模型），其次取第一个；有别名时存别名
      const matchedModel = activeModel ? chatModels.find((m) => m.model_name === activeModel) : null;
      const selected = matchedModel
        ? (matchedModel.alias || matchedModel.model_name)
        : (chatModels[0] ? (chatModels[0].alias || chatModels[0].model_name) : null);
      if (selected) {
        try { localStorage.setItem(MODEL_STORAGE_KEY, selected); } catch { /* noop */ }
      }
      return { availableModels: models, chatAvailableModels: chatModels };
    });
  },

  setSelectedModelName: (sessionId, name) => {
    try { localStorage.setItem(MODEL_STORAGE_KEY, name); } catch { /* noop */ }
    set((state) => {
      const runtime = state.runtimes[sessionId];
      if (!runtime) return state;
      return { runtimes: { ...state.runtimes, [sessionId]: { ...runtime, selectedModelName: name } } };
    });
  },
}));
