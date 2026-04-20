import { describe, it, expect } from 'vitest';
import { shouldHandleRequestEvent } from '../../../jiuwenclaw/web/src/hooks/requestEventFilter';
import type { WsEvent } from '../../../jiuwenclaw/web/src/types';

describe('WebSocket Event Filtering', () => {
  describe('shouldHandleRequestEvent', () => {
    it('should accept event with matching request_id', () => {
      const event: WsEvent = {
        type: 'event',
        event: 'chat.delta',
        payload: { content: 'test' },
        request_id: 'req_123',
      };
      
      expect(shouldHandleRequestEvent(event, { expectedRequestId: 'req_123' })).toBe(true);
    });

    it('should reject event with mismatched request_id', () => {
      const event: WsEvent = {
        type: 'event',
        event: 'chat.delta',
        payload: { content: 'test' },
        request_id: 'req_123',
      };
      
      expect(shouldHandleRequestEvent(event, { expectedRequestId: 'req_456' })).toBe(false);
    });

    it('should accept event without request_id when expectedRequestId is not provided', () => {
      const event: WsEvent = {
        type: 'event',
        event: 'chat.delta',
        payload: { content: 'test' },
      };
      
      expect(shouldHandleRequestEvent(event)).toBe(true);
    });

    it('should accept event without request_id even when expectedRequestId is provided (backward compatibility)', () => {
      const event: WsEvent = {
        type: 'event',
        event: 'chat.delta',
        payload: { content: 'test' },
      };
      
      expect(shouldHandleRequestEvent(event, { expectedRequestId: 'req_123' })).toBe(true);
    });

    it('should accept interrupt_result for current session even when active request id differs', () => {
      const event: WsEvent = {
        type: 'event',
        event: 'chat.interrupt_result',
        payload: { intent: 'pause', success: true },
        request_id: 'interrupt-req-123',
      };

      expect(
        shouldHandleRequestEvent(event, {
          activeRequestId: 'chat-req-456',
        })
      ).toBe(true);
    });

    it('should still respect explicit request id for interrupt_result when provided', () => {
      const event: WsEvent = {
        type: 'event',
        event: 'chat.interrupt_result',
        payload: { intent: 'pause', success: true },
        request_id: 'interrupt-req-123',
      };

      expect(
        shouldHandleRequestEvent(event, {
          activeRequestId: 'chat-req-456',
          expectedRequestId: 'interrupt-req-123',
        })
      ).toBe(true);
      expect(
        shouldHandleRequestEvent(event, {
          activeRequestId: 'chat-req-456',
          expectedRequestId: 'interrupt-req-999',
        })
      ).toBe(false);
    });
  });

  describe('Multi-tab scenario simulation', () => {
    it('should filter events correctly for multiple tabs with same sessionID', () => {
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

    it('should handle history.message events correctly for multiple tabs', () => {
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
