/**
 * 会话状态管理
 */

import { create } from 'zustand';
import { Session, AgentMode, WebConnectionState } from '../types';

const DEFAULT_MODE: AgentMode = 'plan';

function normalizeAgentMode(mode: unknown): AgentMode {
  if (typeof mode !== 'string') return DEFAULT_MODE;
  const normalized = mode.trim().toLowerCase();
  return normalized === 'agent' ? 'agent' : 'plan';
}

function normalizeSession(session: Session): Session {
  return {
    ...session,
    mode: normalizeAgentMode(session.mode),
  };
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

interface ContextWindowUsage {
  inputTokens: number | null;
  /** Completion side tokens from the same LLM round (when reported). */
  outputTokens: number | null;
  /** input + output (and max with total_tokens when the API reports it). */
  usedTokens: number | null;
  limitTokens: number;
  percent: number | null;
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
  contextWindowUsage: ContextWindowUsage | null;
  memoryUsage: MemoryUsage;
  heartbeatState: HeartbeatState;
  heartbeatMessage: string | null;
  heartbeatUpdatedAt: string | null;
  heartbeatHistory: HeartbeatHistoryItem[];

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
  setContextWindowUsage: (usage: Partial<ContextWindowUsage> | null) => void;
  setMemoryUsage: (memoryUsage: Partial<MemoryUsage> | null) => void;
  setHeartbeatStatus: (
    status: HeartbeatState,
    message?: string | null,
    updatedAt?: string | null
  ) => void;
}

export const useSessionStore = create<SessionState>((set) => ({
  currentSession: null,
  sessions: [],
  mode: DEFAULT_MODE,
  isConnected: false,
  availableTools: [],
  connectionStats: {
    state: 'idle',
    inflight: 0,
    lastError: null,
  },
  contextCompressionRate: 0,
  contextCompressionBefore: null,
  contextCompressionAfter: null,
  contextWindowUsage: null,
  memoryUsage: {
    rssMb: null,
    usedPercent: null,
  },
  heartbeatState: 'unknown',
  heartbeatMessage: null,
  heartbeatUpdatedAt: null,
  heartbeatHistory: [],

  setCurrentSession: (session) => {
    const normalizedSession = session ? normalizeSession(session) : null;
    set({
      currentSession: normalizedSession,
      mode: normalizedSession?.mode || DEFAULT_MODE,
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

  setMode: (mode) => {
    set({ mode: normalizeAgentMode(mode) });
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

    set({
      contextCompressionRate: normalizedRate,
      contextCompressionBefore: normalizedBefore,
      contextCompressionAfter: normalizedAfter,
    });
  },

  setContextWindowUsage: (usage) => {
    if (!usage) {
      set({ contextWindowUsage: null });
      return;
    }
    const inputTokens =
      typeof usage.inputTokens === 'number' && Number.isFinite(usage.inputTokens)
        ? Math.max(Math.round(usage.inputTokens), 0)
        : null;
    const outputTokens =
      typeof usage.outputTokens === 'number' && Number.isFinite(usage.outputTokens)
        ? Math.max(Math.round(usage.outputTokens), 0)
        : null;
    const usedTokens =
      typeof usage.usedTokens === 'number' && Number.isFinite(usage.usedTokens)
        ? Math.max(Math.round(usage.usedTokens), 0)
        : null;
    const limitTokens =
      typeof usage.limitTokens === 'number' && Number.isFinite(usage.limitTokens) && usage.limitTokens > 0
        ? usage.limitTokens
        : 128000;
    const percent =
      typeof usage.percent === 'number' && Number.isFinite(usage.percent)
        ? Number(Math.min(Math.max(usage.percent, 0), 100).toFixed(1))
        : null;
    set({ contextWindowUsage: { inputTokens, outputTokens, usedTokens, limitTokens, percent } });
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
}));
