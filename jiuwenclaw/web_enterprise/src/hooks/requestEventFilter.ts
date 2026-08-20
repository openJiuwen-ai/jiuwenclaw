import type { WsEvent } from '../types';

interface ApprovalQuestionLike {
  request_id?: string;
  source?: string;
}

const SUBAGENT_APPROVAL_SOURCES = new Set([
  'subagent_skill_load',
  'subagent_tool_permission',
]);

export function isSubagentApprovalQuestion(
  question: ApprovalQuestionLike | null | undefined
): boolean {
  return Boolean(question && SUBAGENT_APPROVAL_SOURCES.has(question.source ?? ''));
}

export function isMatchingSubagentApprovalExpiry(
  pendingQuestion: ApprovalQuestionLike | null | undefined,
  expiryPayload: Record<string, unknown>
): boolean {
  if (!isSubagentApprovalQuestion(pendingQuestion)) {
    return false;
  }
  const requestId =
    typeof expiryPayload.request_id === 'string' ? expiryPayload.request_id.trim() : '';
  const source =
    typeof expiryPayload.source === 'string' ? expiryPayload.source.trim() : '';
  return Boolean(
    requestId &&
      requestId === pendingQuestion?.request_id?.trim() &&
      source === pendingQuestion?.source
  );
}

export function shouldTreatInvocationPausedAsSubagentTerminal(
  sawSubagentApproval: boolean,
  pendingQuestion: ApprovalQuestionLike | null | undefined
): boolean {
  if (!sawSubagentApproval) {
    return false;
  }
  return pendingQuestion == null || isSubagentApprovalQuestion(pendingQuestion);
}

interface TerminalCorrelation {
  activeRequestId?: string | null;
  activeSessionId?: string | null;
  eventRequestId?: string | null;
  eventSessionId?: string | null;
}

export function doesTerminalTargetActiveRequest({
  activeRequestId,
  activeSessionId,
  eventRequestId,
  eventSessionId,
}: TerminalCorrelation): boolean {
  const activeRid = activeRequestId?.trim() ?? '';
  if (!activeRid) {
    return false;
  }
  const eventRid = eventRequestId?.trim() ?? '';
  if (eventRid) {
    return eventRid === activeRid;
  }
  const activeSession = activeSessionId?.trim() ?? '';
  const terminalSession = eventSessionId?.trim() ?? '';
  return Boolean(activeSession && terminalSession === activeSession);
}

export interface ShouldHandleRequestEventOptions {
  activeRequestId?: string | null;
  expectedRequestId?: string;
  /** 当前连接发出的 `chat.interrupt` 请求的 ws id（与 chat.send 链路的 id 不属于同一枚举） */
  pendingInterruptRequestIds?: ReadonlySet<string>;
}

/**
 * `request_id` 由后端透出时：
 * - 先匹配 pending interrupt（与用户中断请求的 id 对齐）
 * - `chat.interrupt_result`：仅能通过 pendingInterrupt 或非空 request_id（无 id 时兼容旧链路），不再误用 chat 侧的 activeRequestId
 * - 其余事件：对照 expectedRequestId 或当前 active chat request id（无期望值且仍无 id → 放行，兼容）
 */
export function shouldHandleRequestEvent(
  event: WsEvent,
  options: ShouldHandleRequestEventOptions = {}
): boolean {
  const eventIdRaw = event.request_id;
  const rid = typeof eventIdRaw === 'string' ? eventIdRaw.trim() : '';
  if (!rid) {
    return true;
  }

  const pending = options.pendingInterruptRequestIds;
  if (pending?.has(rid)) {
    return true;
  }

  if (event.event === 'chat.interrupt_result') {
    return false;
  }

  const expected =
    options.expectedRequestId ??
    options.activeRequestId ??
    null;

  if (!expected) {
    return true;
  }

  return rid === expected;
}
