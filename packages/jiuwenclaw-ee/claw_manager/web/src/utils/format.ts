import i18n from '../i18n';

export function formatTime(iso?: string | null): string {
  if (!iso) return '-';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const pad = (n: number) => n.toString().padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

export function relativeTime(iso?: string | null): string {
  if (!iso) return '-';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const diff = (Date.now() - d.getTime()) / 1000;
  if (diff < 60) {
    return i18n.t('time.secondsAgo', { count: Math.max(0, Math.floor(diff)) });
  }
  if (diff < 3600) {
    return i18n.t('time.minutesAgo', { count: Math.floor(diff / 60) });
  }
  if (diff < 86400) {
    return i18n.t('time.hoursAgo', { count: Math.floor(diff / 3600) });
  }
  return i18n.t('time.daysAgo', { count: Math.floor(diff / 86400) });
}

export function truncate(s: string, max = 80): string {
  if (!s) return '';
  return s.length > max ? `${s.slice(0, max)}…` : s;
}

export function toCommaList(value: string[] | undefined | null): string {
  if (!value || value.length === 0) return '';
  return value.join(',');
}

export function fromCommaList(value: string): string[] | undefined {
  const v = value.trim();
  if (!v) return undefined;
  return v
    .split(',')
    .map((x) => x.trim())
    .filter(Boolean);
}

export function safeStringify(value: unknown, indent = 2): string {
  if (value === undefined || value === null) return '';
  try {
    return JSON.stringify(value, null, indent);
  } catch {
    return String(value);
  }
}
