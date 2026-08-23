import { parseSkillTreePath, type SkillTreePath } from '../../types/skillTree';
import { parseBeamSearchProgress, type BeamSearchProgress } from '../../types/beamSearch';
import type { AutoReviewerMetadata } from '../../types';
import {
  effectiveReviewerStatus,
  normalizeReviewerMetadata,
  reviewerIndicatesFailure,
} from './reviewerMetadata';

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

function isPermissionFailureResult(result: string): boolean {
  const normalized = result.trim();
  return normalized.startsWith('[PERMISSION_DENIED]') || normalized.startsWith('[PERMISSION_REJECTED]') || normalized.startsWith('[PERMISSION_BLOCKED]');
}

function isFailureStatus(status: string): boolean {
  return ['error', 'failed', 'failure', 'rejected', 'denied', 'blocked'].includes(status.trim().toLowerCase());
}

function metadataIndicatesPermissionFailure(payload: UnknownPayload, toolResultPayload: UnknownPayload, reviewer?: AutoReviewerMetadata): boolean {
  const metadata = asRecord(payload.metadata);
  const values = [
    toolResultPayload.permission_decision,
    toolResultPayload.permission_status,
    payload.permission_decision,
    payload.permission_status,
    metadata?.permission_decision,
    metadata?.permission_status,
  ];
  return (
    reviewerIndicatesFailure(reviewer) ||
    values.some(value => {
      const normalized = typeof value === 'string' ? value.trim().toLowerCase() : '';
      return ['deny', 'denied', 'reject', 'rejected', 'blocked'].includes(normalized);
    })
  );
}

function resolveToolCallId(payload: UnknownPayload, fallback?: UnknownPayload): string | undefined {
  const candidates = [payload.id, payload.tool_call_id, payload.toolCallId, fallback?.tool_call_id, fallback?.toolCallId];
  for (const item of candidates) {
    if (typeof item === 'string' && item) {
      return item;
    }
  }
  return undefined;
}

function resolveMemberName(payload: UnknownPayload, fallback?: UnknownPayload): string | undefined {
  const candidates = [payload.member_name, fallback?.member_name];
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
  /** 后端下发的可读展示名（部分工具带），前端优先直接展示，省去本地推断。 */
  display_name?: string;
  memberName?: string;
  reviewer?: AutoReviewerMetadata;
}

export interface NormalizedToolResult {
  toolName: string;
  toolCallId?: string;
  result: string;
  success: boolean;
  /** status=timeout / timed_out 时为 true，供 store 落成 timeout */
  timedOut?: boolean;
  summary?: string;
  skillTree?: SkillTreePath;
  beamSearch?: BeamSearchProgress;
  reviewer?: AutoReviewerMetadata;
}

export interface NormalizedToolUpdate {
  toolName: string;
  toolCallId?: string;
  beamSearch?: BeamSearchProgress;
  reviewer?: AutoReviewerMetadata;
}

export function normalizeToolCallPayload(payload: UnknownPayload): NormalizedToolCall {
  const toolCallPayload = asRecord(payload.tool_call) ?? payload;
  const id = resolveToolCallId(toolCallPayload, payload) || `tool-${Date.now()}`;
  const name = (typeof toolCallPayload.name === 'string' && toolCallPayload.name) || (typeof payload.tool_name === 'string' && payload.tool_name) || 'unknown';
  const description = typeof toolCallPayload.description === 'string' ? toolCallPayload.description : undefined;
  const formatted_args = typeof toolCallPayload.formatted_args === 'string' ? toolCallPayload.formatted_args : undefined;
  const displayNameRaw =
    (typeof toolCallPayload.display_name === 'string' && toolCallPayload.display_name) ||
    (typeof toolCallPayload.displayName === 'string' && toolCallPayload.displayName) ||
    '';
  const display_name = displayNameRaw.trim() || undefined;
  const memberName = resolveMemberName(toolCallPayload, payload);

  return {
    id,
    name,
    arguments: parseArguments(toolCallPayload.arguments),
    description,
    formatted_args,
    display_name,
    memberName,
    reviewer: normalizeReviewerMetadata(payload),
  };
}

export function normalizeToolResultPayload(payload: UnknownPayload): NormalizedToolResult {
  const toolResultPayload = asRecord(payload.tool_result) ?? payload;
  const rawOutputRecord = asRecord(toolResultPayload.raw_output) ?? asRecord(toolResultPayload.rawOutput);
  const rawOutputResult = typeof rawOutputRecord?.result === 'string' ? rawOutputRecord.result : undefined;
  const result =
    rawOutputResult ||
    (typeof toolResultPayload.result === 'string' && toolResultPayload.result) ||
    (toolResultPayload.data != null ? String(toolResultPayload.data) : '') ||
    (typeof toolResultPayload.error === 'string' ? toolResultPayload.error : '');
  const status =
    typeof toolResultPayload.status === 'string'
      ? toolResultPayload.status.trim().toLowerCase()
      : '';
  const timedOut = status === 'timeout' || status === 'timed_out';
  const statusFailed = timedOut || isFailureStatus(status);
  const reviewer = normalizeReviewerMetadata(payload) ?? normalizeReviewerMetadata(toolResultPayload);
  const reviewerStatus = effectiveReviewerStatus(reviewer);
  const hasExplicitSuccess = typeof toolResultPayload.success === 'boolean';
  const trustedApproval = reviewerStatus === 'approved' || reviewerStatus === 'deterministic_allow';
  const legacyMarkerFailure =
    !hasExplicitSuccess && !trustedApproval && isPermissionFailureResult(result);
  const permissionFailure =
    legacyMarkerFailure || metadataIndicatesPermissionFailure(payload, toolResultPayload, reviewer);
  const success =
    hasExplicitSuccess
      ? toolResultPayload.success === true && !statusFailed && !permissionFailure
      : status
        ? !statusFailed && !permissionFailure
        : !permissionFailure;
  const toolName =
    (typeof toolResultPayload.tool_name === 'string' && toolResultPayload.tool_name) ||
    (typeof toolResultPayload.name === 'string' && toolResultPayload.name) ||
    'unknown';
  const toolCallId = resolveToolCallId(toolResultPayload, payload);
  const summary = typeof toolResultPayload.summary === 'string' ? toolResultPayload.summary : success ? undefined : '❌';
  const skillTree = parseSkillTreePath(toolResultPayload.raw_output) ?? parseSkillTreePath(toolResultPayload.rawOutput);
  const beamSearch = parseBeamSearchProgress(rawOutputRecord?.beam_search);

  return {
    toolName,
    toolCallId,
    result,
    success,
    ...(timedOut ? { timedOut: true } : {}),
    summary,
    skillTree,
    beamSearch,
    reviewer,
  };
}

export function normalizeToolUpdatePayload(payload: UnknownPayload): NormalizedToolUpdate {
  const update = asRecord(payload.tool_update) ?? payload;
  return {
    toolName: (typeof update.tool_name === 'string' && update.tool_name) || 'unknown',
    toolCallId: resolveToolCallId(update, payload),
    beamSearch: parseBeamSearchProgress(update.beam_search_event),
    reviewer: normalizeReviewerMetadata(update) ?? normalizeReviewerMetadata(payload),
  };
}
