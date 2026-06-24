const HISTORY_RESTORE_DONE_CONTENT = 'done';

function isHistoryRestoreDoneContent(rawContent: unknown): boolean {
  if (typeof rawContent !== 'string') {
    return false;
  }
  return rawContent.trim().toLowerCase() === HISTORY_RESTORE_DONE_CONTENT;
}

function isHistoryRestoreDonePayload(payload: Record<string, unknown>): boolean {
  const rawStatus = payload.status;
  if (
    typeof rawStatus === 'string' &&
    rawStatus.trim().toLowerCase() === HISTORY_RESTORE_DONE_CONTENT
  ) {
    return true;
  }
  return isHistoryRestoreDoneContent(payload.content);
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
 * 若提供了 `expectedRequestId`，则同时验证 `request_id` 是否匹配。
 */
export function shouldProcessHistoryPayload(
  payload: Record<string, unknown>,
  expectedSessionId: string,
  expectedRequestId?: string
): boolean {
  const sid = typeof payload.session_id === 'string' ? payload.session_id.trim() : '';
  if (sid && sid !== expectedSessionId) {
    return false;
  }

  if (expectedRequestId) {
    const rid = typeof payload.request_id === 'string' ? payload.request_id.trim() : '';
    if (rid && rid !== expectedRequestId) {
      return false;
    }
  }

  if (!sid) {
    const isEnd = isHistoryRestoreDonePayload(payload) || isHistoryBatchEnd(payload);
    if (!isEnd) {
      return false;
    }
    if (expectedRequestId) {
      const rid = typeof payload.request_id === 'string' ? payload.request_id.trim() : '';
      return !rid || rid === expectedRequestId;
    }
    return true;
  }
  return true;
}
