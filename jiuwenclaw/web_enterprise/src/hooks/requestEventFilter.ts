import type { WsEvent } from '../types';

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
