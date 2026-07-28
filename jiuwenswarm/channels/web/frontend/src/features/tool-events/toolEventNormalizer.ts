import { parseSkillTreePath, type SkillTreePath } from '../../types/skillTree';
import {
  parseBeamSearchProgress,
  type BeamSearchProgress,
} from '../../types/beamSearch';

type UnknownPayload = Record<string, unknown>;

function asRecord(value: unknown): UnknownPayload | null {
  if (!value || typeof value !== 'object') {
    return null;
  }
  return value as UnknownPayload;
}

function parseArguments(raw: unknown): Record<string, unknown> {
  if (raw && typeof raw === 'object') {
    return raw as Record<string, unknown>;
  }
  if (typeof raw === 'string') {
    try {
      const parsed = JSON.parse(raw);
      if (parsed && typeof parsed === 'object') {
        return parsed as Record<string, unknown>;
      }
    } catch {
      // ignore: 非 JSON 字符串时保持空对象
    }
  }
  return {};
}

function resolveToolCallId(payload: UnknownPayload, fallback?: UnknownPayload): string | undefined {
  const candidates = [
    payload.id,
    payload.tool_call_id,
    payload.toolCallId,
    fallback?.tool_call_id,
    fallback?.toolCallId,
  ];
  for (const item of candidates) {
    if (typeof item === 'string' && item) {
      return item;
    }
  }
  return undefined;
}

function resolveMemberName(payload: UnknownPayload, fallback?: UnknownPayload): string | undefined {
  const candidates = [
    payload.member_name,
    fallback?.member_name,
  ];
  for (const item of candidates) {
    if (typeof item === 'string' && item.trim()) {
      return item.trim();
    }
  }

  let role = '';
  if (typeof payload.role === 'string') {
    role = payload.role;
  } else if (typeof fallback?.role === 'string') {
    role = fallback.role;
  }
  return role.trim().toLowerCase() === 'teammate' ? 'teammate' : undefined;
}

export interface NormalizedToolCall {
  id: string;
  name: string;
  arguments: Record<string, unknown>;
  description?: string;
  formatted_args?: string;
  memberName?: string;
}

export interface NormalizedToolResult {
  toolName: string;
  toolCallId?: string;
  result: string;
  success: boolean;
  /** Distinct from failure — e.g. sessions_cancel / canceled session task. */
  canceled?: boolean;
  summary?: string;
  skillTree?: SkillTreePath;
  beamSearch?: BeamSearchProgress;
}

export interface NormalizedToolUpdate {
  toolName: string;
  toolCallId?: string;
  beamSearch?: BeamSearchProgress;
}

export function normalizeToolCallPayload(payload: UnknownPayload): NormalizedToolCall {
  const toolCallPayload = asRecord(payload.tool_call) ?? payload;
  const id = resolveToolCallId(toolCallPayload, payload) || `tool-${Date.now()}`;
  const name =
    (typeof toolCallPayload.name === 'string' && toolCallPayload.name) ||
    (typeof payload.tool_name === 'string' && payload.tool_name) ||
    'unknown';
  const description =
    typeof toolCallPayload.description === 'string'
      ? toolCallPayload.description
      : undefined;
  const formatted_args =
    typeof toolCallPayload.formatted_args === 'string'
      ? toolCallPayload.formatted_args
      : undefined;
  const memberName = resolveMemberName(toolCallPayload, payload);

  return {
    id,
    name,
    arguments: parseArguments(toolCallPayload.arguments),
    description,
    formatted_args,
    memberName,
  };
}

export function normalizeToolResultPayload(payload: UnknownPayload): NormalizedToolResult {
  const toolResultPayload = asRecord(payload.tool_result) ?? payload;
  const rawOutputRecord =
    asRecord(toolResultPayload.raw_output) ?? asRecord(toolResultPayload.rawOutput);
  const rawOutputResult =
    typeof rawOutputRecord?.result === 'string'
      ? rawOutputRecord.result
      : undefined;
  const result =
    rawOutputResult ||
    (typeof toolResultPayload.result === 'string' &&
      toolResultPayload.result) ||
    (toolResultPayload.data != null ? String(toolResultPayload.data) : '') ||
    (typeof toolResultPayload.error === 'string'
      ? toolResultPayload.error
      : '');
  const status =
    typeof toolResultPayload.status === 'string'
      ? toolResultPayload.status
      : '';
  const dataRecord =
    asRecord(rawOutputRecord?.data) ?? asRecord(toolResultPayload.data);
  const dataStatus =
    typeof dataRecord?.status === 'string' ? dataRecord.status : '';
  const canceled =
    toolResultPayload.canceled === true ||
    status === 'canceled' ||
    status === 'cancelled' ||
    dataStatus === 'canceled' ||
    dataStatus === 'cancelled';
  const success =
    typeof toolResultPayload.success === 'boolean'
      ? toolResultPayload.success
      : status
        ? status !== 'error'
        : true;
  const toolName =
    (typeof toolResultPayload.tool_name === 'string' &&
      toolResultPayload.tool_name) ||
    (typeof toolResultPayload.name === 'string' &&
      toolResultPayload.name) ||
    'unknown';
  const toolCallId = resolveToolCallId(toolResultPayload, payload);
  const summary =
    typeof toolResultPayload.summary === 'string'
      ? toolResultPayload.summary
      : canceled
        ? undefined
        : success
          ? undefined
          : '❌';
  const skillTree =
    parseSkillTreePath(toolResultPayload.raw_output) ??
    parseSkillTreePath(toolResultPayload.rawOutput);
  const beamSearch =
    parseBeamSearchProgress(rawOutputRecord?.beam_search);

  return {
    toolName,
    toolCallId,
    result,
    success,
    canceled: canceled || undefined,
    summary,
    skillTree,
    beamSearch,
  };
}

export function normalizeToolUpdatePayload(payload: UnknownPayload): NormalizedToolUpdate {
  const update = asRecord(payload.tool_update) ?? payload;
  return {
    toolName:
      (typeof update.tool_name === 'string' && update.tool_name) || 'unknown',
    toolCallId: resolveToolCallId(update, payload),
    beamSearch: parseBeamSearchProgress(update.beam_search_event),
  };
}

/** Collapsed session-result card label: show result preview, not only “完成”. */
export function previewSessionResultSummary(result: string, maxLen = 120): string {
  const text = (result || '').replace(/\s+/g, ' ').trim();
  if (!text) return '完成';
  return text.length > maxLen ? `${text.slice(0, maxLen)}…` : text;
}

export type SessionResultApplyInput = {
  sessionId: string;
  payload: UnknownPayload;
  at?: string;
  /** Prefix for synthetic toolCallId; live uses `session-`, history uses `session-hist-`. */
  toolCallIdPrefix?: string;
};

/**
 * Apply a chat.session_result payload: flip 最近任务 + synthesize tool card pair.
 * Shared by live WS and history restore so the two paths cannot drift.
 */
export function buildSessionResultReplay(input: SessionResultApplyInput): {
  taskId: string;
  description: string;
  result: string;
  status: 'completed' | 'error' | 'canceled';
  index: number;
  total: number;
  isParallel: boolean;
  toolCallId: string;
  fullResult: string;
  summary: string;
} {
  const { payload } = input;
  const description =
    typeof payload.description === 'string' ? payload.description : '';
  const result = typeof payload.result === 'string' ? payload.result : '';
  const taskId =
    typeof payload.task_id === 'string' && payload.task_id.trim()
      ? payload.task_id.trim()
      : `session-${input.at || Date.now()}`;
  const payloadStatus =
    typeof payload.status === 'string' ? payload.status : 'completed';
  const prefix = input.toolCallIdPrefix ?? 'session-';
  const fullResult = description
    ? `描述: ${description}\n\n结果: ${result}`
    : result;
  let status: 'completed' | 'error' | 'canceled' = 'completed';
  if (payloadStatus === 'error') {
    status = 'error';
  } else if (payloadStatus === 'canceled' || payloadStatus === 'cancelled') {
    status = 'canceled';
  }
  return {
    taskId,
    description,
    result,
    status,
    index: typeof payload.index === 'number' ? payload.index : 0,
    total: typeof payload.total === 'number' ? payload.total : 1,
    isParallel: payload.is_parallel === true,
    toolCallId: `${prefix}${taskId}`,
    fullResult,
    summary: previewSessionResultSummary(result),
  };
}
