import { describe, expect, it } from 'vitest';
import {
  isDbQueryTimeoutMessage,
  normalizeUserFacingError,
} from '../../../jiuwenclaw/web_enterprise/src/utils/userFacingError';

const labels = {
  dbQueryTimeout: '数据库查询超时，请稍后重试',
  requestTimeout: '请求超时',
};

describe('userFacingError', () => {
  it('detects MySQL MAX_EXECUTION_TIME errors', () => {
    const raw =
      "(3024, 'Query execution was interrupted, maximum statement execution time exceeded')";
    expect(isDbQueryTimeoutMessage(raw)).toBe(true);
    expect(normalizeUserFacingError(raw, labels)).toBe(labels.dbQueryTimeout);
  });

  it('detects PostgreSQL statement timeout errors', () => {
    const raw = 'canceling statement due to statement timeout';
    expect(isDbQueryTimeoutMessage(raw)).toBe(true);
    expect(normalizeUserFacingError(raw, labels)).toBe(labels.dbQueryTimeout);
  });

  it('detects bare TimeoutError / database query timeout labels', () => {
    expect(isDbQueryTimeoutMessage('TimeoutError')).toBe(true);
    expect(isDbQueryTimeoutMessage('database query timeout')).toBe(true);
    expect(normalizeUserFacingError('database query timeout', labels)).toBe(
      labels.dbQueryTimeout
    );
  });

  it('passes through unrelated errors', () => {
    const raw = '模型未正确配置，请先配置模型信息';
    expect(isDbQueryTimeoutMessage(raw)).toBe(false);
    expect(normalizeUserFacingError(raw, labels)).toBe(raw);
  });
});
