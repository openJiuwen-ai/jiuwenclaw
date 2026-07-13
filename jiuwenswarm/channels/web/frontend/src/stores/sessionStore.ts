/**
 * 会话状态管理
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

const STORAGE_KEY = 'jiuwenclaw_context_compression';
const MODE_STORAGE_KEY = 'jiuwenclaw_mode';
const MODEL_STORAGE_KEY = 'jiuwenclaw_selected_model';

function loadFromStorage() {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored) {
      return JSON.parse(stored);
    }
  } catch (error) {
    console.error('Error loading context compression from storage:', error);
  }
  return null;
}

function saveToStorage(data: any) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(data));
  } catch (error) {
    console.error('Error saving context compression to storage:', error);
  }
}

function loadModeFromStorage(): AgentMode {
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
  try {
    localStorage.setItem(MODE_STORAGE_KEY, mode);
  } catch (error) {
    console.error('Error saving mode to storage:', error);
  }
}

const DEFAULT_MODE: AgentMode = 'agent.plan';

function normalizeAgentMode(mode: unknown): AgentMode {
  if (typeof mode !== 'string') return DEFAULT_MODE;
  const normalized = mode.trim().toLowerCase();
  if (normalized === 'agent.fast') return 'agent.fast';
  if (normalized === 'team') return 'team';
  if (normalized === 'auto_harness') return 'auto_harness';
  return 'agent.plan';
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
  | 'claimed'
  | 'plan_approved'
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

interface SessionState {
  currentSession: Session | null;
  sessions: Session[];
  mode: AgentMode;
  isConnected: boolean;
  availableTools: string[];
  connectionStats: ConnectionStats;
  contextCompressionRate: number;
  contextCompressionBefore: number | null;
  contextCompressionAfter: number | null;
  memoryUsage: MemoryUsage;
  heartbeatState: HeartbeatState;
  heartbeatMessage: string | null;
  heartbeatUpdatedAt: string | null;
  heartbeatHistory: HeartbeatHistoryItem[];
  teamTaskEvents: TeamTaskEvent[];
  teamTasks: TeamTask[];
  teamMembers: TeamMember[];
  teamLeaderMemberIds: string[];
  teamHumanShareCommands: HumanShareCommand[];
  teamMemberExecutionEvents: TeamMemberExecutionEvent[];
  teamMemberContextCompression: Record<string, TeamMemberContextCompressionState>;
  teamHistoryMessages: Message[];
  availableModels: ModelEntry[];
  selectedModelName: string | null;
  /** 过滤 is_default=true 的模型，供聊天窗口 ModelSelector 使用 */
  chatAvailableModels: ModelEntry[];

  // Actions
  setCurrentSession: (session: Session | null) => void;
  setSessions: (sessions: Session[]) => void;
  addSession: (session: Session) => void;
  updateSession: (sessionId: string, updates: Partial<Session>) => void;
  removeSession: (sessionId: string) => void;
  setMode: (mode: AgentMode) => void;
  setConnected: (connected: boolean) => void;
  setAvailableTools: (tools: string[]) => void;
  setConnectionStats: (stats: Partial<ConnectionStats>) => void;
  setContextCompressionRate: (rate: number) => void;
  setContextCompressionStats: (stats: Partial<ContextCompressionStats> | null) => void;
  setMemoryUsage: (memoryUsage: Partial<MemoryUsage> | null) => void;
  setHeartbeatStatus: (
    status: HeartbeatState,
    message?: string | null,
    updatedAt?: string | null
  ) => void;
  setTeamTaskEvents: (events: TeamTaskEvent[]) => void;
  addTeamTaskEvent: (event: TeamTaskEvent) => void;
  setTeamTasks: (tasks: TeamTask[]) => void;
  upsertTeamTask: (task: TeamTask) => void;
  updateTeamTask: (taskId: string, patch: Partial<TeamTask>) => void;
  setTeamMembers: (members: TeamMember[]) => void;
  setTeamLeaderMemberIds: (memberIds: string[]) => void;
  addTeamLeaderMemberId: (memberId: string) => void;
  addTeamMember: (member: TeamMember) => void;
  updateTeamMemberStatus: (memberId: string, newStatus: string, timestamp?: number) => void;
  setTeamHumanShareCommands: (commands: HumanShareCommand[]) => void;
  upsertTeamHumanShareCommand: (command: HumanShareCommand) => void;
  updateTeamHumanShareStatus: (
    memberName: string,
    status: HumanShareStatus,
    patch?: Partial<HumanShareCommand>
  ) => void;
  setTeamMemberExecutionEvents: (events: TeamMemberExecutionEvent[]) => void;
  addTeamMemberExecutionEvent: (event: TeamMemberExecutionEvent) => void;
  setTeamMemberContextCompressionStatus: (
    memberId: string,
    runtime?: ContextCompressionRuntime,
    summary?: ContextCompressionSummary
  ) => void;
  clearTeamMemberContextCompressionStatus: (memberId: string) => void;
  clearAllTeamMemberContextCompressionStatus: () => void;
  setTeamHistoryMessages: (messages: Message[]) => void;
  setAvailableModels: (models: ModelEntry[], activeModel?: string) => void;
  setSelectedModelName: (name: string) => void;
}

export const useSessionStore = create<SessionState>((set) => ({
  currentSession: null,
  sessions: [],
  mode: loadModeFromStorage(),
  isConnected: false,
  availableTools: [],
  connectionStats: {
    state: 'idle',
    inflight: 0,
    lastError: null,
  },
  contextCompressionRate: loadFromStorage()?.rate || 0,
  contextCompressionBefore: loadFromStorage()?.beforeCompressed || null,
  contextCompressionAfter: loadFromStorage()?.afterCompressed || null,
  memoryUsage: {
    rssMb: null,
    usedPercent: null,
  },
  heartbeatState: 'unknown',
  heartbeatMessage: null,
  heartbeatUpdatedAt: null,
  heartbeatHistory: [],
  teamTaskEvents: [],
  teamTasks: [],
  teamMembers: [],
  teamLeaderMemberIds: [],
  teamHumanShareCommands: [],
  teamMemberExecutionEvents: [],
  teamMemberContextCompression: {},
  teamHistoryMessages: [],
  availableModels: [],
  chatAvailableModels: [],
  selectedModelName: (() => {
    try { return localStorage.getItem(MODEL_STORAGE_KEY); } catch { return null; }
  })(),

  setCurrentSession: (session) => {
    const normalizedSession = session ? normalizeSession(session) : null;
    set((state) => ({
      currentSession: normalizedSession,
      mode: normalizedSession?.mode || state.mode,
      teamHistoryMessages:
        normalizedSession && normalizedSession.session_id === state.currentSession?.session_id
          ? state.teamHistoryMessages
          : [],
    }));
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

  setMode: (mode) => {
    const normalizedMode = normalizeAgentMode(mode);
    saveModeToStorage(normalizedMode);
    set({ mode: normalizedMode });
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

  setContextCompressionRate: (rate) => {
    const normalizedRate = Number.isFinite(rate) ? Math.min(Math.max(rate, 0), 100) : 0;
    set({ contextCompressionRate: Number(normalizedRate.toFixed(1)) });
  },

  setContextCompressionStats: (stats) => {
    if (!stats) {
      set({
        contextCompressionRate: 0,
        contextCompressionBefore: null,
        contextCompressionAfter: null,
      });
      saveToStorage(null);
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

    const contextCompressionData = {
      rate: normalizedRate,
      beforeCompressed: normalizedBefore,
      afterCompressed: normalizedAfter
    };

    set({
      contextCompressionRate: normalizedRate,
      contextCompressionBefore: normalizedBefore,
      contextCompressionAfter: normalizedAfter,
    });

    saveToStorage(contextCompressionData);
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
  setTeamTaskEvents: (events) => {
    set({ teamTaskEvents: events });
  },
  addTeamTaskEvent: (event) => {
    set((state) => {
      const existingIndex = state.teamTaskEvents.findIndex(
        (e) => e.task_id === event.task_id
      );
      if (existingIndex >= 0) {
        const updatedEvents = [...state.teamTaskEvents];
        updatedEvents[existingIndex] = {
          ...updatedEvents[existingIndex],
          ...event,
        };
        return { teamTaskEvents: updatedEvents };
      }
      return { teamTaskEvents: [event, ...state.teamTaskEvents] };
    });
  },
  setTeamTasks: (tasks) => {
    set({ teamTasks: tasks });
  },
  upsertTeamTask: (task) => {
    set((state) => {
      const existingIndex = state.teamTasks.findIndex(
        (item) => item.task_id === task.task_id
      );
      if (existingIndex >= 0) {
        const updatedTasks = [...state.teamTasks];
        updatedTasks[existingIndex] = {
          ...updatedTasks[existingIndex],
          ...task,
          title: task.title ?? updatedTasks[existingIndex].title,
          content: task.content ?? updatedTasks[existingIndex].content,
          assignee: task.assignee ?? updatedTasks[existingIndex].assignee,
          team_id: task.team_id ?? updatedTasks[existingIndex].team_id,
          skills: task.skills ?? updatedTasks[existingIndex].skills,
          files: task.files ?? updatedTasks[existingIndex].files,
        };
        return { teamTasks: updatedTasks };
      }
      return { teamTasks: [task, ...state.teamTasks] };
    });
  },
  updateTeamTask: (taskId, patch) => {
    set((state) => {
      const existingIndex = state.teamTasks.findIndex(
        (task) => task.task_id === taskId
      );
      if (existingIndex < 0) {
        return state;
      }
      const updatedTasks = [...state.teamTasks];
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
      return { teamTasks: updatedTasks };
    });
  },
  setTeamMembers: (members) => {
    set((state) => {
      const memberIds = new Set(members.map((member) => member.member_id));
      const nextCompression = Object.fromEntries(
        Object.entries(state.teamMemberContextCompression).filter(([memberId]) => memberIds.has(memberId))
      );
      return {
        teamMembers: members,
        teamMemberContextCompression: nextCompression,
      };
    });
  },
  setTeamLeaderMemberIds: (memberIds) => {
    const normalized = Array.from(
      new Set(memberIds.map((memberId) => memberId.trim()).filter(Boolean))
    );
    set({ teamLeaderMemberIds: normalized });
  },
  addTeamLeaderMemberId: (memberId) => {
    const normalized = memberId.trim();
    if (!normalized) return;
    set((state) => {
      if (state.teamLeaderMemberIds.includes(normalized)) {
        return state;
      }
      return { teamLeaderMemberIds: [...state.teamLeaderMemberIds, normalized] };
    });
  },
  addTeamMember: (member) => {
    set((state) => {
      const existingIndex = state.teamMembers.findIndex(
        (m) => m.member_id === member.member_id
      );
      if (existingIndex >= 0) {
        const updatedMembers = [...state.teamMembers];
        const existingMember = updatedMembers[existingIndex];
        updatedMembers[existingIndex] = {
          ...existingMember,
          ...member,
          status:
            typeof member.status === 'string' && member.status.trim() !== ''
              ? member.status
              : existingMember.status,
        };
        return { teamMembers: updatedMembers };
      }
      return { teamMembers: [member, ...state.teamMembers] };
    });
  },
  updateTeamMemberStatus: (memberId, newStatus, timestamp) => {
    set((state) => {
      const existingIndex = state.teamMembers.findIndex(
        (m) => m.member_id === memberId
      );
      if (existingIndex >= 0) {
        const updatedMembers = [...state.teamMembers];
        updatedMembers[existingIndex] = {
          ...updatedMembers[existingIndex],
          status: newStatus,
          timestamp: timestamp || Date.now(),
        };
        return { teamMembers: updatedMembers };
      }
      return state;
    });
  },
  setTeamHumanShareCommands: (commands) => {
    set({ teamHumanShareCommands: commands });
  },
  upsertTeamHumanShareCommand: (command) => {
    set((state) => {
      const existingIndex = state.teamHumanShareCommands.findIndex(
        (item) => item.memberName === command.memberName && item.sessionId === command.sessionId
      );
      if (existingIndex >= 0) {
        const updated = [...state.teamHumanShareCommands];
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
        return { teamHumanShareCommands: updated };
      }
      return { teamHumanShareCommands: [...state.teamHumanShareCommands, command] };
    });
  },
  updateTeamHumanShareStatus: (memberName, status, patch = {}) => {
    const normalizedMemberName = memberName.trim();
    if (!normalizedMemberName) return;
    set((state) => ({
      teamHumanShareCommands: state.teamHumanShareCommands.map((command) =>
        command.memberName === normalizedMemberName
          ? {
              ...command,
              ...patch,
              status,
              updatedAt: Date.now(),
            }
          : command
      ),
    }));
  },
  setTeamMemberExecutionEvents: (events) => {
    set({ teamMemberExecutionEvents: dedupeTeamMemberExecutionEvents(events).slice(0, 300) });
  },
  addTeamMemberExecutionEvent: (event) => {
    set((state) => {
      const eventPatch = Object.fromEntries(
        Object.entries(event).filter(([, value]) => value !== undefined)
      ) as TeamMemberExecutionEvent;
      const duplicateIndex = state.teamMemberExecutionEvents.findIndex(
        (item) => isDuplicateFinalExecutionEvent(item, eventPatch)
      );
      if (duplicateIndex >= 0) {
        const updatedEvents = [...state.teamMemberExecutionEvents];
        updatedEvents[duplicateIndex] = {
          ...updatedEvents[duplicateIndex],
          ...eventPatch,
          id: updatedEvents[duplicateIndex].id,
          timestamp: Math.min(updatedEvents[duplicateIndex].timestamp || eventPatch.timestamp, eventPatch.timestamp),
        };
        return { teamMemberExecutionEvents: updatedEvents };
      }
      const existingIndex = state.teamMemberExecutionEvents.findIndex(
        (item) => item.id === event.id
      );
      if (existingIndex >= 0) {
        const updatedEvents = [...state.teamMemberExecutionEvents];
        updatedEvents[existingIndex] = {
          ...updatedEvents[existingIndex],
          ...eventPatch,
        };
        return { teamMemberExecutionEvents: updatedEvents };
      }
      return {
        teamMemberExecutionEvents: [eventPatch, ...state.teamMemberExecutionEvents].slice(0, 300),
      };
    });
  },
  setTeamMemberContextCompressionStatus: (memberId, runtime, summary) => {
    const normalizedMemberId = memberId.trim();
    if (!normalizedMemberId) return;
    set((state) => {
      const next = { ...state.teamMemberContextCompression };
      if (!runtime && !summary) {
        delete next[normalizedMemberId];
      } else {
        const existing = next[normalizedMemberId];
        next[normalizedMemberId] = { runtime, summary: summary ?? existing?.summary };
      }
      return { teamMemberContextCompression: next };
    });
  },
  clearTeamMemberContextCompressionStatus: (memberId) => {
    const normalizedMemberId = memberId.trim();
    if (!normalizedMemberId) return;
    set((state) => {
      if (!state.teamMemberContextCompression[normalizedMemberId]) {
        return state;
      }
      const next = { ...state.teamMemberContextCompression };
      delete next[normalizedMemberId];
      return { teamMemberContextCompression: next };
    });
  },
  clearAllTeamMemberContextCompressionStatus: () => {
    set({ teamMemberContextCompression: {} });
  },
  setTeamHistoryMessages: (messages) => {
    set({ teamHistoryMessages: messages });
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
      return { availableModels: models, chatAvailableModels: chatModels, selectedModelName: selected };
    });
  },
  setSelectedModelName: (name) => {
    try { localStorage.setItem(MODEL_STORAGE_KEY, name); } catch { /* noop */ }
    set({ selectedModelName: name });
  },
}));
