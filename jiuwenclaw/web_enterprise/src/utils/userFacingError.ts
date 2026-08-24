/**
 * 将后端/网络原始错误文案转为用户可读提示（含数据库查询超时）。
 */

const DB_QUERY_TIMEOUT_PATTERNS: RegExp[] = [
  /max_execution_time/i,
  /statement timeout/i,
  /query execution was interrupted/i,
  /querycanceled/i,
  /canceling statement/i,
  /command timeout/i,
  /read timeout/i,
  /write timeout/i,
  /lock wait timeout/i,
  /database query timeout/i,
  /^timeouterror$/i,
];

export function isDbQueryTimeoutMessage(message: string): boolean {
  const text = message.trim();
  if (!text) {
    return false;
  }
  return DB_QUERY_TIMEOUT_PATTERNS.some((pattern) => pattern.test(text));
}

/** 识别并归一化常见超时/失败文案；未识别时返回 trimmed 原文。 */
export function normalizeUserFacingError(
  message: string,
  labels: {
    dbQueryTimeout: string;
    requestTimeout: string;
  }
): string {
  const trimmed = message.trim();
  if (!trimmed) {
    return labels.requestTimeout;
  }
  if (isDbQueryTimeoutMessage(trimmed)) {
    return labels.dbQueryTimeout;
  }
  if (/^request timeout$/i.test(trimmed) || trimmed.includes('REQUEST_TIMEOUT')) {
    return labels.requestTimeout;
  }
  return trimmed;
}
