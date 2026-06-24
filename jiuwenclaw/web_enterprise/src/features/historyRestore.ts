import { Message, MessageRole, UsageSummary, FileDownloadItem, WsEvent } from '../types';
import { webClient } from '../services/webClient';
import { normalizeFinalContent } from '../utils/finalContent';
import { shouldProcessHistoryPayload } from './historyRestoreFilter';
import { logHistoryRestore } from './historyRestoreLog';

export const HISTORY_GET_METHOD = 'history.get';
export const HISTORY_MESSAGE_EVENT = 'history.message';

/** 助手侧仅恢复这些事件；用户消息无 event_type，单独保留 */
const ALLOWED_ASSISTANT_EVENT_TYPES = new Set([
  'chat.final',
  'chat.delta',
  'chat.tool_call',
  'chat.tool_result',
  'chat.usage_summary',
  'chat.file',
  'team.message',
  'harness.message',
  'harness.stage_result',
  'harness.extension_ready',
]);

/** 后端约定：最后一帧 `history.message` 使用 `payload.status: done`（兼容旧版 `payload.content: done`） */
const HISTORY_RESTORE_DONE_CONTENT = 'done';
/** 两次 history.message 之间无新帧时的兜底（毫秒）；正常链路由 done / page_complete 结束；持续有 chunk 时不会触发 */
const HISTORY_RESTORE_IDLE_MS = 60_000;
const HISTORY_RESTORE_MAX_RETRIES = 2;

export interface HistoryToolReplayItem {
  kind: 'tool_call' | 'tool_result';
  at: string;
  payload: Record<string, unknown>;
}

export interface HistoryHarnessReplayItem {
  kind: 'harness_message' | 'harness_stage_result';
  at: string;
  payload: Record<string, unknown>;
}

type HistoryTimelineEntry =
  | { kind: 'message'; message: Message }
  | { kind: 'delta'; at: string; content: string; requestId: string }
  | { kind: 'final_marker'; at: string; requestId: string }
  | { kind: 'tool_call'; at: string; payload: Record<string, unknown> }
  | { kind: 'tool_result'; at: string; payload: Record<string, unknown> }
  | { kind: 'usage_summary'; at: string; usage: UsageSummary }
  | { kind: 'file_items'; at: string; files: FileDownloadItem[] }
  | { kind: 'harness_message'; at: string; content: string; stage?: string }
  | { kind: 'harness_stage_result'; at: string; stage: string; status: string; error: string; messages: string[]; metrics: Record<string, unknown> };

interface BeginHistoryRestoreOptions {
  sessionId: string;
  requestId?: string;
  onReady: (messages: Message[], totalPages: number | null) => void;
  /** 与消息同一时间线顺序，用于恢复 ToolGroupDisplay */
  onToolReplay?: (items: HistoryToolReplayItem[]) => void;
  /** 无消息且无工具回放时调用；`totalPages` 来自流中最后一帧（若有） */
  onEmpty?: (totalPages: number | null) => void;
  onError?: (message: string) => void;
  /** 空闲超时且无数据时重试；由调用方重新发起 `history.get` */
  onRetry?: (attempt: number) => void | Promise<void>;
}

export interface HistoryRestoreHandle {
  generation: number;
  dispose: () => void;
}

let restoreGeneration = 0;
let activeRestore: HistoryRestoreHandle | null = null;

/** 分页拉取与全量恢复互斥，避免 chunk 串台 */
let activePageFetchDispose: (() => void) | null = null;

function disposeActivePageFetch(): void {
  activePageFetchDispose?.();
  activePageFetchDispose = null;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
}

function pickFirstString(input: Record<string, unknown>, keys: string[]): string | undefined {
  for (const key of keys) {
    const value = input[key];
    if (typeof value === 'string') {
      const trimmed = value.trim();
      if (trimmed) {
        return trimmed;
      }
    }
  }
  return undefined;
}

function normalizeHistoryRole(rawRole: unknown): MessageRole {
  if (typeof rawRole !== 'string') return 'assistant';
  const role = rawRole.trim().toLowerCase();
  if (role === 'user' || role === 'human') return 'user';
  if (role === 'assistant' || role === 'ai' || role === 'bot') return 'assistant';
  if (role === 'system') return 'system';
  if (role === 'tool' || role === 'tool_call' || role === 'tool_result') return 'tool';
  return 'assistant';
}

function isHistoryRestoreDoneContent(rawContent: unknown): boolean {
  if (typeof rawContent !== 'string') {
    return false;
  }
  return rawContent.trim().toLowerCase() === HISTORY_RESTORE_DONE_CONTENT;
}

function isHistoryRestoreDonePayload(payload: Record<string, unknown>): boolean {
  const rawStatus = payload.status;
  if (typeof rawStatus === 'string' && rawStatus.trim().toLowerCase() === HISTORY_RESTORE_DONE_CONTENT) {
    return true;
  }
  return isHistoryRestoreDoneContent(payload.content);
}

function extractHistoryMessagePayload(payload: Record<string, unknown>): unknown {
  if ('message' in payload) {
    return payload.message;
  }
  return payload.content;
}

function normalizeHistoryContent(
  rawContent: unknown,
  onError?: (message: string) => void
): Record<string, unknown> | null {
  if (isHistoryRestoreDoneContent(rawContent)) {
    return null;
  }
  if (isRecord(rawContent)) {
    return rawContent;
  }
  if (typeof rawContent !== 'string') {
    return null;
  }
  try {
    const parsed = JSON.parse(rawContent);
    if (isRecord(parsed)) {
      return parsed;
    }
    return null;
  } catch (error) {
    const detail = error instanceof Error ? error.message : String(error);
    onError?.(`history.message.content parse failed: ${detail}`);
    return null;
  }
}

function recordTimestampIso(record: Record<string, unknown>): string {
  const ts = record.timestamp;
  if (typeof ts === 'number' && Number.isFinite(ts)) {
    const millis = ts > 1_000_000_000_000 ? ts : ts * 1000;
    const d = new Date(millis);
    if (!Number.isNaN(d.getTime())) {
      return d.toISOString();
    }
  }
  if (typeof ts === 'string') {
    const parsed = Date.parse(ts);
    if (!Number.isNaN(parsed)) {
      return new Date(parsed).toISOString();
    }
  }
  return new Date().toISOString();
}

const _HISTORY_RECORD_META_KEYS = new Set([
  'id', 'role', 'request_id', 'channel_id', 'timestamp', 'event_type', 'event_payload',
]);

/** 合并 event_payload 与顶层 content，供 final / tool 解析 */
function buildEventPayloadForRecord(record: Record<string, unknown>): Record<string, unknown> {
  const ep = record.event_payload;
  const base = isRecord(ep) ? { ...ep } : {};

  // 无 event_payload 时：将顶层工具字段（extra 展平写入的字段）提升到 base
  if (!isRecord(ep)) {
    for (const [key, value] of Object.entries(record)) {
      if (!_HISTORY_RECORD_META_KEYS.has(key)) {
        base[key] = value;
      }
    }
  }

  if (typeof record.content === 'string' && typeof base.content !== 'string') {
    base.content = record.content;
  }
  return base;
}

function parseHistoryTimelineEntry(
  record: Record<string, unknown>,
  sessionId: string
): HistoryTimelineEntry | null {
  const role = normalizeHistoryRole(record.role);
  const at = recordTimestampIso(record);

  if (role === 'user') {
    const content = pickFirstString(record, ['content', 'text', 'body']) ?? '';
    if (!content.trim()) {
      return null;
    }
    const id =
      pickFirstString(record, ['id', 'message_id', 'msg_id']) ?? `hist-user-${sessionId}-${at}`;
    return {
      kind: 'message',
      message: { id, role: 'user', content, timestamp: at },
    };
  }

  if (role !== 'assistant') {
    return null;
  }

  let eventType = typeof record.event_type === 'string' ? record.event_type.trim() : '';

  if (!eventType) {
    const raw = String(record.content ?? '').trim();
    if (!raw) {
      return null;
    }
    eventType = 'chat.final';
  }

  if (!ALLOWED_ASSISTANT_EVENT_TYPES.has(eventType)) {
    return null;
  }

  const payload = buildEventPayloadForRecord(record);

  if (eventType === 'chat.final') {
    const content = normalizeFinalContent(payload);
    const requestId = pickFirstString(record, ['request_id']) ?? '';
    if (!content.trim()) {
      return { kind: 'final_marker', at, requestId };
    }
    const id =
      pickFirstString(record, ['id', 'message_id', 'msg_id']) ?? `hist-final-${sessionId}-${at}`;
    return {
      kind: 'message',
      message: { id, role: 'assistant', content, timestamp: at },
    };
  }

  if (eventType === 'chat.delta') {
    const content = pickFirstString(record, ['content']) ?? '';
    if (!content) {
      return null;
    }
    const requestId = pickFirstString(record, ['request_id']) ?? '';
    return { kind: 'delta', at, content, requestId };
  }

  if (eventType === 'chat.tool_call') {
    return { kind: 'tool_call', at, payload };
  }

  if (eventType === 'chat.tool_result') {
    return { kind: 'tool_result', at, payload };
  }

  if (eventType === 'chat.usage_summary') {
    const rawUsage = payload.usage;
    if (isRecord(rawUsage)) {
      const usage: UsageSummary = {
        input_tokens: typeof rawUsage.input_tokens === 'number' ? rawUsage.input_tokens : 0,
        output_tokens: typeof rawUsage.output_tokens === 'number' ? rawUsage.output_tokens : 0,
        total_tokens: typeof rawUsage.total_tokens === 'number' ? rawUsage.total_tokens : 0,
      };
      if (typeof rawUsage.input_cost === 'number') usage.input_cost = rawUsage.input_cost;
      if (typeof rawUsage.output_cost === 'number') usage.output_cost = rawUsage.output_cost;
      if (typeof rawUsage.total_cost === 'number') usage.total_cost = rawUsage.total_cost;
      return { kind: 'usage_summary', at, usage };
    }
    return null;
  }

  if (eventType === 'chat.file') {
    const rawFiles = payload.files;
    if (!Array.isArray(rawFiles) || rawFiles.length === 0) {
      return null;
    }
    const files = rawFiles as FileDownloadItem[];
    return {
      kind: 'file_items',
      at,
      files,
    };
  }

  if (eventType === 'harness.message') {
    const content = typeof payload.content === 'string' ? payload.content : '';
    const stage = typeof payload.stage === 'string' ? payload.stage : undefined;
    if (!content.trim()) {
      return null;
    }
    return { kind: 'harness_message', at, content, stage };
  }

  if (eventType === 'harness.stage_result') {
    const stage = typeof payload.stage === 'string' ? payload.stage : '';
    const status = typeof payload.status === 'string' ? payload.status : 'success';
    const error = typeof payload.error === 'string' ? payload.error : '';
    const messages = Array.isArray(payload.messages) ? payload.messages.filter((m) => typeof m === 'string') : [];
    const metrics = isRecord(payload.metrics) ? payload.metrics as Record<string, unknown> : {};
    if (!stage.trim()) {
      return null;
    }
    return { kind: 'harness_stage_result', at, stage, status, error, messages, metrics };
  }

  return null;
}

function deltaBufferKey(requestId: string): string {
  return requestId.trim() || '__default__';
}

function entrySortKey(entry: HistoryTimelineEntry): number {
  const raw = entry.kind === 'message' ? entry.message.timestamp : entry.at;
  const ms = Date.parse(raw);
  return Number.isFinite(ms) ? ms : 0;
}

function materializeHistoryTimeline(
  entries: HistoryTimelineEntry[],
  sessionId: string
): { messages: Message[]; toolReplay: HistoryToolReplayItem[] } {
  const toolReplay: HistoryToolReplayItem[] = [];
  const chronological = entries
    .map((entry, index) => ({ entry, index }))
    .sort((a, b) => {
      const diff = entrySortKey(a.entry) - entrySortKey(b.entry);
      return diff !== 0 ? diff : a.index - b.index;
    })
    .map(({ entry }) => entry);

  for (const e of chronological) {
    if (e.kind === 'tool_call' || e.kind === 'tool_result') {
      toolReplay.push({ kind: e.kind, at: e.at, payload: e.payload });
    }
  }

  const messages: Message[] = [];
  let pendingFileItems: FileDownloadItem[] | null = null;
  const deltaBuffers = new Map<string, { content: string; at: string }>();

  const flushDelta = (requestId: string) => {
    const key = deltaBufferKey(requestId);
    const buf = deltaBuffers.get(key);
    if (!buf || !buf.content.trim()) {
      deltaBuffers.delete(key);
      return;
    }
    messages.push({
      id: `hist-assist-${sessionId}-${key}-${buf.at}`,
      role: 'assistant',
      content: buf.content,
      timestamp: buf.at,
    });
    deltaBuffers.delete(key);
  };

  const flushAllDeltas = () => {
    for (const key of [...deltaBuffers.keys()]) {
      flushDelta(key === '__default__' ? '' : key);
    }
  };

  // history.message 流为 newest-first；unshift 后 entries 已是 time-asc，再按 timestamp 稳定排序
  for (const e of chronological) {
    if (e.kind === 'delta') {
      const key = deltaBufferKey(e.requestId);
      const prev = deltaBuffers.get(key);
      deltaBuffers.set(key, {
        content: `${prev?.content ?? ''}${e.content}`,
        at: e.at,
      });
      continue;
    }

    if (e.kind === 'final_marker') {
      flushDelta(e.requestId);
      continue;
    }

    if (e.kind === 'message') {
      if (e.message.role === 'user') {
        flushAllDeltas();
        messages.push(e.message);
        continue;
      }

      flushAllDeltas();
      let message = e.message;
      if (message.role === 'assistant' && pendingFileItems) {
        message = { ...message, fileItems: pendingFileItems };
        pendingFileItems = null;
      }
      messages.push(message);
      continue;
    }

    if (e.kind === 'usage_summary') {
      flushAllDeltas();
      for (let i = messages.length - 1; i >= 0; i--) {
        if (messages[i].role === 'assistant') {
          messages[i] = { ...messages[i], usageSummary: e.usage };
          break;
        }
      }
      continue;
    }

    if (e.kind === 'harness_message') {
      flushAllDeltas();
      messages.push({
        id: `harness-msg-${e.at}`,
        role: 'system',
        content: e.content,
        timestamp: e.at,
        isHarnessMessage: true,
      });
      continue;
    }

    if (e.kind === 'file_items') {
      pendingFileItems = e.files;
      continue;
    }

    // harness_stage_result / tool_* 已在上方或 toolReplay 处理
  }

  flushAllDeltas();
  return { messages, toolReplay };
}

/** 工作区 history.json 预览：最多展示条数（按消息时间取最近） */
export const HISTORY_FILE_PREVIEW_MAX_MESSAGES = 20;

/**
 * 将磁盘上的 history.json 解析结果（通常为记录数组）转为与历史恢复相同的筛选规则下的消息列表，
 * 并按时间升序仅保留时间上最近的 {@link HISTORY_FILE_PREVIEW_MAX_MESSAGES} 条用户/助手消息。
 */
export function parseHistoryJsonFileToPreviewMessages(
  parsed: unknown,
  sessionId: string
): Message[] {
  if (!Array.isArray(parsed)) {
    return [];
  }

  const messages: Message[] = [];
  for (const item of parsed) {
    if (!isRecord(item)) {
      continue;
    }
    const entry = parseHistoryTimelineEntry(item, sessionId);
    if (entry?.kind === 'message') {
      messages.push(entry.message);
    }
  }

  const sorted = [...messages].sort(
    (a, b) => Date.parse(a.timestamp) - Date.parse(b.timestamp)
  );
  return sorted.slice(-HISTORY_FILE_PREVIEW_MAX_MESSAGES);
}

function isHistoryBatchEnd(payload: Record<string, unknown>): boolean {
  const markers = [
    payload.done,
    payload.last,
    payload.is_last,
    payload.page_complete,
    payload.end,
  ];
  return markers.some((marker) => marker === true);
}

type HistoryFinalizeReason = 'done' | 'batch_end' | 'idle_timeout';

interface HistoryIdleGuardOptions {
  errorMessage: string;
  getEntryCount: () => number;
  getTotalPages: () => number | null;
  logPhase: (phase: string, detail: Record<string, unknown>) => void;
  onError?: (message: string) => void;
  onExhausted: () => void;
  onIdleFinalize: () => void;
  onRetry?: (attempt: number) => void | Promise<void>;
}

function createHistoryIdleGuard(options: HistoryIdleGuardOptions) {
  let idleTimer: number | null = null;
  let retryCount = 0;
  let disposed = false;

  const clearIdleTimer = () => {
    if (idleTimer !== null) {
      window.clearTimeout(idleTimer);
      idleTimer = null;
    }
  };

  const scheduleIdleTimer = () => {
    clearIdleTimer();
    idleTimer = window.setTimeout(() => {
      if (disposed) {
        return;
      }

      options.logPhase('idle_timeout', {
        entryCount: options.getEntryCount(),
        totalPages: options.getTotalPages(),
        retryCount,
      });

      if (options.getEntryCount() > 0 || options.getTotalPages() != null) {
        options.onIdleFinalize();
        return;
      }

      if (retryCount < HISTORY_RESTORE_MAX_RETRIES && options.onRetry) {
        retryCount += 1;
        options.logPhase('retry', {
          attempt: retryCount,
          entryCount: options.getEntryCount(),
        });
        void Promise.resolve(options.onRetry(retryCount)).finally(() => {
          if (!disposed) {
            scheduleIdleTimer();
          }
        });
        return;
      }

      options.onError?.(options.errorMessage);
      options.onExhausted();
    }, HISTORY_RESTORE_IDLE_MS);
  };

  return {
    scheduleIdleTimer,
    clearIdleTimer,
    dispose: () => {
      disposed = true;
      clearIdleTimer();
    },
  };
}

export function beginHistoryRestore(options: BeginHistoryRestoreOptions): HistoryRestoreHandle {
  disposeActivePageFetch();
  activeRestore?.dispose();

  const generation = restoreGeneration + 1;
  restoreGeneration = generation;

  logHistoryRestore('begin', {
    sessionId: options.sessionId,
    requestId: options.requestId ?? null,
    generation,
  });

  const entries: HistoryTimelineEntry[] = [];
  let totalPages: number | null = null;
  let disposed = false;

  function dispose(): void {
    if (disposed) return;
    disposed = true;
    idleGuard.dispose();
    unsubscribe();
    if (activeRestore?.generation === generation) {
      activeRestore = null;
    }
  }

  function finalize(reason: HistoryFinalizeReason): void {
    if (disposed) return;

    const { messages, toolReplay } = materializeHistoryTimeline(entries, options.sessionId);

    logHistoryRestore('finalize', {
      sessionId: options.sessionId,
      requestId: options.requestId ?? null,
      reason,
      entryCount: entries.length,
      messageCount: messages.length,
      toolReplayCount: toolReplay.length,
      totalPages,
    });

    dispose();

    if (messages.length === 0 && toolReplay.length === 0) {
      options.onEmpty?.(totalPages);
      return;
    }
    options.onReady(messages, totalPages);
    if (toolReplay.length > 0) {
      options.onToolReplay?.(toolReplay);
    }
  }

  const idleGuard = createHistoryIdleGuard({
    errorMessage: 'history restore timed out waiting for done frame',
    getEntryCount: () => entries.length,
    getTotalPages: () => totalPages,
    logPhase: (phase, detail) => {
      logHistoryRestore(phase, {
        sessionId: options.sessionId,
        requestId: options.requestId ?? null,
        ...detail,
      });
    },
    onError: options.onError,
    onExhausted: dispose,
    onIdleFinalize: () => finalize('idle_timeout'),
    onRetry: options.onRetry,
  });

  const unsubscribe = webClient.on(HISTORY_MESSAGE_EVENT, (event: WsEvent) => {
    if (disposed || generation !== restoreGeneration) {
      return;
    }

    const payload = { ...event.payload };
    if (event.request_id) {
      payload.request_id = event.request_id;
    }
    if (!shouldProcessHistoryPayload(payload, options.sessionId, options.requestId)) {
      return;
    }

    if (typeof payload.total_pages === 'number' && Number.isFinite(payload.total_pages)) {
      totalPages = payload.total_pages;
    }

    if (isHistoryRestoreDonePayload(payload)) {
      idleGuard.clearIdleTimer();
      finalize('done');
      return;
    }

    const raw = extractHistoryMessagePayload(payload);
    const record = normalizeHistoryContent(raw, options.onError);
    if (record) {
      const entry = parseHistoryTimelineEntry(record, options.sessionId);
      if (entry) {
        entries.unshift(entry);
      }
    }

    if (isHistoryBatchEnd(payload)) {
      idleGuard.clearIdleTimer();
      finalize('batch_end');
      return;
    }

    idleGuard.scheduleIdleTimer();
  });

  idleGuard.scheduleIdleTimer();

  const handle: HistoryRestoreHandle = { generation, dispose };
  activeRestore = handle;
  return handle;
}

export interface FetchHistoryPageResult {
  messages: Message[];
  toolReplay: HistoryToolReplayItem[];
  totalPages: number | null;
}

export interface FetchHistoryPageOptions {
  sessionId: string;
  requestId?: string;
  onReady: (result: FetchHistoryPageResult) => void;
  onEmpty?: (totalPages: number | null) => void;
  onError?: (message: string) => void;
  onRetry?: (attempt: number) => void | Promise<void>;
}

/**
 * 拉取单页历史（用于「加载更早」），与 beginHistoryRestore 互斥。
 * 调用方需在订阅建立后再发 `history.get`（含对应 `page_idx`）。
 */
export function fetchHistoryPage(options: FetchHistoryPageOptions): HistoryRestoreHandle {
  disposeActivePageFetch();
  activeRestore?.dispose();

  const generation = restoreGeneration + 1;
  restoreGeneration = generation;

  logHistoryRestore('fetchPage.begin', {
    sessionId: options.sessionId,
    requestId: options.requestId ?? null,
    generation,
  });

  const entries: HistoryTimelineEntry[] = [];
  let totalPages: number | null = null;
  let disposed = false;

  function dispose(): void {
    if (disposed) return;
    disposed = true;
    idleGuard.dispose();
    unsubscribe();
    activePageFetchDispose = null;
    if (activeRestore?.generation === generation) {
      activeRestore = null;
    }
  }

  function finalize(reason: HistoryFinalizeReason): void {
    if (disposed) return;

    const { messages, toolReplay } = materializeHistoryTimeline(entries, options.sessionId);

    logHistoryRestore('fetchPage.finalize', {
      sessionId: options.sessionId,
      requestId: options.requestId ?? null,
      reason,
      entryCount: entries.length,
      messageCount: messages.length,
      toolReplayCount: toolReplay.length,
      totalPages,
    });

    dispose();

    if (messages.length === 0 && toolReplay.length === 0) {
      options.onEmpty?.(totalPages);
      return;
    }
    options.onReady({ messages, toolReplay, totalPages });
  }

  const idleGuard = createHistoryIdleGuard({
    errorMessage: 'history page fetch timed out waiting for done frame',
    getEntryCount: () => entries.length,
    getTotalPages: () => totalPages,
    logPhase: (phase, detail) => {
      logHistoryRestore(`fetchPage.${phase}`, {
        sessionId: options.sessionId,
        requestId: options.requestId ?? null,
        ...detail,
      });
    },
    onError: options.onError,
    onExhausted: dispose,
    onIdleFinalize: () => finalize('idle_timeout'),
    onRetry: options.onRetry,
  });

  const unsubscribe = webClient.on(HISTORY_MESSAGE_EVENT, (event: WsEvent) => {
    if (disposed || generation !== restoreGeneration) {
      return;
    }

    const payload = { ...event.payload };
    if (event.request_id) {
      payload.request_id = event.request_id;
    }
    if (!shouldProcessHistoryPayload(payload, options.sessionId, options.requestId)) {
      return;
    }

    if (typeof payload.total_pages === 'number' && Number.isFinite(payload.total_pages)) {
      totalPages = payload.total_pages;
    }

    if (isHistoryRestoreDonePayload(payload)) {
      idleGuard.clearIdleTimer();
      finalize('done');
      return;
    }

    const raw = extractHistoryMessagePayload(payload);
    const record = normalizeHistoryContent(raw, options.onError);
    if (record) {
      const entry = parseHistoryTimelineEntry(record, options.sessionId);
      if (entry) {
        entries.unshift(entry);
      }
    }

    if (isHistoryBatchEnd(payload)) {
      idleGuard.clearIdleTimer();
      finalize('batch_end');
      return;
    }

    idleGuard.scheduleIdleTimer();
  });

  idleGuard.scheduleIdleTimer();

  const handle: HistoryRestoreHandle = { generation, dispose };
  activeRestore = handle;
  activePageFetchDispose = dispose;
  return handle;
}