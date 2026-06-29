import { parseSkillTreePath, type SkillTreePath } from '../../types/skillTree';

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
  summary?: string;
  skillTree?: SkillTreePath;
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
      : success ? undefined : '❌';
  const skillTree =
    parseSkillTreePath(toolResultPayload.raw_output) ??
    parseSkillTreePath(toolResultPayload.rawOutput);

  return {
    toolName,
    toolCallId,
    result,
    success,
    summary,
    skillTree,
  };
}
