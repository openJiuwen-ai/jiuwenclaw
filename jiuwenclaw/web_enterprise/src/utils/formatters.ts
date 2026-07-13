/**
 * 格式化工具函数
 */
import i18n from '../i18n';

export const DEFAULT_TOOL_RESULT_DISPLAY_MAX_CHARS = 500;
export const MAX_TOOL_RESULT_DISPLAY_MAX_CHARS = 100_000;

let toolResultDisplayMaxChars = DEFAULT_TOOL_RESULT_DISPLAY_MAX_CHARS;

/**
 * 解析工具结果展示最大字符数（0 表示不截断）。
 */
export function parseToolResultDisplayMaxChars(value: unknown): number {
  if (value === null || value === undefined || value === '') {
    return DEFAULT_TOOL_RESULT_DISPLAY_MAX_CHARS;
  }
  const parsed = Number.parseInt(String(value).trim(), 10);
  if (!Number.isFinite(parsed) || parsed < 0) {
    return DEFAULT_TOOL_RESULT_DISPLAY_MAX_CHARS;
  }
  if (parsed === 0) {
    return 0;
  }
  return Math.min(parsed, MAX_TOOL_RESULT_DISPLAY_MAX_CHARS);
}

/** 同步服务端配置到前端展示逻辑。 */
export function setToolResultDisplayMaxChars(value: unknown): void {
  toolResultDisplayMaxChars = parseToolResultDisplayMaxChars(value);
}

export function getToolResultDisplayMaxChars(): number {
  return toolResultDisplayMaxChars;
}

/**
 * 格式化时间戳
 */
export function formatTimestamp(timestamp: string): string {
  const date = new Date(timestamp);
  return date.toLocaleTimeString(i18n.language, {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
}

/**
 * 格式化日期
 */
export function formatDate(timestamp: string): string {
  const date = new Date(timestamp);
  return date.toLocaleDateString(i18n.language, {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  });
}

/**
 * 格式化相对时间
 */
export function formatRelativeTime(timestamp: string): string {
  const date = new Date(timestamp);
  const now = new Date();
  const diff = now.getTime() - date.getTime();

  const seconds = Math.floor(diff / 1000);
  const minutes = Math.floor(seconds / 60);
  const hours = Math.floor(minutes / 60);
  const days = Math.floor(hours / 24);

  if (days > 0) return i18n.t('time.daysAgo', { count: days });
  if (hours > 0) return i18n.t('time.hoursAgo', { count: hours });
  if (minutes > 0) return i18n.t('time.minutesAgo', { count: minutes });
  return i18n.t('time.justNow');
}

/**
 * 截断文本
 */
export function truncateText(text: string, maxLength: number): string {
  if (text.length <= maxLength) return text;
  return text.slice(0, maxLength) + '...';
}

/**
 * 格式化工具参数
 */
export function formatToolArguments(args: Record<string, unknown>): string {
  try {
    return JSON.stringify(args, null, 2);
  } catch {
    return String(args);
  }
}

/**
 * 格式化工具结果（截断长结果；maxLength 省略时使用服务端配置）
 */
export function formatToolResult(result: string, maxLength?: number): string {
  const limit = maxLength ?? toolResultDisplayMaxChars;
  if (limit === 0 || result.length <= limit) return result;
  return result.slice(0, limit) + `\n... (${i18n.t('chatUi.toolResult.truncated')})`;
}
