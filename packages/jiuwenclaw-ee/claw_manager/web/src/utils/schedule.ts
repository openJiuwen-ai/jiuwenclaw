/** 与后端一致：合法 cron（5/6/7 段）。 */
const CRON_FIELD_RE = /^[\w*/,\-?#]+$/i;
const CRON_FIELD_COUNTS = new Set([5, 6, 7]);

export function isValidHookSchedule(value: string): boolean {
  const text = value.trim();
  if (!text) return false;
  const parts = text.split(/\s+/);
  if (!CRON_FIELD_COUNTS.has(parts.length)) return false;
  return parts.every((part) => CRON_FIELD_RE.test(part));
}
