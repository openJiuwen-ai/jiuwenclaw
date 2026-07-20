/**
 * WebSocket Hook
 *
 * 管理 WebSocket 连接和消息处理
 */

import { useEffect, useRef, useCallback, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  ConnectionAckPayload,
  WebConnectOptions,
  WebError,
  WebRequestOptions,
  WebConnectionState,
  InterruptResultPayload,
  InterruptIntent,
  SubtaskUpdatePayload,
  AskUserQuestionPayload,
  EvolutionStatusPayload,
  UserAnswer,
  MediaItem,
  AgentMode,
  Session,
  ToolResult,
  ToolCall,
  UsageSummary,
  FileDownloadItem,
  ContextCompressionRuntime,
  ContextCompressionSummary,
  WsEvent,
} from '../types';
import {
  ensureSessionRuntimes,
  useChatStore,
  useTodoStore,
  useSessionStore,
  useHarnessStore,
  useWorkspaceStore,
  useCronStore,
} from '../stores';
import type { TeamTask, TeamTaskStatus, TeamTaskUpsert } from '../stores/sessionStore';
import { webClient } from '../services/webClient';
import {
  fetchTtsAudio,
  playAudioBase64,
  sanitizeTtsText,
  stopAllTts,
  normalizeFinalContent,
} from '../utils';
import {
  normalizeToolCallPayload,
  normalizeToolResultPayload,
} from '../features/tool-events/toolEventNormalizer';
import { findActiveTeamLeaderMessage as findActiveTeamLeaderMessageInTurn } from '../features/teamLeaderMessages';

const WS_RECONNECT_EVENT = 'jiuwenclaw:ws-reconnect-request';

function isCompletedResumeResult(interruptResult: unknown): boolean {
  if (!interruptResult || typeof interruptResult !== 'object') {
    return false;
  }
  const result = interruptResult as {
    intent?: unknown;
    success?: unknown;
    has_active_task?: unknown;
  };
  return result.intent === 'resume' && result.success === true && result.has_active_task === false;
}

function getConnectSignature(options: WebConnectOptions): string {
  return JSON.stringify({
    provider: options.provider || '',
    apiKey: options.apiKey || '',
    apiBase: options.apiBase || '',
    model: options.model || '',
    projectDir: options.projectDir || '',
  });
}

const TEAM_TASK_STATUS_SET = new Set<TeamTaskStatus>([
  'pending',
  'blocked',
  'planning',
  'in_progress',
  'in_review',
  'completed',
  'cancelled',
]);

function normalizeTeamTaskStatus(
  status: unknown,
  fallback: TeamTaskStatus = 'pending'
): TeamTaskStatus {
  return typeof status === 'string' && TEAM_TASK_STATUS_SET.has(status as TeamTaskStatus)
    ? status as TeamTaskStatus
    : fallback;
}

function pickString(...values: unknown[]) {
  for (const value of values) {
    if (typeof value === 'string' && value.trim()) {
      return value;
    }
  }
  return undefined;
}

function resolveInterruptResumeMode(sessionId: string): AgentMode {
  const sessionStore = useSessionStore.getState();
  const session =
    sessionStore.currentSession?.session_id === sessionId
      ? sessionStore.currentSession
      : sessionStore.sessions.find((item) => item.session_id === sessionId);
  if (session?.team_name?.trim()) return 'team';
  return normalizeAgentMode(sessionStore.runtimes[sessionId]?.mode);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
}

function getPayloadSessionId(payload: Record<string, unknown>): string | undefined {
  const direct = pickString(payload.session_id);
  if (direct) {
    return direct;
  }
  const nestedPayload = payload.payload;
  if (isRecord(nestedPayload)) {
    const nested = pickString(nestedPayload.session_id);
    if (nested) {
      return nested;
    }
    const nestedEvent = nestedPayload.event;
    if (isRecord(nestedEvent)) {
      return pickString(nestedEvent.session_id);
    }
  }
  const event = payload.event;
  if (isRecord(event)) {
    return pickString(event.session_id);
  }
  return undefined;
}

function getPayloadRequestId(payload: Record<string, unknown>): string | undefined {
  const direct = pickString(payload.request_id, payload.rid);
  if (direct) {
    return direct;
  }
  const nestedPayload = payload.payload;
  if (isRecord(nestedPayload)) {
    const nested = pickString(nestedPayload.request_id, nestedPayload.rid);
    if (nested) {
      return nested;
    }
  }
  return undefined;
}

function normalizeStringArray(value: unknown): string[] | undefined {
  if (!Array.isArray(value)) {
    return undefined;
  }
  const normalized = value.filter(
    (item): item is string => typeof item === 'string' && item.trim().length > 0
  );
  return normalized.length ? normalized : undefined;
}

function normalizeTaskEvent(value: unknown): TeamTaskUpsert | null {
  if (!value || typeof value !== 'object') {
    return null;
  }
  const raw = value as Record<string, unknown>;
  const taskId = pickString(raw.task_id, raw.id);
  if (!taskId) {
    return null;
  }
  // Status is resolved server-side (swarm layer) and read directly here — the
  // frontend no longer derives it from the event type. An absent status means
  // "no change"; the store preserves the task's existing status.
  const rawStatus = pickString(raw.status);
  return {
    task_id: taskId,
    title: pickString(raw.title, raw.name, raw.description),
    content: pickString(raw.content),
    status: rawStatus ? normalizeTeamTaskStatus(rawStatus) : undefined,
    assignee: pickString(raw.assignee, raw.member_id, raw.claimed_by, raw.claimedBy, raw.from_member),
    team_id: pickString(raw.team_id),
    timestamp: typeof raw.timestamp === 'number' ? raw.timestamp : Date.now(),
    skills: normalizeStringArray(raw.skills),
    files: normalizeStringArray(raw.files),
  };
}

function normalizeTaskRecord(
  value: unknown,
  fallbackStatus: TeamTaskStatus = 'pending'
): TeamTask | null {
  if (!value || typeof value !== 'object') {
    return null;
  }
  const raw = value as Record<string, unknown>;
  const taskId = pickString(raw.task_id, raw.id);
  if (!taskId) {
    return null;
  }
  const title = pickString(raw.title, raw.name, raw.description);
  const content = pickString(raw.content);
  return {
    task_id: taskId,
    title,
    content,
    status: normalizeTeamTaskStatus(raw.status, fallbackStatus),
    assignee: pickString(raw.assignee, raw.member_id, raw.claimed_by, raw.claimedBy, raw.from_member),
    team_id: pickString(raw.team_id),
    timestamp: typeof raw.timestamp === 'number' ? raw.timestamp : Date.now(),
    skills: normalizeStringArray(raw.skills),
    files: normalizeStringArray(raw.files),
  };
}

function parseShutdownMemberName(value: unknown): string | undefined {
  if (typeof value !== 'string') {
    return undefined;
  }
  const match = value.match(/Member shutdown:\s*member_name=([^\s,]+)/);
  return match?.[1]?.trim() || undefined;
}

function getShutdownMemberFromToolCall(toolCall: ToolCall): string | undefined {
  if (toolCall.name !== 'shutdown_member') {
    return undefined;
  }
  return pickString(
    toolCall.arguments.member_name,
    toolCall.arguments.member_id,
    toolCall.arguments.name
  );
}

function getShutdownMemberFromToolResult(toolResult: ToolResult): string | undefined {
  if (toolResult.toolName !== 'shutdown_member') {
    return parseShutdownMemberName(toolResult.result);
  }
  return parseShutdownMemberName(toolResult.result) || parseShutdownMemberName(toolResult.summary);
}

function upsertTaskRecords(sessionId: string, values: unknown, fallbackStatus: TeamTaskStatus = 'pending') {
  if (!Array.isArray(values)) {
    const task = normalizeTaskRecord(values, fallbackStatus);
    if (task) {
      useSessionStore.getState().upsertTeamTask(sessionId, task);
    }
    return;
  }
  values.forEach((item) => {
    const task = normalizeTaskRecord(item, fallbackStatus);
    if (task) {
      useSessionStore.getState().upsertTeamTask(sessionId, task);
    }
  });
}

function applyTeamTaskToolCall(sessionId: string, toolCall: ToolCall) {
  if (toolCall.name === 'create_task') {
    upsertTaskRecords(sessionId, Array.isArray(toolCall.arguments.tasks) ? toolCall.arguments.tasks : toolCall.arguments);
    return;
  }
  if (toolCall.name === 'update_task') {
    const taskId = pickString(toolCall.arguments.task_id, toolCall.arguments.id);
    const existingStatus = taskId
      ? useSessionStore.getState().getRuntime(sessionId)?.teamTasks.find((task) => task.task_id === taskId)?.status
      : undefined;
    upsertTaskRecords(sessionId, toolCall.arguments, existingStatus || 'pending');
    return;
  }
  if (toolCall.name === 'claim_task') {
    return;
  }
}

interface UseWebSocketOptions {
  activeSessionId?: string;
  provider?: string;
  apiKey?: string;
  apiBase?: string;
  model?: string;
  projectDir?: string;
  onConnect?: (payload: ConnectionAckPayload) => void;
  onDisconnect?: () => void;
  onError?: (error: string) => void;
  onConfigChanged?: (updatedKeys?: string[]) => void;
}

interface UseWebSocketReturn {
  isConnected: boolean;
  connectionState: WebConnectionState;
  request: <T = unknown>(
    method: string,
    params?: Record<string, unknown>,
    options?: WebRequestOptions
  ) => Promise<T>;
  persistMedia: (content: string, sessionId: string, mediaItems: MediaItem[]) => Promise<PersistMediaResponse>;
  sendMessage: (content: string, sessionId: string, mediaItems?: MediaItem[]) => Promise<boolean>;
  sendStructuredChatContent: (content: unknown, sessionId: string) => Promise<void>;
  interrupt: (
    sessionId: string,
    intent: InterruptIntent,
    options?: { newInput?: string }
  ) => Promise<void>;
  pause: (sessionId: string) => Promise<void>;
  cancel: (sessionId: string) => Promise<void>;
  supplement: (sessionId: string, newInput: string) => Promise<void>;
  resume: (sessionId: string) => Promise<void>;
  switchMode: (sessionId: string, mode: AgentMode) => Promise<void>;
  disconnect: () => void;
  sendUserAnswer: (
    sessionId: string,
    requestId: string,
    answers: UserAnswer[],
    source?: string
  ) => Promise<void>;
  respondActivate: (
    sessionId: string,
    interactionId: string,
    action: 'accept' | 'reject',
    feedback?: string
  ) => Promise<void>;
  getInflightCount: () => number;
}

interface PersistMediaResponse {
  content?: string;
  query?: string;
  media_items?: Record<string, unknown>[];
  files?: Record<string, unknown>;
}

function isPersistedMediaItem(item: MediaItem): boolean {
  return typeof item.path === 'string' && item.path.trim().length > 0;
}

function getMediaMimeType(item: MediaItem): string {
  return item.mime_type || item.mimeType;
}

function toPersistedMediaRecord(item: MediaItem): Record<string, unknown> {
  return {
    type: item.type,
    filename: item.filename,
    mime_type: getMediaMimeType(item),
    path: item.path,
    size_bytes: item.size_bytes ?? item.sizeBytes,
  };
}

function buildPersistedMediaFiles(mediaItems: MediaItem[]): Record<string, unknown> {
  return {
    uploaded_images: mediaItems.map((item) => ({
      filename: item.filename,
      path: item.path,
      mime_type: getMediaMimeType(item),
      size_bytes: item.size_bytes ?? item.sizeBytes,
    })),
  };
}

interface ContextCompressionStatePayload extends Record<string, unknown> {
  status?: string;
  summary?: string;
  operation_id?: string;
  phase?: string;
  processor?: string;
}

interface PendingContextCompressionStart {
  timer: ReturnType<typeof setTimeout>;
  runtimeState: Omit<ContextCompressionRuntime, 'status'>;
  shown: boolean;
}

function normalizeAgentMode(rawMode: unknown): AgentMode {
  if (typeof rawMode !== 'string') return 'agent';
  const normalized = rawMode.trim().toLowerCase();
  if (normalized === 'team') return 'team';
  if (normalized === 'auto_harness') return 'auto_harness';
  return 'agent';
}

function unsupportedEvolutionModeMessage(content: string, mode: AgentMode): string | null {
  const trimmed = content.trim();
  const isEvolutionCommand =
    trimmed === '/evolve' ||
    trimmed.startsWith('/evolve ') ||
    trimmed === '/evolve_simplify' ||
    trimmed.startsWith('/evolve_simplify ');
  if (!isEvolutionCommand || mode === 'agent' || mode === 'team') {
    return null;
  }
  return `${mode} 模式下演进功能不可用。`;
}

const EVENT_DEDUP_WINDOW_MS = 1500;
const CONTEXT_COMPRESSION_START_DELAY_MS = 300;

function normalizeEventTimestampIso(value: unknown): string {
  if (typeof value === 'number' && Number.isFinite(value)) {
    const millis = value > 1_000_000_000_000 ? value : value * 1000;
    const date = new Date(millis);
    if (!Number.isNaN(date.getTime())) {
      return date.toISOString();
    }
  }
  if (typeof value === 'string') {
    const parsed = Date.parse(value);
    if (!Number.isNaN(parsed)) {
      return new Date(parsed).toISOString();
    }
  }
  return new Date().toISOString();
}

function isTeamTeammateMessagePayload(payload: Record<string, unknown>): boolean {
  return typeof payload.role === 'string' && payload.role.trim().toLowerCase() === 'teammate';
}

function isHiddenTeamTeammateMessagePayload(mode: AgentMode, payload: Record<string, unknown>): boolean {
  return mode === 'team' && isTeamTeammateMessagePayload(payload);
}

function getTeamPayloadMemberName(payload: Record<string, unknown>): string | undefined {
  return pickString(payload.member_name, payload.member_id, payload.source_member);
}

function eventTimestampMs(payload: Record<string, unknown>): number {
  const value = payload.timestamp;
  if (typeof value === 'number' && Number.isFinite(value)) {
    return value > 1_000_000_000_000 ? value : value * 1000;
  }
  if (typeof value === 'string') {
    const parsed = Date.parse(value);
    if (!Number.isNaN(parsed)) {
      return parsed;
    }
  }
  return Date.now();
}

function stableEventId(...parts: unknown[]): string {
  return parts
    .map((part) => String(part ?? '').trim())
    .filter(Boolean)
    .join(':')
    .replace(/[^a-zA-Z0-9:_-]+/g, '-')
    .slice(0, 180);
}

function getAgentRefId(payload: Record<string, unknown>): string | undefined {
  const direct = payload.agent_ref;
  if (isRecord(direct)) {
    const id = pickString(direct.id);
    if (id) {
      return id;
    }
  }
  const nestedPayload = payload.payload;
  if (isRecord(nestedPayload)) {
    const nested = nestedPayload.agent_ref;
    if (isRecord(nested)) {
      return pickString(nested.id);
    }
  }
  return undefined;
}

function upsertHumanShareCommandFromEvent(
  payload: Record<string, unknown>,
  event: { member_id?: string; name?: string; mode?: string; timestamp?: number }
): void {
  if (event.mode !== 'human' || !event.member_id) {
    return;
  }
  const sessionId = getPayloadSessionId(payload);
  if (!sessionId) {
    return;
  }
  const teamName = getAgentRefId(payload) || 'unknown';
  const sessionRef = `team_${teamName}_session_${sessionId}`;
  useSessionStore.getState().upsertTeamHumanShareCommand(
    sessionId,
    {
      memberName: event.member_id,
      displayName: event.name,
      sessionId,
      teamName,
      sessionRef,
      joinCommand: `/join ${sessionRef} as ${event.member_id}`,
      exitCommand: `/exit ${sessionRef}`,
      status: 'pending',
      updatedAt: event.timestamp || Date.now(),
    },
  );
}

function stringifyCompact(value: unknown): string {
  if (typeof value === 'string') {
    return value;
  }
  try {
    return JSON.stringify(value);
  } catch {
    return String(value ?? '');
  }
}

function stringifyPayloadForDedup(payload: Record<string, unknown>): string {
  try {
    const serialized = JSON.stringify(payload);
    if (!serialized) {
      return '';
    }
    return serialized.length > 800 ? serialized.slice(0, 800) : serialized;
  } catch {
    return '';
  }
}

function makeEventDedupKey(eventName: string, payload: Record<string, unknown>): string {
  const payloadSessionId =
    typeof payload.session_id === 'string' ? payload.session_id : '';
  const payloadEventType =
    typeof payload.event_type === 'string' ? payload.event_type : '';
  const payloadSnapshot = stringifyPayloadForDedup(payload);
  return `${eventName}::${payloadSessionId}::${payloadEventType}::${payloadSnapshot}`;
}

export function useWebSocket(options: UseWebSocketOptions): UseWebSocketReturn {
  const { t } = useTranslation();
  const {
    provider,
    apiKey,
    apiBase,
    model,
    projectDir,
    onConnect,
    onDisconnect,
    onError,
    onConfigChanged,
  } = options;

  // 同步更新 ref，避免竞态条件
  // 必须在渲染阶段同步更新，否则 effect 执行之前收到的事件会被错误过滤
  const userInputVersionRef = useRef(0);
  const activeRequestIdRef = useRef<string | undefined>(undefined);
  // 立即同步更新，不等待 effect

  const [isConnected, setIsConnected] = useState(false);
  const [connectionState, setConnectionState] =
    useState<WebConnectionState>('idle');
  const lastConnectSignatureRef = useRef<string>('');
  const onConnectRef = useRef(onConnect);
  const onDisconnectRef = useRef(onDisconnect);
  const onErrorRef = useRef(onError);
  const onConfigChangedRef = useRef(onConfigChanged);
  const sendMessageRef = useRef<typeof sendMessage>();
  const recentEventRef = useRef<Map<string, number>>(new Map());
  const teamToolCallMemberRef = useRef<Map<string, string>>(new Map());
  const shutdownMemberToolCallRef = useRef<Map<string, string>>(new Map());
  const clearedTeamPanelSessionRef = useRef<Set<string>>(new Set());
  const teamMemberOutputEventRef = useRef<Map<string, string>>(new Map());
  const eventDedupDroppedRef = useRef<Record<string, number>>({});
  const symphonyStatusTargetRef = useRef<Map<string, { messageId: string; baseContent: string }>>(
    new Map()
  );
  const contextCompressionSummaryRef = useRef<Map<string, ContextCompressionSummary>>(new Map());
  const pendingContextCompressionStartRef =
    useRef<Map<string, PendingContextCompressionStart>>(new Map());
  const pendingTeamMemberContextCompressionStartRef =
    useRef<Map<string, PendingContextCompressionStart>>(new Map());
  const heldContextUsageSessionsRef = useRef<Set<string>>(new Set());
  const pendingContextUsageRef = useRef<Map<string, {
    rate: number;
    beforeCompressed: number | null;
    afterCompressed: number | null;
  }>>(new Map());

  // Stores: 仅保留全局 action（A 类，不需要 sessionId）
  const {
    setConnected,
    setAvailableTools,
    setConnectionStats,
    updateSession,
    setContextCompressionStats,
    setHeartbeatStatus,
    setTeamMemberContextCompressionStatus,
    clearTeamMemberContextCompressionStatus,
    clearAllTeamMemberContextCompressionStatus,
  } = useSessionStore();

  const resolveEventSessionId = useCallback(
    (payload: Record<string, unknown>): string | null => {
      const payloadSessionId = getPayloadSessionId(payload);
      if (!payloadSessionId) return null;
      ensureSessionRuntimes(payloadSessionId);
      return payloadSessionId;
    },
    []
  );

  const handleTtsPlayback = useCallback(
    (sessionId: string, messageId: string, content: string) => {
      const sanitized = sanitizeTtsText(content);
      if (!sanitized || sanitized.startsWith('[任务已中断]')) {
        return;
      }

      const existing = useChatStore.getState().getRuntime(sessionId)?.messages.find((msg) => msg.id === messageId);
      if (existing?.audioBase64) {
        return;
      }

      void (async () => {
        const versionAtStart = userInputVersionRef.current;
        const ttsSessionId = sessionId;
        const response = await fetchTtsAudio(
          sanitized,
          ttsSessionId && ttsSessionId !== 'new' ? ttsSessionId : undefined
        );
        if (!response?.success || !response.audio_base64) {
          return;
        }

        useChatStore.getState().updateMessage(sessionId, messageId, {
          audioBase64: response.audio_base64,
          audioMime: response.audio_mime,
        });

        if (versionAtStart !== userInputVersionRef.current) {
          return;
        }

        await playAudioBase64(
          response.audio_base64,
          response.audio_mime || 'audio/mpeg'
        );
      })();
    },
    []
  );

  const handleConnectionAck = useCallback(
    (payload: Record<string, unknown>) => {
      const ackPayload = payload as unknown as ConnectionAckPayload;
      setConnected(true);
      if (Array.isArray(ackPayload.tools)) {
        setAvailableTools(ackPayload.tools);
      }
      useChatStore.getState().setGlobalTaskRunning(Boolean(ackPayload.task_running));
      onConnectRef.current?.(ackPayload);
    },
    [setAvailableTools, setConnected]
  );

  // 断开连接
  const disconnect = useCallback(() => {
    webClient.disconnect();
  }, []);

  const request = useCallback(
    async <T = unknown>(
      method: string,
      params?: Record<string, unknown>,
      requestOptions?: WebRequestOptions
    ): Promise<T> => {
      return webClient.request<T>(method, params, requestOptions);
    },
    []
  );

  const findActiveTeamLeaderMessage = useCallback((sessionId: string) => {
    const messages = useChatStore.getState().getRuntime(sessionId)?.messages ?? [];
    return findActiveTeamLeaderMessageInTurn(messages);
  }, []);

  const closeActiveTeamLeaderMessages = useCallback((sessionId: string) => {
    const messages = useChatStore.getState().getRuntime(sessionId)?.messages ?? [];
    for (const msg of messages) {
      if (msg.id.startsWith('team-leader-') && msg.isStreaming) {
        useChatStore.getState().updateMessage(sessionId, msg.id, { isStreaming: false });
      }
    }
  }, []);

  const clearPendingContextCompressionStart = useCallback((sessionId: string) => {
    const pending = pendingContextCompressionStartRef.current.get(sessionId);
    if (pending) {
      clearTimeout(pending.timer);
      pendingContextCompressionStartRef.current.delete(sessionId);
    }
  }, []);

  const getTeamMemberContextCompressionKey = useCallback(
    (sessionId: string, memberId: string) => `${sessionId}\u0000${memberId}`,
    []
  );

  const clearPendingTeamMemberContextCompressionStart = useCallback((sessionId: string, memberId: string) => {
    const key = getTeamMemberContextCompressionKey(sessionId, memberId);
    const pending = pendingTeamMemberContextCompressionStartRef.current.get(key);
    if (!pending) return;
    clearTimeout(pending.timer);
    pendingTeamMemberContextCompressionStartRef.current.delete(key);
  }, [getTeamMemberContextCompressionKey]);

  const clearAllPendingTeamMemberContextCompressionStarts = useCallback(() => {
    for (const pending of pendingTeamMemberContextCompressionStartRef.current.values()) {
      clearTimeout(pending.timer);
    }
    pendingTeamMemberContextCompressionStartRef.current.clear();
  }, []);

  const resetContextCompressionTurn = useCallback((sessionId: string) => {
    clearPendingContextCompressionStart(sessionId);
    contextCompressionSummaryRef.current.delete(sessionId);
    useChatStore.getState().setContextCompressionStatus(sessionId, undefined);
  }, [clearPendingContextCompressionStart]);

  const finishContextCompressionTurn = useCallback((sessionId: string) => {
    clearPendingContextCompressionStart(sessionId);
    const summary = contextCompressionSummaryRef.current.get(sessionId);
    useChatStore.getState().setContextCompressionStatus(sessionId, undefined, summary && summary.count > 0 ? summary : undefined);
  }, [clearPendingContextCompressionStart]);

  const buildContextCompressionRuntimeState = useCallback(
    (payload: ContextCompressionStatePayload): Omit<ContextCompressionRuntime, 'status'> | null => {
      const summary = payload.summary?.trim() || '';
      if (!summary) return null;
      return {
        summary,
        operationId: payload.operation_id?.trim() || '',
        phase: payload.phase?.trim() || undefined,
        processor: payload.processor?.trim() || undefined,
      };
    },
    []
  );

  const handleContextCompressionState = useCallback(
    (sessionId: string, payload: ContextCompressionStatePayload) => {
      const status = payload.status?.trim().toLowerCase() || '';
      const runtimeState = buildContextCompressionRuntimeState(payload);
      if (!status || !runtimeState) return;

      if (status === 'completed') {
        clearPendingContextCompressionStart(sessionId);
        const current = contextCompressionSummaryRef.current.get(sessionId) ?? { count: 0, summaries: [] };
        const nextSummary = {
          count: current.count + 1,
          summaries: [...current.summaries, runtimeState.summary],
        };
        contextCompressionSummaryRef.current.set(sessionId, nextSummary);
        useChatStore.getState().setContextCompressionStatus(sessionId, {
          ...runtimeState,
          status: 'completed',
        });
        return;
      }

      if (status === 'started' || status === 'running') {
        clearPendingContextCompressionStart(sessionId);
        const pending: PendingContextCompressionStart = {
          runtimeState,
          shown: false,
          timer: setTimeout(() => {
            const current = pendingContextCompressionStartRef.current.get(sessionId);
            if (current !== pending) return;
            pending.shown = true;
            useChatStore.getState().setContextCompressionStatus(sessionId, {
              ...pending.runtimeState,
              status: 'running',
            });
          }, CONTEXT_COMPRESSION_START_DELAY_MS),
        };
        pendingContextCompressionStartRef.current.set(sessionId, pending);
        return;
      }

      if (status === 'noop' || status === 'skipped') {
        const pending = pendingContextCompressionStartRef.current.get(sessionId);
        if (pending && !pending.shown) {
          clearPendingContextCompressionStart(sessionId);
          return;
        }
        if (pending) {
          clearPendingContextCompressionStart(sessionId);
        }
        useChatStore.getState().setContextCompressionStatus(sessionId, {
          ...runtimeState,
          status: 'unchanged',
        });
        return;
      }

      if (status === 'failed' || status === 'error') {
        clearPendingContextCompressionStart(sessionId);
        useChatStore.getState().setContextCompressionStatus(sessionId, {
          ...runtimeState,
          status: 'failed',
        });
      }
    },
    [buildContextCompressionRuntimeState, clearPendingContextCompressionStart]
  );

  const findExistingTeamMemberId = useCallback((sessionId: string, memberName: unknown): string | null => {
    if (typeof memberName !== 'string' || !memberName.trim()) {
      return null;
    }
    const candidate = memberName.trim();
    const existingMember = useSessionStore
      .getState()
      .getRuntime(sessionId)
      ?.teamMembers.find((member) => member.member_id === candidate);
    return existingMember?.member_id || null;
  }, []);

  const handleTeamMemberContextCompressionState = useCallback(
    (sessionId: string, payload: ContextCompressionStatePayload, memberId: string) => {
      const status = payload.status?.trim().toLowerCase() || '';
      const runtimeState = buildContextCompressionRuntimeState(payload);
      if (!status || !runtimeState) return;

      if (status === 'completed') {
        clearPendingTeamMemberContextCompressionStart(sessionId, memberId);
        const current =
          useSessionStore.getState().getRuntime(sessionId)?.teamMemberContextCompression[memberId]?.summary;
        const nextSummary = {
          count: (current?.count || 0) + 1,
          summaries: [...(current?.summaries || []), runtimeState.summary],
        };
        setTeamMemberContextCompressionStatus(sessionId, memberId, {
          ...runtimeState,
          status: 'completed',
        }, nextSummary);
        return;
      }

      if (status === 'started' || status === 'running') {
        clearPendingTeamMemberContextCompressionStart(sessionId, memberId);
        const key = getTeamMemberContextCompressionKey(sessionId, memberId);
        const pending: PendingContextCompressionStart = {
          runtimeState,
          shown: false,
          timer: setTimeout(() => {
            if (pendingTeamMemberContextCompressionStartRef.current.get(key) !== pending) return;
            pending.shown = true;
            setTeamMemberContextCompressionStatus(sessionId, memberId, {
              ...pending.runtimeState,
              status: 'running',
            });
          }, CONTEXT_COMPRESSION_START_DELAY_MS),
        };
        pendingTeamMemberContextCompressionStartRef.current.set(key, pending);
        return;
      }

      if (status === 'noop' || status === 'skipped') {
        const key = getTeamMemberContextCompressionKey(sessionId, memberId);
        const pending = pendingTeamMemberContextCompressionStartRef.current.get(key);
        if (pending && !pending.shown) {
          clearPendingTeamMemberContextCompressionStart(sessionId, memberId);
          return;
        }
        if (pending) {
          clearPendingTeamMemberContextCompressionStart(sessionId, memberId);
        }
        setTeamMemberContextCompressionStatus(sessionId, memberId, {
          ...runtimeState,
          status: 'unchanged',
        });
        return;
      }

      if (status === 'failed' || status === 'error') {
        clearPendingTeamMemberContextCompressionStart(sessionId, memberId);
        setTeamMemberContextCompressionStatus(sessionId, memberId, {
          ...runtimeState,
          status: 'failed',
        });
      }
    },
    [
      buildContextCompressionRuntimeState,
      clearPendingTeamMemberContextCompressionStart,
      getTeamMemberContextCompressionKey,
      setTeamMemberContextCompressionStatus,
    ]
  );

  useEffect(() => {
    return () => {
      pendingContextCompressionStartRef.current.forEach((pending) => {
        clearTimeout(pending.timer);
      });
      pendingContextCompressionStartRef.current.clear();
      clearAllPendingTeamMemberContextCompressionStarts();
    };
  }, [clearAllPendingTeamMemberContextCompressionStarts]);

  const persistMedia = useCallback(
    async (content: string, sessionId: string, mediaItems: MediaItem[]) => {
      return request<PersistMediaResponse>('media.persist', {
        session_id: sessionId,
        content,
        media_items: mediaItems as unknown as Record<string, unknown>[],
      });
    },
    [request],
  );

  // 发送聊天消息
  const sendMessage = useCallback(
    async (content: string, sessionId: string, mediaItems: MediaItem[] = []): Promise<boolean> => {
      const hasMedia = mediaItems.length > 0;
      if (!content.trim() && !hasMedia) return false;

      const currentMode = useSessionStore.getState().getRuntime(sessionId)?.mode;
      const unsupportedEvolutionMode = unsupportedEvolutionModeMessage(content, currentMode ?? 'agent');
      if (unsupportedEvolutionMode) {
        useChatStore.getState().addMessage(sessionId, {
          id: `error-${Date.now()}`,
          role: 'system',
          content: unsupportedEvolutionMode,
          timestamp: new Date().toISOString(),
        });
        return false;
      }

      const isInitialUserMessage = !useChatStore
        .getState()
        .getRuntime(sessionId)
        ?.messages.some((message) => message.role === 'user');
      if (isInitialUserMessage) {
        heldContextUsageSessionsRef.current.add(sessionId);
        pendingContextUsageRef.current.delete(sessionId);
        setContextCompressionStats(sessionId, {
          rate: 0,
          beforeCompressed: 0,
          afterCompressed: 0,
        });
      }

      resetContextCompressionTurn(sessionId);
      userInputVersionRef.current += 1;
      stopAllTts();

      // A new query supersedes an unanswered inline question for this same session.
      if (useChatStore.getState().getRuntime(sessionId)?.pendingQuestion) {
        useChatStore.getState().setPendingQuestion(sessionId, null);
      }

      // 添加用户消息（附带输入栏选中的技能）
      const selectedSkills = useSessionStore.getState().getRuntime(sessionId)?.selectedSkills ?? [];
      useChatStore.getState().addMessage(sessionId, {
        id: `user-${Date.now()}`,
        role: 'user',
        content,
        mediaItems,
        timestamp: new Date().toISOString(),
        ...(selectedSkills.length > 0 ? { skills: selectedSkills } : {}),
      });
      // 发送后清空输入栏已选技能
      if (selectedSkills.length > 0) {
        useSessionStore.getState().clearSelectedSkills(sessionId);
      }

      // 不再预先创建助手消息，而是在收到第一个 content_chunk 时创建
      // 这样工具调用会先显示，然后才是助手的回复

      useChatStore.getState().setProcessing(sessionId, true);
      useChatStore.getState().setThinking(sessionId, true);

      // 正常调用接口
      const selectedModel = useSessionStore.getState().getRuntime(sessionId)?.selectedModelName;
      const currentSessionState = useSessionStore.getState();
      const currentSession = currentSessionState.currentSession?.session_id === sessionId
        ? currentSessionState.currentSession
        : currentSessionState.sessions.find((s) => s.session_id === sessionId);
      const projectDir = currentSession?.project_dir || undefined;
      if (currentMode === 'auto_harness') {
        useHarnessStore.getState().reset(sessionId);
      }
      if (currentMode === 'team') {
        if (clearedTeamPanelSessionRef.current.has(sessionId)) {
          clearedTeamPanelSessionRef.current.delete(sessionId);
        }
        useChatStore.getState().setPaused(sessionId, false);
      }
      try {
        let outgoingContent = content.replace(/\{\{skill:([^}]+)\}\}/g, '$1');
        let outgoingMediaItems: Record<string, unknown>[] | undefined;
        let outgoingFiles: Record<string, unknown> | undefined;
        if (hasMedia) {
          if (mediaItems.every(isPersistedMediaItem)) {
            outgoingMediaItems = mediaItems.map(toPersistedMediaRecord);
            outgoingFiles = buildPersistedMediaFiles(mediaItems);
          } else {
            const persisted = await persistMedia(content, sessionId, mediaItems);
            outgoingContent = persisted.content ?? persisted.query ?? content;
            outgoingMediaItems = persisted.media_items;
            outgoingFiles = persisted.files;
          }
        }
        await request('chat.send', {
          session_id: sessionId,
          content: outgoingContent,
          ...(outgoingMediaItems ? { media_items: outgoingMediaItems } : {}),
          ...(outgoingFiles ? { files: outgoingFiles } : {}),
          mode: currentMode,
          ...(selectedModel ? { model_name: selectedModel } : {}),
          ...(projectDir ? { project_dir: projectDir } : {}),
          skills: selectedSkills,
        });
        return true;
      } catch (error) {
        const webError = error as WebError;
        setConnectionStats({ lastError: webError.message });
        useChatStore.getState().setProcessing(sessionId, false);
        useChatStore.getState().setThinking(sessionId, false);
        const errorMsg = webError.message || t('network.sendMessageFailed');
        onErrorRef.current?.(errorMsg);
        useChatStore.getState().addMessage(sessionId, {
          id: `error-${Date.now()}`,
          role: 'system',
          content: t('network.errorPrefix', { message: errorMsg }),
          timestamp: new Date().toISOString(),
        });
        return false;
      }
    },
    [
      persistMedia,
      request,
      resetContextCompressionTurn,
      setContextCompressionStats,
      setConnectionStats,
      t,
    ]
  );

  const sendStructuredChatContent = useCallback(
    async (content: unknown, sessionId: string) => {
      resetContextCompressionTurn(sessionId);
      userInputVersionRef.current += 1;
      stopAllTts();

      useChatStore.getState().setProcessing(sessionId, true);
      useChatStore.getState().setThinking(sessionId, true);

      const currentSessionState = useSessionStore.getState();
      const currentSession = currentSessionState.currentSession?.session_id === sessionId
        ? currentSessionState.currentSession
        : currentSessionState.sessions.find((s) => s.session_id === sessionId);
      const projectDir = currentSession?.project_dir || undefined;
      const currentMode = currentSessionState.getRuntime(sessionId)?.mode;
      const selectedModel = currentSessionState.getRuntime(sessionId)?.selectedModelName;
      if (currentMode === 'auto_harness') {
        useHarnessStore.getState().reset(sessionId);
      }
      if (currentMode === 'team') {
        useChatStore.getState().setPaused(sessionId, false);
      }
      try {
        await request('chat.send', {
          session_id: sessionId,
          content,
          mode: currentMode,
          ...(selectedModel ? { model_name: selectedModel } : {}),
          ...(projectDir ? { project_dir: projectDir } : {}),
        });
      } catch (error) {
        const webError = error as WebError;
        setConnectionStats({ lastError: webError.message });
        useChatStore.getState().setProcessing(sessionId, false);
        useChatStore.getState().setThinking(sessionId, false);
        const errorMsg = webError.message || t('network.sendMessageFailed');
        onErrorRef.current?.(errorMsg);
        useChatStore.getState().addMessage(sessionId, {
          id: `error-${Date.now()}`,
          role: 'system',
          content: t('network.errorPrefix', { message: errorMsg }),
          timestamp: new Date().toISOString(),
        });
      }
    },
    [request, resetContextCompressionTurn, setConnectionStats, t]
  );

  // 存储sendMessage函数到ref
  useEffect(() => {
    sendMessageRef.current = sendMessage;
  }, [sendMessage]);

  // 统一中断接口 - pause/cancel/supplement/resume
  const interrupt = useCallback(
    async (
      sessionId: string,
      intent: InterruptIntent,
      options?: { newInput?: string }
    ) => {
      const newInput = options?.newInput;
      if (intent === 'supplement' && newInput) {
        resetContextCompressionTurn(sessionId);
        userInputVersionRef.current += 1;
        stopAllTts();
        if (useSessionStore.getState().getRuntime(sessionId)?.mode === 'team') {
          closeActiveTeamLeaderMessages(sessionId);
        }
        useChatStore.getState().addMessage(sessionId, {
          id: `user-${Date.now()}`,
          role: 'user',
          content: newInput,
          timestamp: new Date().toISOString(),
        });
      }
      try {
        const params: Record<string, unknown> = {
          session_id: sessionId,
          intent,
        };
        const currentMode = useSessionStore.getState().getRuntime(sessionId)?.mode;
        if (['pause', 'resume', 'cancel', 'supplement'].includes(intent)) {
          params.mode = currentMode;
          if (currentMode === 'team') {
            params.team = true;
          }
        }
        if (intent === 'supplement') {
          params.new_input = newInput ?? '';
          const selectedModel = useSessionStore.getState().getRuntime(sessionId)?.selectedModelName;
          if (selectedModel) params.model_name = selectedModel;
        }
        await request('chat.interrupt', params);
      } catch (error) {
        const webError = error as WebError;
        setConnectionStats({ lastError: webError.message });
        onErrorRef.current?.(webError.message || t('network.interruptFailed'));
      }
    },
    [
      closeActiveTeamLeaderMessages,
      request,
      resetContextCompressionTurn,
      setConnectionStats,
      t,
    ]
  );

  // 暂停 - 显式暂停当前任务
  const pause = useCallback(
    async (sessionId: string) => {
      try {
        await interrupt(sessionId, 'pause');
      } catch (error) {
        const webError = error as WebError;
        setConnectionStats({ lastError: webError.message });
        onErrorRef.current?.(webError.message || t('network.pauseFailed'));
      }
    },
    [interrupt, setConnectionStats, t]
  );

  const cancel = useCallback(
    async (sessionId: string) => {
      try {
        await interrupt(sessionId, 'cancel');
      } catch (error) {
        const webError = error as WebError;
        setConnectionStats({ lastError: webError.message });
        onErrorRef.current?.(webError.message || t('network.cancelFailed'));
      }
    },
    [interrupt, setConnectionStats, t]
  );

  const supplement = useCallback(
    async (sessionId: string, newInput: string) => {
      try {
        await interrupt(sessionId, 'supplement', { newInput });
      } catch (error) {
        const webError = error as WebError;
        setConnectionStats({ lastError: webError.message });
        onErrorRef.current?.(webError.message || t('network.supplementFailed'));
      }
    },
    [interrupt, setConnectionStats, t]
  );

  // 恢复 - 恢复暂停的任务
  const resume = useCallback(
    async (sessionId: string) => {
      try {
        await interrupt(sessionId, 'resume');
        useChatStore.getState().setPaused(sessionId, false);
      } catch (error) {
        const webError = error as WebError;
        setConnectionStats({ lastError: webError.message });
        onErrorRef.current?.(webError.message || t('network.resumeFailed'));
      }
    },
    [interrupt, setConnectionStats, t]
  );

  // 切换模式
  const switchMode = useCallback(
    async (sessionId: string, mode: AgentMode) => {
      // 标记正在切换模式
      useChatStore.getState().setSwitchingMode(sessionId, true);

      const currentMode = useSessionStore.getState().getRuntime(sessionId)?.mode;
      // Reset harnessStore when leaving auto_harness mode
      if (currentMode === 'auto_harness' && mode !== 'auto_harness') {
        useHarnessStore.getState().reset(sessionId);
      }

      // 只有在有任务执行时才调用 interrupt
      if (sessionId && sessionId !== 'new') {
        const runtime = useChatStore.getState().getRuntime(sessionId);
        if (runtime?.isProcessing || runtime?.isPaused) {
          try {
            await interrupt(sessionId, 'cancel');
          } catch {
            // 忽略中断错误
          }
        }
      }

      useSessionStore.getState().setMode(sessionId, mode);
      if (sessionId && sessionId !== 'new') {
        updateSession(sessionId, { mode });
      }
      // 延迟重置标志
      setTimeout(() => {
        useChatStore.getState().setSwitchingMode(sessionId, false);
      }, 300);
    },
    [updateSession, interrupt]
  );

  // 发送用户回答
  const sendUserAnswer = useCallback(
    async (sessionId: string, requestId: string, answers: UserAnswer[], source?: string) => {
      try {
        const pendingQuestion = useChatStore.getState().getRuntime(sessionId)?.pendingQuestion;
        const pendingMatches = pendingQuestion?.request_id === requestId;
        const effectiveSource = source ?? (pendingMatches ? pendingQuestion?.source : undefined);
        const approvalSchema =
          pendingMatches
            ? pendingQuestion?.approvalSchema
            : undefined;
        const evolutionMeta =
          pendingMatches
            ? pendingQuestion.evolutionMeta
            : undefined;
        const evolutionMetaPayload =
          evolutionMeta && typeof evolutionMeta === 'object'
            ? { evolution_meta: evolutionMeta }
            : {};
        const approvalSchemaPayload = approvalSchema ? { approval_schema: approvalSchema } : {};
        const sourcePayload = effectiveSource ? { source: effectiveSource } : {};
        const structuredPlanPayload =
          pendingMatches && pendingQuestion?.planApprovalKind === 'plan_approval'
            ? {
                plan_approval_kind: pendingQuestion.planApprovalKind,
                plan_content: pendingQuestion.planContent ?? '',
                plan_language: pendingQuestion.planLanguage ?? 'cn',
              }
            : {};
        const approvalTransport =
          evolutionMeta && typeof evolutionMeta.approval_transport === 'string'
            ? evolutionMeta.approval_transport
            : undefined;
        // 如果是需要走 interrupt/interact 的确认，发送 chat.send
        if (
          effectiveSource === 'permission_interrupt' ||
          effectiveSource === 'confirm_interrupt' ||
          effectiveSource === 'ask_user_interrupt' ||
          effectiveSource === 'evolution_interrupt' ||
          (effectiveSource === 'skill_evolution_approval' && approvalTransport === 'interrupt')
        ) {
          const resolvedResumeMode = resolveInterruptResumeMode(sessionId);
          await request('chat.send', {
            session_id: sessionId,
            query: '',
            mode: resolvedResumeMode,
            request_id: requestId,
            answers: answers,
            ...sourcePayload,
            ...structuredPlanPayload,
            ...approvalSchemaPayload,
            ...evolutionMetaPayload,
          });
        } else if (effectiveSource === 'activate_confirm') {
          const action = answers[0]?.selected_options[0] === '拒绝' ? 'reject' : 'accept';
          const interactionId = requestId || useHarnessStore.getState().getRuntime(sessionId)?.activateInteraction?.interactionId || '';
          if (!interactionId) {
            throw new Error('missing activate interaction id');
          }
          await request('chat.send', {
            session_id: sessionId,
            content: '',
            mode: 'auto_harness',
            activate_response: {
              interaction_id: interactionId,
              action,
              feedback: '',
            },
          });
          useHarnessStore.getState().setActivateInteraction(sessionId, null);
        } else {
          // 否则发送 chat.user_answer（自进化确认）
          await request('chat.user_answer', {
            session_id: sessionId,
            request_id: requestId,
            answers,
            ...sourcePayload,
            ...approvalSchemaPayload,
            ...evolutionMetaPayload,
          });
        }
        useChatStore.getState().setPendingQuestion(sessionId, null);
      } catch (error) {
        const webError = error as WebError;
        setConnectionStats({ lastError: webError.message });
        onErrorRef.current?.(webError.message || t('network.submitAnswerFailed'));
      }
    },
    [request, setConnectionStats, t]
  );

  const respondActivate = useCallback(
    async (sessionId: string, interactionId: string, action: 'accept' | 'reject', feedback?: string) => {
      try {
        await request('chat.send', {
          session_id: sessionId,
          content: '',
          mode: 'auto_harness',
          activate_response: {
            interaction_id: interactionId,
            action,
            feedback: feedback || '',
          },
        });
        useHarnessStore.getState().setActivateInteraction(sessionId, null);
      } catch (error) {
        const webError = error as WebError;
        setConnectionStats({ lastError: webError.message });
      }
    },
    [request, setConnectionStats]
  );

  const revealPendingContextUsage = useCallback((sessionId: string) => {
    heldContextUsageSessionsRef.current.delete(sessionId);
    const pending = pendingContextUsageRef.current.get(sessionId);
    pendingContextUsageRef.current.delete(sessionId);
    if (pending) {
      setContextCompressionStats(sessionId, pending);
    }
  }, [setContextCompressionStats]);

  // 会话切换时不再重置上下文压缩信息，保持本地存储的状态
  // useEffect(() => {
  //   setContextCompressionStats(null);
  // }, [activeSessionId, setContextCompressionStats]);

  useEffect(() => {
    onConnectRef.current = onConnect;
    onDisconnectRef.current = onDisconnect;
    onErrorRef.current = onError;
    onConfigChangedRef.current = onConfigChanged;
  }, [onConfigChanged, onConnect, onDisconnect, onError]);

  const shouldDropDuplicatedEvent = useCallback(
    (eventName: string, payload: Record<string, unknown>): boolean => {
      const now = Date.now();
      const dedupKey = makeEventDedupKey(eventName, payload);
      const recent = recentEventRef.current;
      const lastSeen = recent.get(dedupKey);
      recent.set(dedupKey, now);

      // 控制 map 大小，避免长期运行后无限增长
      if (recent.size > 400) {
        for (const [key, ts] of recent) {
          if (now - ts > EVENT_DEDUP_WINDOW_MS * 6) {
            recent.delete(key);
          }
        }
      }

      const dropped = lastSeen != null && now - lastSeen <= EVENT_DEDUP_WINDOW_MS;
      if (dropped && import.meta.env.DEV) {
        const nextCount = (eventDedupDroppedRef.current[eventName] || 0) + 1;
        eventDedupDroppedRef.current[eventName] = nextCount;
        if (nextCount === 1 || nextCount % 10 === 0) {
          console.debug('[ws][metrics] eventDedupDropped', {
            eventName,
            count: nextCount,
          });
        }
      }
      return dropped;
    },
    []
  );

  const clearThinkingForVisibleOutput = useCallback((sessionId: string) => {
    const currentMode = useSessionStore.getState().getRuntime(sessionId)?.mode;
    const isProcessingNow = useChatStore.getState().getRuntime(sessionId)?.isProcessing;
    if (currentMode === 'auto_harness' && isProcessingNow) {
      return;
    }
    useChatStore.getState().setThinking(sessionId, false);
  }, []);

  const shouldRecoverProcessingFromReasoning = useCallback((sessionId: string, payload: Record<string, unknown>): boolean => {
    const runtime = useChatStore.getState().getRuntime(sessionId);
    if (!runtime || runtime.isProcessing || runtime.isLoadingHistory) {
      return false;
    }
    if (runtime.currentStreamId) {
      return true;
    }
    if (webClient.getInflightCount() > 0) {
      return true;
    }
    const payloadRequestId = getPayloadRequestId(payload);
    return Boolean(
      payloadRequestId &&
      activeRequestIdRef.current &&
      payloadRequestId === activeRequestIdRef.current
    );
  }, []);

  const getTeamMemberOutputKey = useCallback(
    (payload: Record<string, unknown>, memberId: string): string => stableEventId(
      'member-output-key',
      getPayloadSessionId(payload),
      memberId,
      payload.rid,
      payload.request_id
    ),
    []
  );

  const getOrCreateTeamMemberOutputEventId = useCallback(
    (payload: Record<string, unknown>, memberId: string): string => {
      const key = getTeamMemberOutputKey(payload, memberId);
      const existing = teamMemberOutputEventRef.current.get(key);
      if (existing) {
        return existing;
      }
      const id = stableEventId(
        'member-output',
        getPayloadSessionId(payload),
        memberId,
        payload.rid,
        payload.request_id,
        Date.now()
      );
      teamMemberOutputEventRef.current.set(key, id);
      return id;
    },
    [getTeamMemberOutputKey]
  );

  const takeTeamMemberOutputEventId = useCallback(
    (payload: Record<string, unknown>, memberId: string): string | undefined => {
      const key = getTeamMemberOutputKey(payload, memberId);
      const id = teamMemberOutputEventRef.current.get(key);
      if (id) {
        teamMemberOutputEventRef.current.delete(key);
      }
      return id;
    },
    [getTeamMemberOutputKey]
  );

  const appendTeamMemberOutputDelta = useCallback(
    (sessionId: string, payload: Record<string, unknown>, memberId: string, content: string) => {
      if (!content) {
        return;
      }
      const id = getOrCreateTeamMemberOutputEventId(payload, memberId);
      const existingContent =
        useSessionStore.getState().getRuntime(sessionId)?.teamMemberExecutionEvents.find((event) => event.id === id)?.content || '';
      useSessionStore.getState().addTeamMemberExecutionEvent(sessionId, {
        id,
        member_id: memberId,
        kind: 'final',
        timestamp: eventTimestampMs(payload),
        title: t('team.process.execution.final'),
        content: `${existingContent}${content}`,
      });
    },
    [getOrCreateTeamMemberOutputEventId, t]
  );

  useEffect(() => {
    const applyTeamMemberShutdown = (memberId: string, sessionId?: string) => {
      const normalizedMemberId = memberId.trim();
      if (!normalizedMemberId) {
        return;
      }
      if (!sessionId) {
        return;
      }
      const sessionStore = useSessionStore.getState();
      const runtime = sessionStore.getRuntime(sessionId);
      const currentMembers = runtime?.teamMembers ?? [];
      const nextMembers = currentMembers.filter(
        (member) => member.member_id !== normalizedMemberId
      );
      if (nextMembers.length === currentMembers.length) {
        return;
      }
      clearPendingTeamMemberContextCompressionStart(sessionId, normalizedMemberId);
      clearTeamMemberContextCompressionStatus(sessionId, normalizedMemberId);
      sessionStore.setTeamMembers(sessionId, nextMembers);
      if (nextMembers.length === 0) {
        clearedTeamPanelSessionRef.current.add(sessionId);
        useTodoStore.getState().clearTodos(sessionId);
        const currentSessionStore = useSessionStore.getState();
        currentSessionStore.setTeamMembers(sessionId, []);
        currentSessionStore.setTeamTaskEvents(sessionId, []);
        currentSessionStore.setTeamHumanShareCommands(sessionId, []);
        currentSessionStore.setTeamTasks(sessionId, []);
        currentSessionStore.setTeamMemberExecutionEvents(sessionId, []);
        clearAllTeamMemberContextCompressionStatus(sessionId);
        currentSessionStore.setTeamHistoryMessages(sessionId, []);
      }
    };

    const isTeamPanelClearedForPayload = (payload: Record<string, unknown>) => {
      const sessionId = getPayloadSessionId(payload) || undefined;
      return Boolean(sessionId && clearedTeamPanelSessionRef.current.has(sessionId));
    };

    const unsubs = [
      webClient.on('connection.ack', ({ payload }) => {
        handleConnectionAck(payload);
      }),
      webClient.on('hello', ({ payload }) => {
        handleConnectionAck(payload);
      }),
      webClient.on('chat.delta', ({ payload }) => {
          const sessionId = resolveEventSessionId(payload);
          if (!sessionId) return;

        // 页面刷新后，如果收到活跃事件但 isProcessing=false，自动恢复执行状态
        if (!useChatStore.getState().getRuntime(sessionId)?.isProcessing && !useChatStore.getState().getRuntime(sessionId)?.isLoadingHistory) {
          useChatStore.getState().setProcessing(sessionId, true);
        }

        const currentMode = useSessionStore.getState().getRuntime(sessionId)?.mode;
        const content = typeof payload.content === 'string' ? payload.content : '';

        if (isHiddenTeamTeammateMessagePayload(currentMode ?? 'agent', payload)) {
          const memberId = getTeamPayloadMemberName(payload);
          if (memberId) {
            appendTeamMemberOutputDelta(sessionId, payload, memberId, content);
          }
          return;
        }
        if (content) {
          revealPendingContextUsage(sessionId);
        }
        if (currentMode === 'team' && content) {
          clearThinkingForVisibleOutput(sessionId);
          const existingMsg = findActiveTeamLeaderMessage(sessionId);

          if (existingMsg) {
            const existingContent = existingMsg.content || '';
            const newContent = existingContent + content;
            const updatePayload: { content: string; isStreaming?: boolean } = { content: newContent };
            if (content.includes('MEDIA:')) {
              updatePayload.isStreaming = false;
            }
            useChatStore.getState().updateMessage(sessionId, existingMsg.id, updatePayload);
          } else {
            const msgId = `team-leader-${Date.now()}`;
            useChatStore.getState().addMessage(sessionId, {
              id: msgId,
              role: 'system',
              content: content,
              timestamp: new Date().toISOString(),
              isStreaming: true,
            });
          }
          return;
        }

        const currentStreamId = useChatStore.getState().getRuntime(sessionId)?.currentStreamId;
        clearThinkingForVisibleOutput(sessionId);
        if (!currentStreamId && content) {
          const assistantMsgId = `assistant-${Date.now()}`;
          useChatStore.getState().addMessage(sessionId, {
            id: assistantMsgId,
            role: 'assistant',
            content: '',
            timestamp: new Date().toISOString(),
            isStreaming: true,
          });
          useChatStore.getState().startStreaming(sessionId, assistantMsgId);
        }
        useChatStore.getState().appendStreamContent(sessionId, content);
      }),
      webClient.on('chat.reasoning', ({ payload }) => {
        const sessionId = resolveEventSessionId(payload);
        if (!sessionId) return;

        // 只在明确属于当前活跃请求时恢复 processing，避免 evolution 后置 reasoning
        // 把已完成会话重新拉回处理中。
        if (shouldRecoverProcessingFromReasoning(sessionId, payload)) {
          useChatStore.getState().setProcessing(sessionId, true);
        }
      }),
      webClient.on('chat.final', ({ payload }) => {
        if (shouldDropDuplicatedEvent('chat.final', payload)) return;

        const cronMeta = payload.cron as Record<string, unknown> | undefined;

        // cron 广播处理：结果到达时刷新定时任务会话列表（在 sessionId 路由之前，确保无论如何都刷新）
        if (cronMeta && typeof cronMeta === 'object') {
          const cronJobId = typeof cronMeta.job_id === 'string' ? cronMeta.job_id.trim() : '';
          const cronStatus = typeof cronMeta.status === 'string' ? cronMeta.status.trim() : '';
          if (cronJobId && cronStatus !== 'running') {
            const cronJob = useCronStore.getState().jobs.find((j) => j.id === cronJobId);
            const cronProjectId = cronJob?.project_id || 'default';
            void useCronStore.getState().loadCronSessions(cronProjectId, cronJobId);
          }
        }

        let sessionId = resolveEventSessionId(payload);
        // cron 广播 session_id 为空（后端对 web 通道置空），
        // 优先使用 cronMeta.exec_session_id 路由到定时任务专属会话，
        // 不再 fallback 到当前活跃会话，避免广播消息污染创建者会话。
        if (!sessionId && cronMeta) {
          const execSessionId =
            typeof cronMeta.exec_session_id === 'string'
              ? (cronMeta.exec_session_id as string).trim()
              : '';
          if (execSessionId) {
            sessionId = execSessionId;
            ensureSessionRuntimes(sessionId);
          }
        }
        if (!sessionId) return;

        const memberAction = pickString(payload.member_action);
        const actionMemberName = pickString(payload.member_name);
        if (
          actionMemberName &&
          (memberAction === 'joined' || memberAction === 'left')
        ) {
          // 使用 upsert（若 spawned 事件尚未到达则创建占位，后续 spawned 事件会补全 teamName/sessionRef 等字段）
          useSessionStore.getState().upsertTeamHumanShareCommand(
            sessionId,
            {
              memberName: actionMemberName,
              displayName: pickString(payload.display_name),
              sessionId,
              teamName: '',
              sessionRef: '',
              joinCommand: '',
              exitCommand: '',
              status: memberAction === 'joined' ? 'joined' : 'left',
              sourceChannel: pickString(payload.source_channel),
              userId: pickString(payload.user_id),
              updatedAt: Date.now(),
            },
          );
          const content = normalizeFinalContent(payload);
          if (content) {
            useChatStore.getState().addMessage(sessionId, {
              id: `team-human-${memberAction}-${Date.now()}`,
              role: 'system',
              content,
              timestamp: normalizeEventTimestampIso(payload.timestamp),
            });
          }
          return;
        }

        const currentMode = useSessionStore.getState().getRuntime(sessionId)?.mode;
        const content = normalizeFinalContent(payload);
        finishContextCompressionTurn(sessionId);

        if (typeof payload.source === 'string' && payload.source === 'proactive_notification') {
          if (content) {
            useHarnessStore.getState().setProactiveNotification(content);
          }
          return;
        }

        // team 模式下，过滤成员输出，只保留外层 leader 回复。
        if (isHiddenTeamTeammateMessagePayload(currentMode ?? 'agent', payload)) {
          const memberId = getTeamPayloadMemberName(payload);
          if (memberId) {
            const timestamp = eventTimestampMs(payload);
            const outputEventId = takeTeamMemberOutputEventId(payload, memberId);
            if (!content.trim()) {
              return;
            }
            useSessionStore.getState().addTeamMemberExecutionEvent(sessionId, {
              id: outputEventId || stableEventId('final', payload.session_id, memberId, payload.rid, timestamp, content.slice(0, 48)),
              member_id: memberId,
              kind: 'final',
              timestamp,
              title: t('team.process.execution.final'),
              content,
            });
          }
          return;
        }
        const teamLeaderMessageToFinalize =
          currentMode === 'team' && content
            ? findActiveTeamLeaderMessage(sessionId)
            : undefined;
        // Defensive: chat.final is the definitive end-of-response marker.
        // The primary state change is driven by chat.processing_status
        // (is_processing=false), but if that frame is lost the UI would be stuck
        // showing the stop button.
        // In team mode the backend suppresses chat.final while the team is
        // still running and only sends chat.processing_status(is_complete=true)
        // on team.completed, so we must NOT reset isProcessing here.
        if (!useChatStore.getState().getRuntime(sessionId)?.isLoadingHistory) {
          useChatStore.getState().setExecutionError(sessionId, null);
          if (currentMode !== 'team') {
            useChatStore.getState().setProcessing(sessionId, false);
          }
          useChatStore.getState().setThinking(sessionId, false);
          useChatStore.getState().clearSubtasks(sessionId);
        }
        if (content) {
          revealPendingContextUsage(sessionId);
        }
        if (currentMode === 'team' && content) {
          clearThinkingForVisibleOutput(sessionId);
          const timestamp = payload.timestamp || Date.now();

          if (teamLeaderMessageToFinalize) {
            useChatStore.getState().updateMessage(sessionId, teamLeaderMessageToFinalize.id, {
              content: `team.leader:${JSON.stringify({ content, timestamp })}`,
              isStreaming: false,
              timestamp: normalizeEventTimestampIso(payload.timestamp),
            });
            return;
          }
          useChatStore.getState().addMessage(sessionId, {
            id: `team-leader-${Date.now()}`,
            role: 'system',
            content: `team.leader:${JSON.stringify({ content, timestamp })}`,
            timestamp: new Date().toISOString(),
          });
          return;
        }

        const runtime = useChatStore.getState().getRuntime(sessionId);
        const currentStreamId = runtime?.currentStreamId;
        const messages = runtime?.messages ?? [];
        const payloadSessionId =
          typeof payload.session_id === 'string' ? payload.session_id.trim() : '';

        // 检查是否为主动推荐消息
        const source = typeof payload.source === 'string' ? payload.source : '';
        const isProactiveRecommendation = source === 'proactive_recommendation';
        const proactiveType = typeof payload.proactive_type === 'string' ? payload.proactive_type : '';

        // 仅当有明确会话绑定时才把 final 合并进当前流式气泡。
        // 定时任务等广播的 session_id 为空/null，若仍走 currentStreamId 会写到错误气泡甚至”无可见更新”。
        const streamId = currentStreamId;
        if (streamId && payloadSessionId) {
          useChatStore.getState().updateMessage(sessionId, streamId, {
            ...(content ? { content } : {}),
            isStreaming: false,
            ...(isProactiveRecommendation ? { isProactiveRecommendation, ...(proactiveType ? { proactiveType: proactiveType as 'skill_recommend' | 'task_reminder' | 'need_exploration' } : {}) } : {}),
          });
          useChatStore.getState().stopStreaming(sessionId);
          if (content && !content.includes('MEDIA:')) {
            handleTtsPlayback(sessionId, streamId, content);
          }
          return;
        }
        if (content) {
          const cronMeta = payload.cron as Record<string, unknown> | undefined;
          const cronRunId =
            typeof cronMeta?.run_id === 'string' ? cronMeta.run_id.trim() : '';
          const isCronPlaceholderContent =
            cronMeta?.is_placeholder === true ||
            /正在执行中，结果稍后补发/.test(content) ||
            /^\[cron\].*正在执行中/.test(content);

          // 正式结果：替换同 run_id 的占位气泡，或最近的定时任务「正在执行中」占位
          if (!isCronPlaceholderContent) {
            let placeholderId: string | null = null;
            if (cronRunId) {
              const byRun = messages.find((m) => m.id === `cron-placeholder-${cronRunId}`);
              if (byRun) placeholderId = byRun.id;
            }
            if (!placeholderId) {
              for (let i = messages.length - 1; i >= 0; i -= 1) {
                const msg = messages[i];
                if (msg.role !== 'assistant' || typeof msg.content !== 'string') continue;
                if (
                  /正在执行中，结果稍后补发/.test(msg.content) ||
                  /^\[cron\].*正在执行中/.test(msg.content)
                ) {
                  placeholderId = msg.id;
                  break;
                }
              }
            }
            if (placeholderId) {
              useChatStore.getState().updateMessage(sessionId, placeholderId, { content, isStreaming: false });
              if (!content.includes('MEDIA:')) {
                handleTtsPlayback(sessionId, placeholderId, content);
              }
              return;
            }
          }

          const messageId =
            isCronPlaceholderContent && cronRunId
              ? `cron-placeholder-${cronRunId}`
              : cronRunId && !isCronPlaceholderContent
                ? `cron-final-${cronRunId}`
                : `msg-${Date.now()}`;

          const existing = messages.find((m) => m.id === messageId);
          if (existing) {
            if (existing.content === content) {
              return;
            }
            useChatStore.getState().updateMessage(sessionId, messageId, { content, isStreaming: false });
            if (!content.includes('MEDIA:')) {
              handleTtsPlayback(sessionId, messageId, content);
            }
            return;
          }

          // 去重：若上一条已是相同内容的助手消息（同一回复被收到两次），不再追加
          const last = messages[messages.length - 1];
          if (last?.role === 'assistant' && last.content === content) {
            return;
          }
          useChatStore.getState().addMessage(sessionId, {
            id: messageId,
            role: 'assistant',
            content,
            timestamp: new Date().toISOString(),
            isProactiveRecommendation,
            ...(proactiveType ? { proactiveType: proactiveType as 'skill_recommend' | 'task_reminder' | 'need_exploration' } : {}),
          });
          if (!content.includes('MEDIA:')) {
            handleTtsPlayback(sessionId, messageId, content);
          }
        }
      }),
      webClient.on('chat.media', ({ payload }) => {
        const sessionId = resolveEventSessionId(payload);
        if (!sessionId) return;
        const mediaPayload = payload as {
          content?: string;
          media_items?: MediaItem[];
        };
        const runtime = useChatStore.getState().getRuntime(sessionId);
        const currentStreamId = runtime?.currentStreamId;
        const messages = runtime?.messages ?? [];
        const targetId =
          currentStreamId ??
          [...messages].reverse().find((msg) => msg.role === 'assistant')?.id;
        if (!targetId) {
          return;
        }
        const updates: { content?: string; mediaItems?: MediaItem[] } = {};
        if (mediaPayload.content !== undefined) {
          updates.content = mediaPayload.content;
        }
        if (mediaPayload.media_items?.length) {
          updates.mediaItems = mediaPayload.media_items;
        }
        if (Object.keys(updates).length > 0) {
          useChatStore.getState().updateMessage(sessionId, targetId, updates);
        }
        if (mediaPayload.content) {
          handleTtsPlayback(sessionId, targetId, mediaPayload.content);
        }
      }),
      webClient.on('chat.file', ({ payload }) => {
        const sessionId = resolveEventSessionId(payload);
        if (!sessionId) return;
        const files = (payload.files ?? []) as FileDownloadItem[];
        if (!files.length) return;
        const currentMode = useSessionStore.getState().getRuntime(sessionId)?.mode;
        if (isHiddenTeamTeammateMessagePayload(currentMode ?? 'agent', payload)) {
          const memberId = getTeamPayloadMemberName(payload);
          if (memberId) {
            const timestamp = eventTimestampMs(payload);
            useSessionStore.getState().addTeamMemberExecutionEvent(sessionId, {
              id: stableEventId('file', payload.session_id, memberId, timestamp, files.map((file) => file.name).join(',')),
              member_id: memberId,
              kind: 'file',
              timestamp,
              title: t('team.process.execution.sentFile'),
              content: files.map((file) => file.name).join('\n'),
              files: files.map((file) => ({
                name: file.name,
                size: file.size,
                mime_type: file.mime_type,
                download_url: file.download_url,
              })),
            });
          }
          return;
        }
        if (currentMode === 'team') {
          const target = findActiveTeamLeaderMessage(sessionId);
          if (target) {
            useChatStore.getState().updateMessage(sessionId, target.id, {
              fileItems: [...(target.fileItems || []), ...files],
            });
          } else {
            useChatStore.getState().addMessage(sessionId, {
              id: `team-leader-${Date.now()}`,
              role: 'system',
              content: '',
              timestamp: new Date().toISOString(),
              isStreaming: true,
              fileItems: files,
            });
          }
          return;
        }
        useChatStore.getState().addFileItems(sessionId, files);
      }),
      webClient.on('chat.tool_call', ({ payload }) => {
        const sessionId = resolveEventSessionId(payload);
        if (!sessionId) return;
        if (shouldDropDuplicatedEvent('chat.tool_call', payload)) return;
        // 页面刷新后，如果收到活跃事件但 isProcessing=false，自动恢复执行状态
        if (!useChatStore.getState().getRuntime(sessionId)?.isProcessing && !useChatStore.getState().getRuntime(sessionId)?.isLoadingHistory) {
          useChatStore.getState().setProcessing(sessionId, true);
        }
        const currentMode = useSessionStore.getState().getRuntime(sessionId)?.mode;
        clearThinkingForVisibleOutput(sessionId);
        const toolCall = normalizeToolCallPayload(payload);
        const shutdownMemberId = getShutdownMemberFromToolCall(toolCall);
        if (shutdownMemberId) {
          shutdownMemberToolCallRef.current.set(toolCall.id, shutdownMemberId);
        }
        if (isHiddenTeamTeammateMessagePayload(currentMode ?? 'agent', payload)) {
          if (currentMode === 'team' && !isTeamPanelClearedForPayload(payload)) {
            applyTeamTaskToolCall(sessionId, toolCall);
          }
          const memberId = getTeamPayloadMemberName(payload) || toolCall.memberName;
          if (memberId) {
            teamToolCallMemberRef.current.set(toolCall.id, memberId);
            const timestamp = eventTimestampMs(payload);
            useSessionStore.getState().addTeamMemberExecutionEvent(sessionId, {
              id: stableEventId('tool-call', payload.session_id, memberId, toolCall.id, timestamp),
              member_id: memberId,
              kind: 'tool_call',
              timestamp,
              title: t('team.process.execution.toolCallTitle', { tool: toolCall.name }),
              content: toolCall.description || toolCall.formatted_args || stringifyCompact(toolCall.arguments),
              tool_name: toolCall.name,
              tool_call_id: toolCall.id,
            });
          }
          return;
        }
        const runtime = useChatStore.getState().getRuntime(sessionId);
        const currentStreamId = runtime?.currentStreamId;
        const messages = runtime?.messages ?? [];
        const toolRequestId = getPayloadRequestId(payload) || activeRequestIdRef.current;
        const currentStreamMessage =
          currentMode === 'team'
            ? findActiveTeamLeaderMessage(sessionId)
            : currentStreamId
              ? messages.find((msg) => msg.id === currentStreamId)
              : undefined;
        useChatStore.getState().addToolCall(
          sessionId,
          toolCall,
          currentStreamMessage?.timestamp
            ? { startedAt: currentStreamMessage.timestamp, requestId: toolRequestId }
            : { requestId: toolRequestId }
        );
        if (currentMode === 'team' && !isTeamPanelClearedForPayload(payload)) {
          applyTeamTaskToolCall(sessionId, toolCall);
        }
      }),
      webClient.on('chat.tool_result', ({ payload }) => {
        const sessionId = resolveEventSessionId(payload);
        if (!sessionId) return;
        if (shouldDropDuplicatedEvent('chat.tool_result', payload)) return;
        const currentMode = useSessionStore.getState().getRuntime(sessionId)?.mode;
        const toolResult = normalizeToolResultPayload(payload);
        const activeSessionId = getPayloadSessionId(payload) || undefined;
        const shutdownMemberId =
          (toolResult.toolCallId
            ? shutdownMemberToolCallRef.current.get(toolResult.toolCallId)
            : undefined) ||
          getShutdownMemberFromToolResult(toolResult);
        if (isHiddenTeamTeammateMessagePayload(currentMode ?? 'agent', payload)) {
          const memberId =
            getTeamPayloadMemberName(payload) ||
            (toolResult.toolCallId ? teamToolCallMemberRef.current.get(toolResult.toolCallId) : undefined);
          if (memberId) {
            const timestamp = eventTimestampMs(payload);
            useSessionStore.getState().addTeamMemberExecutionEvent(sessionId, {
              id: stableEventId('tool-result', payload.session_id, memberId, toolResult.toolCallId, timestamp),
              member_id: memberId,
              kind: 'tool_result',
              timestamp,
              title: t('team.process.execution.toolResultTitle', { tool: toolResult.toolName }),
              content: toolResult.summary || stringifyCompact(toolResult.result),
              tool_name: toolResult.toolName,
              tool_call_id: toolResult.toolCallId,
            });
          }
          if (shutdownMemberId) {
            if (toolResult.toolCallId) {
              shutdownMemberToolCallRef.current.delete(toolResult.toolCallId);
            }
            applyTeamMemberShutdown(
              shutdownMemberId,
              activeSessionId
            );
          }
          return;
        }
        if (shutdownMemberId) {
          if (toolResult.toolCallId) {
            shutdownMemberToolCallRef.current.delete(toolResult.toolCallId);
          }
          applyTeamMemberShutdown(
            shutdownMemberId,
            activeSessionId
          );
        }
        useChatStore.getState().addToolResult(sessionId, toolResult);
      }),
      webClient.on('todo.updated', ({ payload }) => {
        const sessionId = resolveEventSessionId(payload);
        if (!sessionId) return;
        if (shouldDropDuplicatedEvent('todo.updated', payload)) return;
        if (isTeamPanelClearedForPayload(payload)) {
          return;
        }
        const todos = Array.isArray(payload.todos) ? payload.todos : [];
        useTodoStore.getState().setTodos(sessionId, todos as Parameters<ReturnType<typeof useTodoStore.getState>['setTodos']>[1]);
      }),
      webClient.on('context.usage', ({ payload }) => {
        const sessionId = resolveEventSessionId(payload);
        if (!sessionId) return;
        const currentMode = useSessionStore.getState().getRuntime(sessionId)?.mode;
        if (isHiddenTeamTeammateMessagePayload(currentMode ?? 'agent', payload)) return;
        const rate =
          typeof payload.rate === 'number' ? payload.rate : 0;
        const contextMax =
          typeof payload.context_max === 'number' && Number.isFinite(payload.context_max)
            ? payload.context_max
            : null;
        const tokensUsed =
          typeof payload.tokens_used === 'number' && Number.isFinite(payload.tokens_used)
            ? payload.tokens_used
            : null;
        const stats = { rate, beforeCompressed: contextMax, afterCompressed: tokensUsed };
        if (heldContextUsageSessionsRef.current.has(sessionId)) {
          pendingContextUsageRef.current.set(sessionId, stats);
          setContextCompressionStats(sessionId, {
            rate: 0,
            beforeCompressed: 0,
            afterCompressed: 0,
          });
        } else {
          setContextCompressionStats(sessionId, stats);
        }
        console.debug('[ws] context.usage', {
          session_id: payload.session_id,
          rate,
          context_max: contextMax,
          tokens_used: tokensUsed,
        });
      }),
      webClient.on<ContextCompressionStatePayload>(
        'context.compression_state',
        ({ payload }) => {
          const sessionId = resolveEventSessionId(payload);
          if (!sessionId) return;
          const memberId = findExistingTeamMemberId(sessionId, payload.member_name);
          if (memberId) {
            handleTeamMemberContextCompressionState(sessionId, payload, memberId);
            return;
          }
          handleContextCompressionState(sessionId, payload);
        }
      ),
      webClient.on('heartbeat.relay', ({ payload }) => {
        const heartbeatText =
          typeof payload.heartbeat === 'string' ? payload.heartbeat : '';
        // 只要成功收到 relay 即表示已成功发到前端，始终为 ok，不存在 alert
        setHeartbeatStatus(
          'ok',
          heartbeatText || null,
          new Date().toISOString()
        );
      }),
      webClient.on('session.updated', ({ payload }) => {
        const sessionId =
          typeof payload.session_id === 'string' ? payload.session_id : '';
        if (!sessionId) return;
        updateSession(sessionId, payload as Partial<Session>);
        useWorkspaceStore.getState().patchSession(sessionId, payload as Partial<Session>);
        if (typeof payload.mode === 'string') {
          useSessionStore.getState().setMode(sessionId, normalizeAgentMode(payload.mode));
        }
      }),
      webClient.on('chat.processing_status', ({ payload }) => {
        const sessionId = resolveEventSessionId(payload);
        if (!sessionId) return;
        if (shouldDropDuplicatedEvent('chat.processing_status', payload)) return;
        // 切换模式时忽略处理状态更新
        if (useChatStore.getState().getRuntime(sessionId)?.switchingMode) return;
        // 加载历史消息时忽略处理状态更新
        if (useChatStore.getState().getRuntime(sessionId)?.isLoadingHistory) return;
        const isProcessingNow = Boolean(payload.is_processing);
        // 如果 interrupt_result 指示任务已完成，忽略 processing_status=true
        const interruptResult = useChatStore.getState().getRuntime(sessionId)?.interruptResult;
        const resumeAlreadyCompleted = isCompletedResumeResult(interruptResult);
        if (isProcessingNow && resumeAlreadyCompleted) {
          return;
        }
        if (isProcessingNow && useChatStore.getState().getRuntime(sessionId)?.isPaused) {
          return;
        }
        useChatStore.getState().setProcessing(sessionId, isProcessingNow);
        const sessionPatch: Partial<Session> = {
          is_processing: isProcessingNow,
          updated_at: new Date().toISOString(),
        };
        updateSession(sessionId, sessionPatch);
        useWorkspaceStore.getState().patchSession(sessionId, sessionPatch);
        if (!isProcessingNow) {
          useChatStore.getState().setThinking(sessionId, false);
          useChatStore.getState().clearSubtasks(sessionId);
          useChatStore.getState().stopStreaming(sessionId);

          // 检查是否有等待的任务队列
          const currentMode = useSessionStore.getState().getRuntime(sessionId)?.mode;
          const runtime = useChatStore.getState().getRuntime(sessionId);
          const taskQueue = runtime?.taskQueue ?? [];
          const queuePaused = runtime?.queuePaused ?? false;
          if (
            currentMode === 'agent' &&
            !resumeAlreadyCompleted &&
            !queuePaused &&
            taskQueue.length > 0
          ) {
            // 智能执行/单Agent模式下，自动处理队列中的下一个任务
            const nextTask = taskQueue[0];
            if (nextTask && sendMessageRef.current) {
              // 从队列中移除该任务
              useChatStore.getState().removeFromTaskQueue(sessionId, nextTask.id);
              // 发送下一个任务
              sendMessageRef.current(nextTask.content, sessionId);
            }
          }
        }
      }),
      webClient.on('chat.symphony_status', ({ payload }) => {
        const sessionId = resolveEventSessionId(payload);
        if (!sessionId) return;
        const content = typeof payload.content === 'string' ? payload.content.trim() : '';
        if (!content) return;
        const operationId =
          typeof payload.operation_id === 'string' && payload.operation_id.trim()
            ? payload.operation_id.trim()
            : typeof payload.request_id === 'string' && payload.request_id.trim()
              ? payload.request_id.trim()
              : `${Date.now()}`;
        const messageId = `symphony-status-${operationId}`;
        const status = typeof payload.status === 'string' ? payload.status : '';
        const detail = typeof payload.detail === 'string' ? payload.detail.trim() : '';
        const displayContent =
          status === 'failed' && detail && !content.includes(detail)
            ? `${content}\n${detail}`
            : content;
        const chatState = useChatStore.getState();
        const messages = chatState.getRuntime(sessionId)?.messages ?? [];
        const cachedTarget = symphonyStatusTargetRef.current.get(operationId);
        const targetMessage = cachedTarget
          ? messages.find((message) => message.id === cachedTarget.messageId)
          : [...messages].reverse().find(
            (message) =>
              message.role === 'assistant' ||
              (message.role === 'system' && message.id?.startsWith('team-leader-'))
          );
        if (targetMessage) {
          const target = cachedTarget || {
            messageId: targetMessage.id,
            baseContent: targetMessage.content || '',
          };
          symphonyStatusTargetRef.current.set(operationId, target);
          const baseContent = target.baseContent.trimEnd();
          chatState.updateMessage(sessionId, target.messageId, {
            content: baseContent ? `${baseContent}\n\n${displayContent}` : displayContent,
            timestamp: new Date().toISOString(),
          });
          return;
        }
        const existing = messages.find((message) => message.id === messageId);
        if (existing) {
          chatState.updateMessage(sessionId, messageId, {
            content: displayContent,
            timestamp: new Date().toISOString(),
          });
          return;
        }
        chatState.addMessage(sessionId, {
          id: messageId,
          role: 'system',
          content: displayContent,
          timestamp: new Date().toISOString(),
        });
      }),
      webClient.on('chat.evolution_status', ({ payload }) => {
        const sessionId = resolveEventSessionId(payload);
        if (!sessionId) return;
        if (shouldDropDuplicatedEvent('chat.evolution_status', payload)) return;
        useChatStore.getState().setEvolutionStatus(sessionId, payload as unknown as EvolutionStatusPayload);
      }),
      webClient.on('chat.notice', ({ payload }) => {
        const sessionId = resolveEventSessionId(payload);
        if (!sessionId) return;
        if (shouldDropDuplicatedEvent('chat.notice', payload)) return;
        const content = pickString(payload.content, payload.message, payload.text);
        if (!content) return;
        const noticeType = pickString(payload.notice_type, payload.type) || 'notice';
        const requestId = getPayloadRequestId(payload) || `${Date.now()}`;
        const messageId = `notice-${noticeType}-${requestId}`;
        const chatState = useChatStore.getState();
        const existing = chatState.getRuntime(sessionId)?.messages.find((message) => message.id === messageId);
        if (existing) {
          chatState.updateMessage(sessionId, messageId, {
            content,
            timestamp: new Date().toISOString(),
          });
          return;
        }
        chatState.addMessage(sessionId, {
          id: messageId,
          role: 'system',
          content,
          timestamp: new Date().toISOString(),
        });
      }),
      webClient.on('config.changed', ({ payload }) => {
        const updatedKeys = Array.isArray(payload?.updated_keys)
          ? payload.updated_keys.filter((key): key is string => typeof key === 'string')
          : undefined;
        onConfigChangedRef.current?.(updatedKeys);
      }),
      webClient.on('task.global_running', ({ payload }) => {
        useChatStore.getState().setGlobalTaskRunning(Boolean(payload?.running));
      }),
      webClient.on('chat.error', ({ payload }) => {
        const sessionId = resolveEventSessionId(payload);
        if (!sessionId) return;
        if (shouldDropDuplicatedEvent('chat.error', payload)) return;
        useChatStore.getState().setThinking(sessionId, false);
        const errorMsg =
          typeof payload.error === 'string' ? payload.error : t('network.unknownError');
        // 忽略 "invalid page_idx or session history not found" 错误，因为这是新会话的正常情况
        if (errorMsg.includes('invalid page_idx or session history not found')) {
          useChatStore.getState().setLoadingHistory(sessionId, false);
          return;
        }
        useChatStore.getState().setExecutionError(sessionId, errorMsg);
        onErrorRef.current?.(errorMsg);
        useChatStore.getState().setSessionError(sessionId, errorMsg);
        useChatStore.getState().addMessage(sessionId, {
          id: `error-${Date.now()}`,
          role: 'system',
          content: t('network.errorPrefix', { message: errorMsg }),
          timestamp: new Date().toISOString(),
        });
      }),
      webClient.on('security.alert', ({ payload }) => {
        const sessionId = resolveEventSessionId(payload);
        if (!sessionId) return;

        const alertMsg =
          typeof payload.message === 'string'
            ? payload.message
            : '安全警告';

        window.dispatchEvent(new CustomEvent('security-alert', {
          detail: {
            message: alertMsg,
            message_id: payload.message_id || '',
            tool_call_id: payload.tool_call_id || '',
            alert_type: payload.alert_type || 'security',
            tool_name: payload.tool_name || '',
          }
        }));
      }),
      webClient.on('chat.retract', (event: WsEvent) => {
        const sessionId = resolveEventSessionId(event.payload);
        if (!sessionId) return;

        const retractMsg =
          typeof event.payload.message === 'string'
            ? event.payload.message
            : '内容已因安全原因撤回';

        const runtime = useChatStore.getState().getRuntime(sessionId);
        const currentStreamId = runtime?.currentStreamId;
        const messages = runtime?.messages ?? [];

        // Replace current streaming message first
        if (currentStreamId) {
          useChatStore.getState().updateMessage(sessionId, currentStreamId, {
            content: retractMsg,
            isStreaming: false,
          });
          useChatStore.getState().stopStreaming(sessionId);
        }

        // Replace ALL assistant messages after the last user message
        let lastUserIdx = -1;
        for (let i = messages.length - 1; i >= 0; i -= 1) {
          if (messages[i].role === 'user') {
            lastUserIdx = i;
            break;
          }
        }
        if (lastUserIdx >= 0) {
          for (let i = lastUserIdx + 1; i < messages.length; i++) {
            if (messages[i].role === 'assistant') {
              useChatStore.getState().updateMessage(sessionId, messages[i].id, { content: retractMsg });
            }
          }
        } else {
          for (const msg of messages) {
            if (msg.role === 'assistant') {
              useChatStore.getState().updateMessage(sessionId, msg.id, { content: retractMsg });
            }
          }
        }

        useChatStore.getState().setProcessing(sessionId, false);
        useChatStore.getState().setThinking(sessionId, false);
        activeRequestIdRef.current = undefined;

        const retractRequestId = typeof event.payload.request_id === 'string' ? event.payload.request_id : undefined;
        useChatStore.getState().clearCurrentTurnData(sessionId, retractRequestId);
      }),
      webClient.on('chat.interrupt_result', ({ payload }) => {
        const sessionId = resolveEventSessionId(payload);
        if (!sessionId) return;
        if (shouldDropDuplicatedEvent('chat.interrupt_result', payload)) return;
        // 切换模式时忽略中断结果
        if (useChatStore.getState().getRuntime(sessionId)?.switchingMode) return;
        const resultPayload = payload as unknown as InterruptResultPayload;
        useChatStore.getState().setInterruptResult(sessionId, resultPayload);
        // has_active_task 为 false 表示没有活跃任务（任务已完成）
        const hasActiveTask = resultPayload.has_active_task !== false;

        if (resultPayload.intent === 'pause') {
          if (resultPayload.success) {
            useChatStore.getState().setPaused(sessionId, true, resultPayload.paused_task);
          }
          useChatStore.getState().setProcessing(sessionId, false);
          useChatStore.getState().setThinking(sessionId, false);
        } else if (resultPayload.intent === 'resume') {
          if (resultPayload.success) {
            // 直接设置所有状态值
            if (hasActiveTask) {
              useChatStore.getState().setPaused(sessionId, false);
              useChatStore.getState().setProcessing(sessionId, true);
              useChatStore.getState().setThinking(sessionId, true);
            } else {
              useChatStore.getState().setPaused(sessionId, false);
              useChatStore.getState().setProcessing(sessionId, false);
              useChatStore.getState().setThinking(sessionId, false);
              // 任务已完成时，检查并触发队列中的下一个任务
              const currentMode = useSessionStore.getState().getRuntime(sessionId)?.mode;
              const runtime = useChatStore.getState().getRuntime(sessionId);
              const taskQueue = runtime?.taskQueue ?? [];
              const queuePaused = runtime?.queuePaused ?? false;
              if (currentMode === 'agent' && !queuePaused && taskQueue.length > 0) {
                const nextTask = taskQueue[0];
                if (nextTask && sendMessageRef.current) {
                  useChatStore.getState().removeFromTaskQueue(sessionId, nextTask.id);
                  sendMessageRef.current(nextTask.content, sessionId);
                }
              }
            }
          }
        } else if (resultPayload.intent === 'cancel') {
          useChatStore.getState().setPaused(sessionId, false);
          useChatStore.getState().setProcessing(sessionId, false);
          useChatStore.getState().setThinking(sessionId, false);
        } else if (resultPayload.intent === 'supplement') {
          useChatStore.getState().setPaused(sessionId, false);
        }
      }),
      webClient.on('chat.subtask_update', ({ payload }) => {
        const sessionId = resolveEventSessionId(payload);
        if (!sessionId) return;
        useChatStore.getState().updateSubtask(sessionId, payload as unknown as SubtaskUpdatePayload);
      }),
      webClient.on('chat.ask_user_question', ({ payload }) => {
        const sessionId = resolveEventSessionId(payload);
        if (!sessionId) return;
        const questionPayload = payload as Record<string, unknown>;
        const evolutionMeta =
          questionPayload.evolution_meta && typeof questionPayload.evolution_meta === 'object'
            ? (questionPayload.evolution_meta as Record<string, unknown>)
            : questionPayload._evolution_meta && typeof questionPayload._evolution_meta === 'object'
              ? (questionPayload._evolution_meta as Record<string, unknown>)
              : undefined;
        const questions = Array.isArray(questionPayload.questions) ? questionPayload.questions : [];
        const approvalSchema =
          typeof questionPayload.approval_schema === 'string'
            ? questionPayload.approval_schema
            : undefined;
        const planApprovalKind =
          typeof questionPayload.plan_approval_kind === 'string'
            ? questionPayload.plan_approval_kind
            : undefined;
        const planContent =
          typeof questionPayload.plan_content === 'string'
            ? questionPayload.plan_content
            : undefined;
        const planLanguage =
          questionPayload.plan_language === 'cn' || questionPayload.plan_language === 'en'
            ? questionPayload.plan_language
            : undefined;
        const normalizedPayload: AskUserQuestionPayload = {
          request_id: typeof questionPayload.request_id === 'string' ? questionPayload.request_id : '',
          source: typeof questionPayload.source === 'string' ? questionPayload.source : undefined,
          questions,
          ...(approvalSchema ? { approvalSchema } : {}),
          ...(evolutionMeta ? { evolutionMeta } : {}),
          ...(planApprovalKind ? { planApprovalKind } : {}),
          ...(planContent !== undefined ? { planContent } : {}),
          ...(planLanguage ? { planLanguage } : {}),
        };
        useChatStore.getState().setPendingQuestion(sessionId, normalizedPayload);
      }),
      // 同时监听 session_result 事件，以处理后端可能发送的不同格式
      webClient.on('session_result', ({ payload }) => {
        const sessionId = resolveEventSessionId(payload);
        if (!sessionId) return;
        clearThinkingForVisibleOutput(sessionId);
        const description =
          typeof payload.description === 'string' ? payload.description : '';
        const result = typeof payload.result === 'string' ? payload.result : '';
        // 创建工具调用对象
        const toolCallId = `session-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;
        const sessionToolCall: ToolCall = {
          id: toolCallId,
          name: 'session',
          arguments: {
            session_id: sessionId,
            description: description,
          },
          description: description || '会话完成',
          formatted_args: `会话任务：【${description || '未知任务'}】`,
        };
        useChatStore.getState().addToolCall(sessionId, sessionToolCall);
        // 组合 description 和 result 作为完整结果
        const fullResult = description
          ? `描述: ${description}\n\n结果: ${result}`
          : result;
        const sessionResult: ToolResult = {
          toolName: 'session',
          result: fullResult,
          success: true,
          toolCallId: toolCallId,
          summary: '完成',
        };
        useChatStore.getState().addToolResult(sessionId, sessionResult);
      }),
      webClient.on('chat.session_result', ({ payload }) => {
        const sessionId = resolveEventSessionId(payload);
        if (!sessionId) return;
        if (shouldDropDuplicatedEvent('chat.session_result', payload)) {
          return;
        }
        clearThinkingForVisibleOutput(sessionId);
        const description =
          typeof payload.description === 'string' ? payload.description : '';
        const result = typeof payload.result === 'string' ? payload.result : '';
        // 创建工具调用对象
        const toolCallId = `session-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;
        const sessionToolCall: ToolCall = {
          id: toolCallId,
          name: 'session',
          arguments: {
            session_id: sessionId,
            description: description,
          },
          description: description || '会话完成',
          formatted_args: `会话任务：【${description || '未知任务'}】`,
        };
        useChatStore.getState().addToolCall(sessionId, sessionToolCall);
        // 组合 description 和 result 作为完整结果
        const fullResult = description
          ? `描述: ${description}\n\n结果: ${result}`
          : result;
        const sessionResult: ToolResult = {
          toolName: 'session',
          result: fullResult,
          success: true,
          toolCallId: toolCallId,
          summary: '完成',
        };
        useChatStore.getState().addToolResult(sessionId, sessionResult);
      }),
      webClient.on('proactive_recommendation', ({ payload }) => {
        const sessionId = resolveEventSessionId(payload);
        if (!sessionId) return;
        const content = typeof payload.content === 'string' ? payload.content : '';
        if (!content) return;

        const proactiveType = typeof payload.proactive_type === 'string' ? payload.proactive_type : '';
        const proactiveTarget = typeof payload.proactive_target === 'string' ? payload.proactive_target : '';
        const proactiveReason = typeof payload.proactive_reason === 'string' ? payload.proactive_reason : '';

        const messageId = `proactive-${Date.now()}`;
        useChatStore.getState().addMessage(sessionId, {
          id: messageId,
          role: 'assistant',
          content,
          timestamp: new Date().toISOString(),
          isProactiveRecommendation: true,
          proactiveType: (proactiveType as 'skill_recommend' | 'task_reminder' | 'need_exploration') || undefined,
        });

        console.debug('[ws] proactive_recommendation', {
          type: proactiveType,
          target: proactiveTarget,
          reason: proactiveReason,
        });
      }),
      webClient.on('team.event', ({ payload }) => {
        const sessionId = resolveEventSessionId(payload);
        if (!sessionId) return;
        if (shouldDropDuplicatedEvent('team.event', payload)) {
          return;
        }
        clearThinkingForVisibleOutput(sessionId);
        useChatStore.getState().addMessage(sessionId, {
          id: `team-event-${Date.now()}`,
          role: 'system',
          content: `team.event:${JSON.stringify(payload)}`,
          timestamp: new Date().toISOString(),
        });
      }),
      webClient.on('team.message', ({ payload }) => {
        const sessionId = resolveEventSessionId(payload);
        if (!sessionId) return;
        if (shouldDropDuplicatedEvent('team.message', payload)) {
          return;
        }
        clearThinkingForVisibleOutput(sessionId);
        useChatStore.getState().addMessage(sessionId, {
          id: `team-message-${Date.now()}`,
          role: 'system',
          content: `team.event:${JSON.stringify(payload)}`,
          timestamp: new Date().toISOString(),
        });
      }),
      webClient.on('team.task', ({ payload }) => {
        const sessionId = resolveEventSessionId(payload);
        if (!sessionId) return;
        if (shouldDropDuplicatedEvent('team.task', payload)) {
          return;
        }
        if (isTeamPanelClearedForPayload(payload)) {
          return;
        }
        clearThinkingForVisibleOutput(sessionId);
        const p = payload as { payload?: { event?: unknown }; event?: unknown };
        const event = p.payload?.event || p.event;
        if (event) {
          const e = event as {
            type?: string;
            team_id?: string;
            task_id?: string;
            status?: string;
            timestamp?: number;
            member_id?: string;
            assignee?: string;
            team_name?: string;
            title?: string;
            name?: string;
            description?: string;
            content?: string;
            updated_at?: number | string | null;
          };
          useSessionStore.getState().addTeamTaskEvent(sessionId, {
            id: `task-${Date.now()}`,
            type: e.type || '',
            team_id: e.team_id || '',
            task_id: e.task_id || '',
            status: e.status || '',
            timestamp: e.timestamp || Date.now(),
            member_id: e.member_id,
            assignee: e.assignee,
            team_name: e.team_name,
            title: e.title || e.name || e.description,
            content: e.content,
            updated_at: e.updated_at,
          });
          const normalizedTask = normalizeTaskEvent(event);
          if (normalizedTask) {
            useSessionStore.getState().upsertTeamTask(sessionId, normalizedTask);
          }
        }
      }),
      webClient.on('team.member', ({ payload }) => {
        const sessionId = resolveEventSessionId(payload);
        if (!sessionId) return;
        if (shouldDropDuplicatedEvent('team.member', payload)) {
          return;
        }
        const p = payload as { payload?: { event?: unknown }; event?: unknown };
        const event = p.payload?.event || p.event;
        if (event) {
          const e = event as {
            type?: string;
            member_id?: string;
            status?: string;
            new_status?: string;
            timestamp?: number;
            name?: string;
            execution_status?: string | null;
            mode?: string;
          };
          const activeSessionId = getPayloadSessionId(payload) || undefined;
          upsertHumanShareCommandFromEvent(payload, e);
          if (e.type === 'team.member.shutdown' && e.member_id) {
            applyTeamMemberShutdown(e.member_id, activeSessionId);
          } else if (activeSessionId && clearedTeamPanelSessionRef.current.has(activeSessionId)) {
            return;
          } else if (e.type === 'team.member.status_changed' && e.member_id && e.new_status) {
            useSessionStore.getState().updateTeamMemberStatus(
              sessionId,
              e.member_id,
              e.new_status,
              e.timestamp
            );
          } else if (e.type === 'team.member.execution_changed' && e.member_id) {
            const existingMember = useSessionStore.getState().getRuntime(sessionId)?.teamMembers.some(
              (member) => member.member_id === e.member_id
            );
            if (existingMember) {
              useSessionStore.getState().addTeamMember(sessionId, {
                id: `member-${Date.now()}`,
                member_id: e.member_id,
                status: e.status || '',
                timestamp: e.timestamp || Date.now(),
                name: e.name,
                execution_status: e.execution_status || e.new_status,
                mode: e.mode,
              });
            }
          } else if (!e.type || e.type === 'team.member.spawned' || e.type === 'team.member.restarted') {
            useSessionStore.getState().addTeamMember(sessionId, {
              id: `member-${Date.now()}`,
              member_id: e.member_id || '',
              status: e.status || '',
              timestamp: e.timestamp || Date.now(),
              name: e.name,
              execution_status: e.execution_status,
              mode: e.mode,
            });
          }
        }
      }),
      webClient.on('chat.usage_summary', ({ payload }) => {
        console.log('[usage_summary] received:', payload);
        const sessionId = resolveEventSessionId(payload);
        if (!sessionId) {
          console.log('[usage_summary] filtered by session check');
          return;
        }
        const usage = payload.usage as UsageSummary | undefined;
        if (!usage) {
          console.log('[usage_summary] no usage field in payload');
          return;
        }
        const runtime = useChatStore.getState().getRuntime(sessionId);
        const currentStreamId = runtime?.currentStreamId;
        const messages = runtime?.messages ?? [];
        let targetId = currentStreamId;
        if (!targetId) {
          for (let i = messages.length - 1; i >= 0; i--) {
            if (messages[i].role === 'assistant') {
              targetId = messages[i].id;
              break;
            }
          }
        }
        console.log('[usage_summary] targetId:', targetId, 'usage:', usage);
        if (targetId) {
          useChatStore.getState().setUsageSummary(sessionId, targetId, usage);
        }
      }),
      webClient.on('harness.message', ({ payload }) => {
        const sessionId = resolveEventSessionId(payload);
        if (!sessionId) return;
        const content = typeof payload.content === 'string' ? payload.content : '';
        const stage = typeof payload.stage === 'string' ? payload.stage : undefined;

        useHarnessStore.getState().addHarnessMessage(sessionId, content, stage);

        // Pipeline start message contains stages array: { content, pipeline, stages: [{slot, display_name}] }
        const rawStages = payload.stages;
        if (Array.isArray(rawStages) && rawStages.length > 0) {
          const stages: { slot: string; display_name: string }[] = [];
          for (const s of rawStages) {
            if (typeof s === 'object' && s !== null) {
              const obj = s as Record<string, unknown>;
              const slot = typeof obj.slot === 'string' ? obj.slot : '';
              const displayName = typeof obj.display_name === 'string' ? obj.display_name : '';
              if (slot) stages.push({ slot, display_name: displayName || slot });
            }
          }
          if (stages.length > 0) useHarnessStore.getState().setStageDefinitions(sessionId, stages);
        }

        // Mark stage as running (skip pipeline start message which has stages array)
        if (stage && !rawStages) {
          const existingStage = useHarnessStore.getState().getRuntime(sessionId)?.stageResults.find(s => s.stage === stage);
          if (existingStage?.status !== 'running') {
            useHarnessStore.getState().updateStageResult(sessionId, { stage, status: 'running', messages: [], metrics: {} });
          }
        }

        useChatStore.getState().addMessage(sessionId, {
          id: `harness-msg-${Date.now()}`,
          role: 'system',
          content,
          timestamp: new Date().toISOString(),
          isHarnessMessage: true,
        });
      }),
      webClient.on('harness.stage_result', ({ payload }) => {
        const sessionId = resolveEventSessionId(payload);
        if (!sessionId) return;
        const stage = typeof payload.stage === 'string' ? payload.stage : '';
        const status = typeof payload.status === 'string' ? payload.status : 'success';
        const error = typeof payload.error === 'string' ? payload.error : undefined;
        const messages = Array.isArray(payload.messages) ? payload.messages.filter((m) => typeof m === 'string') : [];
        const metrics = typeof payload.metrics === 'object' && payload.metrics !== null && !Array.isArray(payload.metrics)
          ? payload.metrics as Record<string, unknown>
          : {};
        const scope = typeof payload.scope === 'string' ? payload.scope : '';
        const extensionName = typeof payload.extension_name === 'string' ? payload.extension_name : '';
        const extensionStage = typeof payload.extension_stage === 'string' ? payload.extension_stage : '';
        const parentStage = typeof payload.parent_stage === 'string' ? payload.parent_stage : '';
        const taskId = typeof payload.task_id === 'string' ? payload.task_id : undefined;
        if (scope === 'extension' && extensionName) {
          useHarnessStore.getState().updateExtensionProgress(sessionId, {
            extensionName,
            taskId,
            parentStage: parentStage || stage,
            extensionStage,
            status: status as 'running' | 'success' | 'failed' | 'timeout' | 'pending' | 'waiting' | 'skipped' | 'rejected',
            error,
            messages,
          });
        }
        if (stage) {
          useHarnessStore.getState().updateStageResult(sessionId, {
            stage,
            status: status as 'running' | 'success' | 'failed' | 'timeout' | 'pending',
            error,
            messages,
            metrics,
          });
          if (status === 'failed' && error) {
            useChatStore.getState().addMessage(sessionId, {
              id: `harness-error-${Date.now()}`,
              role: 'system',
              content: `Stage ${stage} failed: ${error}`,
              timestamp: new Date().toISOString(),
            });
          }
        } else {
          console.warn('[harness.stage_result] No stage field in payload, skipping update');
        }
      }),
      webClient.on('harness.extension_ready', ({ payload }) => {
        const sessionId = resolveEventSessionId(payload);
        if (!sessionId) return;
        const extensionName = typeof payload.extension_name === 'string' ? payload.extension_name : '';
        const runtimePath = typeof payload.runtime_path === 'string' ? payload.runtime_path : '';
        const sessionRuntimePath = typeof payload.session_runtime_path === 'string' ? payload.session_runtime_path : runtimePath;
        const extensionRuntimePath = typeof payload.extension_runtime_path === 'string' ? payload.extension_runtime_path : '';
        const configPath = typeof payload.config_path === 'string' ? payload.config_path : '';
        const runtimeExtensions = Array.isArray(payload.runtime_extensions)
          ? payload.runtime_extensions
              .filter((item) => typeof item === 'object' && item !== null)
              .map((item) => {
                const obj = item as Record<string, unknown>;
                return {
                  extensionName: typeof obj.extension_name === 'string' ? obj.extension_name : '',
                  runtimePath: typeof obj.runtime_path === 'string' ? obj.runtime_path : '',
                  configPath: typeof obj.config_path === 'string' ? obj.config_path : '',
                };
              })
              .filter((item) => item.extensionName && item.runtimePath)
          : [];
        const verifyReport = typeof payload.verify_report === 'object' && payload.verify_report !== null && !Array.isArray(payload.verify_report)
          ? payload.verify_report as Record<string, unknown>
          : {};
        const componentsSummary = typeof payload.components_summary === 'object' && payload.components_summary !== null && !Array.isArray(payload.components_summary)
          ? payload.components_summary as Record<string, unknown>
          : {};

        useHarnessStore.getState().setExtensionReady(sessionId, {
          extensionName,
          runtimePath,
          sessionRuntimePath,
          extensionRuntimePath,
          configPath,
          runtimeExtensions,
          verifyReport,
          componentsSummary,
        });
      }),
      webClient.on('harness.activate_interaction', ({ payload }) => {
        const sessionId = resolveEventSessionId(payload);
        if (!sessionId) return;
        const interactionId = typeof payload.interaction_id === 'string' ? payload.interaction_id : '';
        const extensionName = typeof payload.extension_name === 'string' ? payload.extension_name : '';
        const runtimePath = typeof payload.runtime_path === 'string' ? payload.runtime_path : '';
        const options: string[] = Array.isArray(payload.options) ? payload.options : ['accept', 'reject'];

        useHarnessStore.getState().setActivateInteraction(sessionId, {
          interactionId,
          extensionName,
          runtimePath,
          options,
          pending: true,
        });
        useChatStore.getState().setPendingQuestion(sessionId, {
          request_id: interactionId,
          source: 'activate_confirm',
          questions: [{
            header: '扩展激活确认',
            question: `是否激活扩展 **${extensionName}**？`,
            options: options.map((opt: string) => ({
              label: opt === 'accept' ? '激活' : opt === 'reject' ? '拒绝' : opt,
              description: '',
            })),
          }],
        });
      }),
      webClient.on('harness.session_finished', ({ payload }) => {
        const sessionId = resolveEventSessionId(payload);
        if (!sessionId) return;
        useChatStore.getState().setExecutionError(sessionId, null);
        useChatStore.getState().setProcessing(sessionId, false);
        useChatStore.getState().setThinking(sessionId, false);
        useHarnessStore.getState().setHarnessRunning(sessionId, false);
      }),
    ];

    return () => {
      unsubs.forEach((fn) => fn());
    };
  }, [
    appendTeamMemberOutputDelta,
    clearAllTeamMemberContextCompressionStatus,
    clearPendingTeamMemberContextCompressionStart,
    clearTeamMemberContextCompressionStatus,
    findExistingTeamMemberId,
    finishContextCompressionTurn,
    handleConnectionAck,
    handleContextCompressionState,
    handleTeamMemberContextCompressionState,
    handleTtsPlayback,
    revealPendingContextUsage,
    setContextCompressionStats,
    setHeartbeatStatus,
    clearThinkingForVisibleOutput,
    findActiveTeamLeaderMessage,
    updateSession,
    resolveEventSessionId,
    shouldDropDuplicatedEvent,
    shouldRecoverProcessingFromReasoning,
    t,
    takeTeamMemberOutputEventId,
  ]);

  useEffect(() => {
    const connectOptions: WebConnectOptions = {
      provider,
      apiKey,
      apiBase,
      model,
      projectDir,
    };
    const nextSignature = getConnectSignature(connectOptions);
    const previousSignature = lastConnectSignatureRef.current;
    const state = webClient.getState();

    if (nextSignature === previousSignature && state !== 'closed') {
      return;
    }

    lastConnectSignatureRef.current = nextSignature;

    const runConnect = async () => {
      try {
        if (previousSignature && previousSignature !== nextSignature && state !== 'closed') {
          await webClient.disconnect('connect options changed');
        }
        await webClient.connect(connectOptions);
      } catch (error) {
        const webError = error as WebError;
        setConnectionStats({ lastError: webError.message });
        onErrorRef.current?.(webError.message || 'WebSocket connection error');
      }
    };

    void runConnect();
  }, [
    apiBase,
    apiKey,
    model,
    projectDir,
    provider,
    setConnectionStats,
  ]);

  useEffect(() => {
    return () => {
      lastConnectSignatureRef.current = '';
      webClient.disconnect();
      setConnected(false);
      // 不再重置上下文压缩信息，保持本地存储的状态
      // setContextCompressionStats(null);
      setHeartbeatStatus('unknown', null, null);
      setConnectionStats({ state: 'closed', inflight: 0 });
    };
  }, [
    setContextCompressionStats,
    setConnectionStats,
    setConnected,
    setHeartbeatStatus,
  ]);

  useEffect(() => {
    const connectOptions: WebConnectOptions = {
      provider,
      apiKey,
      apiBase,
      model,
      projectDir,
    };
    const reconnectByDebugToggle = () => {
      void webClient.disconnect('debug mode toggled').then(() => {
        void webClient.connect(connectOptions).catch((error) => {
          const webError = error as WebError;
          setConnectionStats({ lastError: webError.message });
          onErrorRef.current?.(webError.message || 'WebSocket reconnect error');
        });
      });
    };
    window.addEventListener(WS_RECONNECT_EVENT, reconnectByDebugToggle);
    return () => {
      window.removeEventListener(WS_RECONNECT_EVENT, reconnectByDebugToggle);
    };
  }, [apiBase, apiKey, model, projectDir, provider, setConnectionStats]);

  useEffect(() => {
    const unsub = webClient.onStateChange((state) => {
      setConnectionState(state);
      const connected = state === 'ready';
      setIsConnected(connected);
      setConnected(connected);
      setConnectionStats({
        state,
        inflight: webClient.getInflightCount(),
        lastError: null,
      });
      if (!connected && (state === 'reconnecting' || state === 'closed')) {
        onDisconnectRef.current?.();
      }
    });
    return () => {
      unsub();
    };
  }, [setConnected, setConnectionStats]);

  useEffect(() => {
    const timer = window.setInterval(() => {
      setConnectionStats({
        inflight: webClient.getInflightCount(),
      });
    }, 1000);
    return () => {
      window.clearInterval(timer);
    };
  }, [setConnectionStats]);

  useEffect(() => {
    const markAllRuntimes = () => {
      const runtimes = useChatStore.getState().runtimes;
      for (const sid of Object.keys(runtimes)) {
        useChatStore.getState().markTimedOutExecutions(sid);
      }
    };
    markAllRuntimes();
    const timer = window.setInterval(markAllRuntimes, 1000);
    return () => {
      window.clearInterval(timer);
    };
  }, []);

  return {
    isConnected,
    connectionState,
    request,
    persistMedia,
    sendMessage,
    sendStructuredChatContent,
    interrupt,
    pause,
    cancel,
    supplement,
    resume,
    switchMode,
    disconnect,
    sendUserAnswer,
    respondActivate,
    getInflightCount: () => webClient.getInflightCount(),
  };
}
