import { Message, MessageRole, UsageSummary, FileDownloadItem, MediaItem, WsEvent } from '../types';
import { webClient } from '../services/webClient';
import { normalizeFinalContent } from '../utils/finalContent';
import { isA2UIClientEventContent } from './a2ui/a2uiContent';

export const HISTORY_GET_METHOD = 'history.get';
export const HISTORY_MESSAGE_EVENT = 'history.message';

/** 助手侧仅恢复这些事件；用户消息无 event_type，单独保留 */
const ALLOWED_ASSISTANT_EVENT_TYPES = new Set([
  'chat.final',
  'chat.tool_call',
  'chat.tool_result',
  'chat.usage_summary',
  'chat.file',
  'team.message',
  'team.member',
  'team.task',
  'harness.message',
  'harness.stage_result',
  'harness.extension_ready'
]);

/** 后端约定：最后一帧 `history.message` 使用 `payload.status: done`（兼容旧版 `payload.content: done`） */
const HISTORY_RESTORE_DONE_CONTENT = 'done';

export interface HistoryToolReplayItem {
  kind: 'tool_call' | 'tool_result';
  at: string;
  payload: Record<string, unknown>;
}

export interface HistoryHarnessReplayItem {
  kind: 'harness_message' | 'harness_stage_result';
  at: string;
  payload: {
    content?: string;
    stage?: string;
    status?: string;
    error?: string;
    messages?: string[];
    metrics?: Record<string, unknown>;
  };
}

export interface HistoryTeamReplayItem {
  kind: 'team_member' | 'team_task';
  at: string;
  payload: {
    event: Record<string, unknown>;
  };
}

type HistoryTimelineEntry =
  | { kind: 'message'; message: Message }
  | { kind: 'tool_call'; at: string; payload: Record<string, unknown> }
  | { kind: 'tool_result'; at: string; payload: Record<string, unknown> }
  | { kind: 'usage_summary'; at: string; usage: UsageSummary }
  | { kind: 'file_items'; at: string; files: FileDownloadItem[] }
  | { kind: 'team_member'; at: string; payload: { event: Record<string, unknown> } }
  | { kind: 'team_task'; at: string; payload: { event: Record<string, unknown> } }
  | { kind: 'harness_message'; at: string; content: string; stage?: string }
  | { kind: 'harness_stage_result'; at: string; stage: string; status: string; error: string; messages: string[]; metrics: Record<string, unknown> };

interface BeginHistoryRestoreOptions {
  sessionId: string;
  onReady: (messages: Message[], totalPages: number | null) => void;
  /** 与消息同一时间线顺序，用于恢复 ToolGroupDisplay */
  onToolReplay?: (items: HistoryToolReplayItem[]) => void;
  /** 与消息同一时间线顺序，用于恢复 HarnessProgressBar */
  onHarnessReplay?: (items: HistoryHarnessReplayItem[]) => void;
  /** 与消息同一时间线顺序，用于恢复 Team 成员/任务状态 */
  onTeamReplay?: (items: HistoryTeamReplayItem[]) => void;
  /** 无消息且无工具回放时调用；`totalPages` 来自流中最后一帧（若有） */
  onEmpty?: (totalPages: number | null) => void;
  onError?: (message: string) => void;
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

function isTeamModeRecord(record: Record<string, unknown>): boolean {
  return typeof record.mode === 'string' && record.mode.trim().toLowerCase() === 'team';
}

function isTeamTeammateMessageRecord(record: Record<string, unknown>): boolean {
  return typeof record.role === 'string' && record.role.trim().toLowerCase() === 'teammate';
}

function isHiddenTeamTeammateMessageRecord(record: Record<string, unknown>): boolean {
  return isTeamModeRecord(record) && isTeamTeammateMessageRecord(record);
}

const _HISTORY_RECORD_META_KEYS = new Set([
  'id', 'role', 'request_id', 'channel_id', 'timestamp', 'event_type', 'event_payload', 'mode',
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

function extractTeamEventRecord(record: Record<string, unknown>): Record<string, unknown> | null {
  if (isRecord(record.event)) {
    return record.event;
  }
  if (isRecord(record.event_payload)) {
    if (isRecord(record.event_payload.event)) {
      return record.event_payload.event;
    }
    return record.event_payload;
  }

  const payload: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(record)) {
    if (!_HISTORY_RECORD_META_KEYS.has(key)) {
      payload[key] = value;
    }
  }
  if (isRecord(payload.event)) {
    return payload.event;
  }
  return Object.keys(payload).length > 0 ? payload : null;
}

function filenameFromPath(path: string): string {
  const parts = path.split(/[\\/]+/).filter(Boolean);
  return parts[parts.length - 1] || 'image';
}

function normalizeHistoryMediaItem(value: unknown): MediaItem | null {
  if (!isRecord(value)) {
    return null;
  }

  const rawType = typeof value.type === 'string' ? value.type.trim().toLowerCase() : 'image';
  if (rawType && rawType !== 'image') {
    return null;
  }

  const path = pickFirstString(value, ['path', 'url']);
  if (!path) {
    return null;
  }

  const mimeType = pickFirstString(value, ['mime_type', 'mimeType']) ?? 'image/png';
  if (!mimeType.startsWith('image/')) {
    return null;
  }

  const filename = pickFirstString(value, ['filename', 'name']) ?? filenameFromPath(path);
  const size = typeof value.size_bytes === 'number'
    ? value.size_bytes
    : typeof value.sizeBytes === 'number'
      ? value.sizeBytes
      : undefined;

  return {
    type: 'image',
    filename,
    path,
    mime_type: mimeType,
    mimeType,
    ...(typeof size === 'number' ? { size_bytes: size, sizeBytes: size } : {}),
  };
}

function appendHistoryMediaItems(
  target: MediaItem[],
  seenKeys: Set<string>,
  value: unknown
): void {
  if (!Array.isArray(value)) {
    return;
  }
  for (const item of value) {
    const normalized = normalizeHistoryMediaItem(item);
    if (!normalized) {
      continue;
    }
    const key = normalized.path || `${normalized.filename}:${normalized.mimeType}`;
    if (seenKeys.has(key)) {
      continue;
    }
    seenKeys.add(key);
    target.push(normalized);
  }
}

function extractHistoryMediaItems(record: Record<string, unknown>): MediaItem[] {
  const mediaItems: MediaItem[] = [];
  const seenKeys = new Set<string>();

  appendHistoryMediaItems(mediaItems, seenKeys, record.media_items);
  appendHistoryMediaItems(mediaItems, seenKeys, record.mediaItems);

  if (isRecord(record.files)) {
    appendHistoryMediaItems(mediaItems, seenKeys, record.files.uploaded_images);
  }
  if (isRecord(record.event_payload)) {
    appendHistoryMediaItems(mediaItems, seenKeys, record.event_payload.media_items);
    if (isRecord(record.event_payload.files)) {
      appendHistoryMediaItems(mediaItems, seenKeys, record.event_payload.files.uploaded_images);
    }
  }

  return mediaItems;
}

function parseHistoryTimelineEntry(
  record: Record<string, unknown>,
  sessionId: string
): HistoryTimelineEntry | null {
  const role = normalizeHistoryRole(record.role);
  const at = recordTimestampIso(record);

  if (role === 'user') {
    const rawContent = record.content ?? record.text ?? record.body;
    if (isA2UIClientEventContent(rawContent)) {
      return null;
    }
    const content = typeof rawContent === 'string' ? rawContent : String(rawContent ?? '');
    const mediaItems = extractHistoryMediaItems(record);
    if (!content.trim() && mediaItems.length === 0) {
      return null;
    }
    const id =
      pickFirstString(record, ['id', 'message_id', 'msg_id']) ?? `hist-user-${sessionId}-${at}`;
    return {
      kind: 'message',
      message: {
        id,
        role: 'user',
        content,
        timestamp: at,
        ...(mediaItems.length > 0 ? { mediaItems } : {}),
      },
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

  if (eventType === 'team.message') {
    const event = extractTeamEventRecord(record);
    if (!event) {
      return null;
    }
    const teamPayload = { event };
    const id = pickFirstString(event, ['message_id']) ?? `hist-team-message-${sessionId}-${at}`;
    return {
      kind: 'message',
      message: {
        id,
        role: 'system',
        content: `team.event:${JSON.stringify(teamPayload)}`,
        timestamp: at,
      },
    };
  }

  if (eventType === 'team.member' || eventType === 'team.task') {
    const event = extractTeamEventRecord(record);
    if (!event) {
      return null;
    }
    return {
      kind: eventType === 'team.member' ? 'team_member' : 'team_task',
      at,
      payload: { event },
    };
  }

  const payload = buildEventPayloadForRecord(record);

  if (eventType === 'chat.final') {
    const content = normalizeFinalContent(payload);
    if (!content.trim()) {
      return null;
    }
    const id =
      pickFirstString(record, ['id', 'message_id', 'msg_id']) ?? `hist-final-${sessionId}-${at}`;
    if (isTeamModeRecord(record)) {
      if (isHiddenTeamTeammateMessageRecord(record)) {
        return null;
      }
      return {
        kind: 'message',
        message: {
          id: `team-leader-${id}`,
          role: 'system',
          content: `team.leader:${JSON.stringify({
            content,
            timestamp: Date.parse(at),
          })}`,
          timestamp: at,
        },
      };
    }
    // 主动推荐消息：从历史记录还原 source/proactive_type，使刷新后仍按
    // ProactiveRecommendationCard 渲染（否则会退化为普通白色气泡）。
    const histSource = typeof payload.source === 'string' ? payload.source : '';
    const isProactiveRecommendation = histSource === 'proactive_recommendation';
    const histProactiveType = typeof payload.proactive_type === 'string' ? payload.proactive_type : '';
    return {
      kind: 'message',
      message: {
        id,
        role: 'assistant',
        content,
        timestamp: at,
        ...(isProactiveRecommendation ? { isProactiveRecommendation } : {}),
        ...(isProactiveRecommendation && histProactiveType
          ? { proactiveType: histProactiveType as 'skill_recommend' | 'task_reminder' | 'need_exploration' }
          : {}),
      },
    };
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

/**
 * 将磁盘上的 history.json 解析结果（通常为记录数组）转为与历史恢复相同的筛选规则下的消息列表，
 * 并按时间升序返回全部可展示的用户/助手消息。
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

  return messages.sort((a, b) => Date.parse(a.timestamp) - Date.parse(b.timestamp));
}

export function parseHistoryJsonFilePreviewMode(parsed: unknown): 'team' | null {
  if (!Array.isArray(parsed)) {
    return null;
  }

  return parsed.some((item) => isRecord(item) && isTeamModeRecord(item)) ? 'team' : null;
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

/**
 * 仅处理属于当前 `history.get` 会话的帧，避免多标签/乱序下的串台。
 * 无 `session_id` 时：丢弃数据行；仍接受明确的结束帧（兼容未注入 id 的旧链路）。
 */
function shouldProcessHistoryPayload(
  payload: Record<string, unknown>,
  expectedSessionId: string,
  expectedPageIdx?: number
): boolean {
  const sid = typeof payload.session_id === 'string' ? payload.session_id.trim() : '';
  if (sid && sid !== expectedSessionId) {
    return false;
  }
  if (expectedPageIdx !== undefined && payload.page_idx !== expectedPageIdx) {
    return false;
  }
  if (!sid) {
    return isHistoryRestoreDonePayload(payload) || isHistoryBatchEnd(payload);
  }
  return true;
}

export function beginHistoryRestore(options: BeginHistoryRestoreOptions): HistoryRestoreHandle {
  disposeActivePageFetch();
  activeRestore?.dispose();

  const generation = restoreGeneration + 1;
  restoreGeneration = generation;

  const entries: HistoryTimelineEntry[] = [];
  let totalPages: number | null = null;
  let disposed = false;

  const unsubscribe = webClient.on(HISTORY_MESSAGE_EVENT, (event: WsEvent) => {
    if (disposed || generation !== restoreGeneration) {
      return;
    }

    const payload = event.payload;
    if (!shouldProcessHistoryPayload(payload, options.sessionId)) {
      return;
    }

    if (typeof payload.total_pages === 'number' && Number.isFinite(payload.total_pages)) {
      totalPages = payload.total_pages;
    }

    if (isHistoryRestoreDonePayload(payload)) {
      finalize();
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
      finalize();
    }
  });

  function dispose(): void {
    if (disposed) return;
    disposed = true;
    unsubscribe();
    if (activeRestore?.generation === generation) {
      activeRestore = null;
    }
  }

  function finalize(): void {
    if (disposed) return;

    const messages: Message[] = [];
    const toolReplay: HistoryToolReplayItem[] = [];
    const harnessReplay: HistoryHarnessReplayItem[] = [];
    const teamReplay: HistoryTeamReplayItem[] = [];
    let pendingFileItems: FileDownloadItem[] | null = null;
    for (const e of entries) {
      if (e.kind === 'message') {
        if (pendingFileItems && (
          e.message.role === 'assistant' ||
          (e.message.role === 'system' && e.message.id?.startsWith('team-leader-'))
        )) {
          e.message = { ...e.message, fileItems: pendingFileItems };
          pendingFileItems = null;
        }
        messages.push(e.message);
      } else if (e.kind === 'usage_summary') {
        for (let i = messages.length - 1; i >= 0; i--) {
          if (messages[i].role === 'assistant') {
            messages[i] = { ...messages[i], usageSummary: e.usage };
            break;
          }
        }
      } else if (e.kind === 'harness_message') {
        harnessReplay.push({
          kind: 'harness_message',
          at: e.at,
          payload: { content: e.content, stage: e.stage },
        });
        // Also add as system message with harness flag
        messages.push({
          id: `harness-msg-${e.at}`,
          role: 'system',
          content: e.content,
          timestamp: e.at,
          isHarnessMessage: true,
        });
      } else if (e.kind === 'harness_stage_result') {
        harnessReplay.push({
          kind: 'harness_stage_result',
          at: e.at,
          payload: {
            stage: e.stage,
            status: e.status,
            error: e.error,
            messages: e.messages,
            metrics: e.metrics,
          },
        });
      } else if (e.kind === 'file_items') {
        pendingFileItems = e.files;
      } else if (e.kind === 'team_member' || e.kind === 'team_task') {
        teamReplay.push({ kind: e.kind, at: e.at, payload: e.payload });
      } else {
        toolReplay.push({ kind: e.kind, at: e.at, payload: e.payload });
      }
    }

    dispose();

    if (messages.length === 0 && toolReplay.length === 0 && harnessReplay.length === 0 && teamReplay.length === 0) {
      options.onEmpty?.(totalPages);
      return;
    }
    options.onReady(messages, totalPages);
    if (toolReplay.length > 0) {
      options.onToolReplay?.(toolReplay);
    }
    if (harnessReplay.length > 0) {
      options.onHarnessReplay?.(harnessReplay);
    }
    if (teamReplay.length > 0) {
      options.onTeamReplay?.(teamReplay);
    }
  }

  const handle: HistoryRestoreHandle = { generation, dispose };
  activeRestore = handle;
  return handle;
}

export interface FetchHistoryPageResult {
  messages: Message[];
  toolReplay: HistoryToolReplayItem[];
  harnessReplay: HistoryHarnessReplayItem[];
  teamReplay: HistoryTeamReplayItem[];
  totalPages: number | null;
}

export interface FetchHistoryPageOptions {
  sessionId: string;
  pageIdx: number;
  onReady: (result: FetchHistoryPageResult) => void;
  onEmpty?: (totalPages: number | null) => void;
  onError?: (message: string) => void;
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

  const entries: HistoryTimelineEntry[] = [];
  let totalPages: number | null = null;
  let disposed = false;

  const unsubscribe = webClient.on(HISTORY_MESSAGE_EVENT, (event: WsEvent) => {
    if (disposed || generation !== restoreGeneration) {
      return;
    }

    const payload = event.payload;
    if (!shouldProcessHistoryPayload(payload, options.sessionId, options.pageIdx)) {
      return;
    }

    if (typeof payload.total_pages === 'number' && Number.isFinite(payload.total_pages)) {
      totalPages = payload.total_pages;
    }

    if (isHistoryRestoreDonePayload(payload)) {
      finalize();
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
      finalize();
    }
  });

  function dispose(): void {
    if (disposed) return;
    disposed = true;
    unsubscribe();
    activePageFetchDispose = null;
    if (activeRestore?.generation === generation) {
      activeRestore = null;
    }
  }

  function finalize(): void {
    if (disposed) return;

    const messages: Message[] = [];
    const toolReplay: HistoryToolReplayItem[] = [];
    const harnessReplay: HistoryHarnessReplayItem[] = [];
    const teamReplay: HistoryTeamReplayItem[] = [];
    let pendingFileItems: FileDownloadItem[] | null = null;
    for (const e of entries) {
      if (e.kind === 'message') {
        if (pendingFileItems && (
          e.message.role === 'assistant' ||
          (e.message.role === 'system' && e.message.id?.startsWith('team-leader-'))
        )) {
          e.message = { ...e.message, fileItems: pendingFileItems };
          pendingFileItems = null;
        }
        messages.push(e.message);
      } else if (e.kind === 'usage_summary') {
        for (let i = messages.length - 1; i >= 0; i--) {
          if (messages[i].role === 'assistant') {
            messages[i] = { ...messages[i], usageSummary: e.usage };
            break;
          }
        }
      } else if (e.kind === 'harness_message') {
        harnessReplay.push({
          kind: 'harness_message',
          at: e.at,
          payload: { content: e.content, stage: e.stage },
        });
        messages.push({
          id: `harness-msg-${e.at}`,
          role: 'system',
          content: e.content,
          timestamp: e.at,
          isHarnessMessage: true,
        });
      } else if (e.kind === 'harness_stage_result') {
        harnessReplay.push({
          kind: 'harness_stage_result',
          at: e.at,
          payload: {
            stage: e.stage,
            status: e.status,
            error: e.error,
            messages: e.messages,
            metrics: e.metrics,
          },
        });
      } else if (e.kind === 'file_items') {
        pendingFileItems = e.files;
      } else if (e.kind === 'team_member' || e.kind === 'team_task') {
        teamReplay.push({ kind: e.kind, at: e.at, payload: e.payload });
      } else {
        toolReplay.push({ kind: e.kind, at: e.at, payload: e.payload });
      }
    }

    dispose();

    if (messages.length === 0 && toolReplay.length === 0 && harnessReplay.length === 0 && teamReplay.length === 0) {
      options.onEmpty?.(totalPages);
      return;
    }
    options.onReady({ messages, toolReplay, harnessReplay, teamReplay, totalPages });
  }

  const handle: HistoryRestoreHandle = { generation, dispose };
  activeRestore = handle;
  activePageFetchDispose = dispose;
  return handle;
}
