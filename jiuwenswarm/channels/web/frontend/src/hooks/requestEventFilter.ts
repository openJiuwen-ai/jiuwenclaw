import type { WsEvent } from '../types';

export interface ShouldHandleRequestEventOptions {
  activeRequestId?: string | null;
  expectedRequestId?: string;
  /** 当前连接发出的 `chat.interrupt` 请求的 ws id（与 chat.send 链路的 id 不属于同一枚举） */
  pendingInterruptRequestIds?: ReadonlySet<string>;
}

/**
 * 决定一条流式事件是否属于当前 tab 的活跃请求。
 *
 * - 无 request_id 的事件一律放行（后端不给 chat.delta/final 注入 request_id）。
 * - interrupt_result 只认本 tab 发出的 interrupt 请求 id，其余一律拒绝。
 * - 其余带 request_id 的事件，必须匹配 activeRequestId（或 expectedRequestId）才放行。
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
