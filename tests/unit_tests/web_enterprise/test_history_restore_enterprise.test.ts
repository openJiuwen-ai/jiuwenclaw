import { describe, it, expect } from 'vitest';
import { shouldProcessHistoryPayload } from '../../../jiuwenclaw/web_enterprise/src/features/historyRestoreFilter';

describe('Enterprise History Restore Filter', () => {
  describe('shouldProcessHistoryPayload', () => {
    it('accepts payload with matching session_id and request_id', () => {
      const payload = {
        session_id: 'sess_123',
        request_id: 'req_456',
        content: 'test message',
      };

      expect(shouldProcessHistoryPayload(payload, 'sess_123', 'req_456')).toBe(true);
    });

    it('rejects payload with mismatched request_id when session_id matches', () => {
      const payload = {
        session_id: 'sess_123',
        request_id: 'req_456',
        content: 'test message',
      };

      expect(shouldProcessHistoryPayload(payload, 'sess_123', 'req_789')).toBe(false);
    });

    it('rejects payload with mismatched session_id', () => {
      const payload = {
        session_id: 'sess_123',
        request_id: 'req_456',
        content: 'test message',
      };

      expect(shouldProcessHistoryPayload(payload, 'sess_999', 'req_456')).toBe(false);
    });

    it('accepts payload without request_id when expectedRequestId is not provided', () => {
      const payload = {
        session_id: 'sess_123',
        content: 'test message',
      };

      expect(shouldProcessHistoryPayload(payload, 'sess_123')).toBe(true);
    });

    it('accepts payload with session_id but missing request_id when expectedRequestId is provided', () => {
      const payload = {
        session_id: 'sess_123',
        content: 'test message',
      };

      expect(shouldProcessHistoryPayload(payload, 'sess_123', 'req_456')).toBe(true);
    });

    it('rejects data payload without session_id', () => {
      const payload = {
        content: 'test message',
      };

      expect(shouldProcessHistoryPayload(payload, 'sess_123', 'req_456')).toBe(false);
    });

    it('accepts done payload without session_id when request_id is absent', () => {
      const payload = {
        status: 'done',
      };

      expect(shouldProcessHistoryPayload(payload, 'sess_123', 'req_456')).toBe(true);
    });

    it('accepts done payload without session_id when request_id matches', () => {
      const payload = {
        status: 'done',
        request_id: 'req_456',
      };

      expect(shouldProcessHistoryPayload(payload, 'sess_123', 'req_456')).toBe(true);
    });

    it('rejects done payload without session_id when request_id mismatches', () => {
      const payload = {
        status: 'done',
        request_id: 'req_other',
      };

      expect(shouldProcessHistoryPayload(payload, 'sess_123', 'req_456')).toBe(false);
    });

    it('accepts content done payload without session_id', () => {
      const payload = {
        content: ' done ',
      };

      expect(shouldProcessHistoryPayload(payload, 'sess_123', 'req_456')).toBe(true);
    });

    it.each([
      ['done', { done: true }],
      ['last', { last: true }],
      ['is_last', { is_last: true }],
      ['page_complete', { page_complete: true }],
      ['end', { end: true }],
    ])('accepts batch end marker %s without session_id', (_label, payload) => {
      expect(shouldProcessHistoryPayload(payload, 'sess_123', 'req_456')).toBe(true);
    });

    it('trims session_id and request_id before comparison', () => {
      const payload = {
        session_id: '  sess_123  ',
        request_id: '  req_456  ',
        content: 'test message',
      };

      expect(shouldProcessHistoryPayload(payload, 'sess_123', 'req_456')).toBe(true);
    });
  });
});
