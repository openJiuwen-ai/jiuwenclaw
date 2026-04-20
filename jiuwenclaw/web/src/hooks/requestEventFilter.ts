import type { WsEvent } from '../types';

export interface ShouldHandleRequestEventOptions {
  activeRequestId?: string | null;
  expectedRequestId?: string;
}

export function shouldHandleRequestEvent(
  event: WsEvent,
  options: ShouldHandleRequestEventOptions = {}
): boolean {
  let expectedRequestId = options.expectedRequestId;
  if (!expectedRequestId && event.event !== 'chat.interrupt_result') {
    const currentRequestId = options.activeRequestId;
    if (currentRequestId) {
      expectedRequestId = currentRequestId;
    }
  }

  if (!expectedRequestId) {
    return true;
  }

  const eventId = event.request_id;
  if (!eventId) {
    return true;
  }

  return eventId === expectedRequestId;
}
