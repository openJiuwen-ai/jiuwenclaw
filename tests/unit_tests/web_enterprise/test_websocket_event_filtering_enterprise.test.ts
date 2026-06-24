import { describe, it, expect } from 'vitest';
import { shouldHandleRequestEvent } from '../../../jiuwenclaw/web_enterprise/src/hooks/requestEventFilter';
import type { WsEvent } from '../../../jiuwenclaw/web_enterprise/src/types';

describe('Enterprise WebSocket Event Filtering', () => {
  describe('shouldHandleRequestEvent', () => {
    it('accepts event with matching request_id to expectedRequestId', () => {
      const event: WsEvent = {
        type: 'event',
        event: 'chat.delta',
        payload: { content: 'test' },
        request_id: 'req_123',
      };

      expect(shouldHandleRequestEvent(event, { expectedRequestId: 'req_123' })).toBe(true);
    });

    it('rejects event with mismatched request_id', () => {
      const event: WsEvent = {
        type: 'event',
        event: 'chat.delta',
        payload: { content: 'test' },
        request_id: 'req_123',
      };

      expect(shouldHandleRequestEvent(event, { expectedRequestId: 'req_456' })).toBe(false);
    });

    it('accepts event without request_id (backward compatibility)', () => {
      const event: WsEvent = {
        type: 'event',
        event: 'chat.delta',
        payload: { content: 'test' },
      };

      expect(shouldHandleRequestEvent(event)).toBe(true);
      expect(shouldHandleRequestEvent(event, { expectedRequestId: 'req_123' })).toBe(true);
    });

    it('accepts event with whitespace-only request_id as missing id', () => {
      const event: WsEvent = {
        type: 'event',
        event: 'chat.delta',
        payload: { content: 'test' },
        request_id: '   ',
      };

      expect(shouldHandleRequestEvent(event, { expectedRequestId: 'req_123' })).toBe(true);
    });

    it('trims request_id before comparison', () => {
      const event: WsEvent = {
        type: 'event',
        event: 'chat.delta',
        payload: { content: 'test' },
        request_id: '  req_123  ',
      };

      expect(shouldHandleRequestEvent(event, { expectedRequestId: 'req_123' })).toBe(true);
    });

    it('matches activeRequestId for non-interrupt events', () => {
      const event: WsEvent = {
        type: 'event',
        event: 'chat.delta',
        payload: { content: 'test' },
        request_id: 'chat-1',
      };

      expect(shouldHandleRequestEvent(event, { activeRequestId: 'chat-1' })).toBe(true);
      expect(shouldHandleRequestEvent(event, { activeRequestId: 'chat-2' })).toBe(false);
    });

    it('prioritizes expectedRequestId over activeRequestId', () => {
      const event: WsEvent = {
        type: 'event',
        event: 'chat.delta',
        payload: { content: 'test' },
        request_id: 'req_expected',
      };

      expect(
        shouldHandleRequestEvent(event, {
          expectedRequestId: 'req_expected',
          activeRequestId: 'req_active',
        })
      ).toBe(true);
      expect(
        shouldHandleRequestEvent(event, {
          expectedRequestId: 'req_other',
          activeRequestId: 'req_expected',
        })
      ).toBe(false);
    });

    it('accepts events with request_id when no expected or active id is set', () => {
      const event: WsEvent = {
        type: 'event',
        event: 'chat.delta',
        payload: { content: 'test' },
        request_id: 'req_orphan',
      };

      expect(shouldHandleRequestEvent(event)).toBe(true);
    });

    it('accepts interrupt_result when request_id is in pendingInterruptRequestIds', () => {
      const pending = new Set(['intr-1']);
      const event: WsEvent = {
        type: 'event',
        event: 'chat.interrupt_result',
        payload: { intent: 'pause', success: true },
        request_id: 'intr-1',
      };

      expect(
        shouldHandleRequestEvent(event, {
          activeRequestId: 'chat-xyz',
          pendingInterruptRequestIds: pending,
        })
      ).toBe(true);
    });

    it('rejects interrupt_result when request_id does not belong to this tab', () => {
      const event: WsEvent = {
        type: 'event',
        event: 'chat.interrupt_result',
        payload: { intent: 'pause', success: true },
        request_id: 'intr-other-tab',
      };

      expect(
        shouldHandleRequestEvent(event, {
          activeRequestId: 'chat-xyz',
          pendingInterruptRequestIds: new Set(['intr-own']),
        })
      ).toBe(false);
    });

    it('rejects interrupt_result even when request_id matches activeRequestId', () => {
      const event: WsEvent = {
        type: 'event',
        event: 'chat.interrupt_result',
        payload: { intent: 'pause', success: true },
        request_id: 'chat-xyz',
      };

      expect(
        shouldHandleRequestEvent(event, {
          activeRequestId: 'chat-xyz',
          pendingInterruptRequestIds: new Set(['intr-own']),
        })
      ).toBe(false);
    });

    it('accepts interrupt_result without request_id for old servers', () => {
      const event: WsEvent = {
        type: 'event',
        event: 'chat.interrupt_result',
        payload: { intent: 'pause', success: true },
      };

      expect(shouldHandleRequestEvent(event, { activeRequestId: 'chat-1' })).toBe(true);
    });

    it('prioritizes pendingInterrupt match for any event type', () => {
      const pending = new Set(['mixed-1']);
      const evt: WsEvent = {
        type: 'event',
        event: 'chat.processing_status',
        payload: { session_id: 's', is_processing: false },
        request_id: 'mixed-1',
      };

      expect(
        shouldHandleRequestEvent(evt, {
          activeRequestId: 'chat-2',
          pendingInterruptRequestIds: pending,
        })
      ).toBe(true);
    });
  });

  describe('multi-tab scenarios', () => {
    it('filters chat deltas by request_id per logical tab expectation', () => {
      const tabARequestId = 'chat-tab-a-123';
      const tabBRequestId = 'chat-tab-b-456';

      const eventForA: WsEvent = {
        type: 'event',
        event: 'chat.delta',
        payload: { content: 'Message for Tab A' },
        request_id: tabARequestId,
      };

      const eventForB: WsEvent = {
        type: 'event',
        event: 'chat.delta',
        payload: { content: 'Message for Tab B' },
        request_id: tabBRequestId,
      };

      expect(shouldHandleRequestEvent(eventForA, { expectedRequestId: tabARequestId })).toBe(true);
      expect(shouldHandleRequestEvent(eventForA, { expectedRequestId: tabBRequestId })).toBe(false);
      expect(shouldHandleRequestEvent(eventForB, { expectedRequestId: tabARequestId })).toBe(false);
      expect(shouldHandleRequestEvent(eventForB, { expectedRequestId: tabBRequestId })).toBe(true);
    });

    it('filters history.message by expectedRequestId when used as proxy for tab expectation', () => {
      const tabARequestId = 'history-tab-a-123';
      const tabBRequestId = 'history-tab-b-456';

      const historyEventForA: WsEvent = {
        type: 'event',
        event: 'history.message',
        payload: {
          session_id: 'sess_shared',
          content: 'History for Tab A',
          page_idx: 1,
        },
        request_id: tabARequestId,
      };

      const historyEventForB: WsEvent = {
        type: 'event',
        event: 'history.message',
        payload: {
          session_id: 'sess_shared',
          content: 'History for Tab B',
          page_idx: 2,
        },
        request_id: tabBRequestId,
      };

      expect(shouldHandleRequestEvent(historyEventForA, { expectedRequestId: tabARequestId })).toBe(true);
      expect(shouldHandleRequestEvent(historyEventForA, { expectedRequestId: tabBRequestId })).toBe(false);
      expect(shouldHandleRequestEvent(historyEventForB, { expectedRequestId: tabARequestId })).toBe(false);
      expect(shouldHandleRequestEvent(historyEventForB, { expectedRequestId: tabBRequestId })).toBe(true);
    });
  });
});
