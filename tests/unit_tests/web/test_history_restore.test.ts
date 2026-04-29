import { describe, it, expect } from 'vitest';
import { shouldProcessHistoryPayload } from '../../../jiuwenclaw/web/src/features/historyRestoreFilter';

describe('History Restore - Request ID Filtering', () => {
  describe('shouldProcessHistoryPayload', () => {
    it('should accept payload with matching session_id and request_id', () => {
      const payload = {
        session_id: 'sess_123',
        request_id: 'req_456',
        content: 'test message',
      };
      
      expect(shouldProcessHistoryPayload(payload, 'sess_123', 'req_456')).toBe(true);
    });

    it('should reject payload with mismatched request_id', () => {
      const payload = {
        session_id: 'sess_123',
        request_id: 'req_456',
        content: 'test message',
      };
      
      expect(shouldProcessHistoryPayload(payload, 'sess_123', 'req_789')).toBe(false);
    });

    it('should reject payload with mismatched session_id', () => {
      const payload = {
        session_id: 'sess_123',
        request_id: 'req_456',
        content: 'test message',
      };
      
      expect(shouldProcessHistoryPayload(payload, 'sess_999', 'req_456')).toBe(false);
    });

    it('should accept payload without request_id when expectedRequestId is not provided', () => {
      const payload = {
        session_id: 'sess_123',
        content: 'test message',
      };
      
      expect(shouldProcessHistoryPayload(payload, 'sess_123')).toBe(true);
    });

    it('should accept done payload even without session_id', () => {
      const payload = {
        status: 'done',
      };
      
      expect(shouldProcessHistoryPayload(payload, 'sess_123', 'req_456')).toBe(true);
    });

    it('should reject data payload without session_id', () => {
      const payload = {
        content: 'test message',
      };
      
      expect(shouldProcessHistoryPayload(payload, 'sess_123', 'req_456')).toBe(false);
    });
  });
});
