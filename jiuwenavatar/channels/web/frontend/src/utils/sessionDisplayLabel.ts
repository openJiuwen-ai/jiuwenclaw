/** 会话列表展示标题（与 SessionsPanel 逻辑一致） */

function formatDateTime(date: Date): string {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  const hours = String(date.getHours()).padStart(2, '0');
  const minutes = String(date.getMinutes()).padStart(2, '0');
  const seconds = String(date.getSeconds()).padStart(2, '0');
  return `${year}-${month}-${day} ${hours}:${minutes}:${seconds}`;
}

function isPlausibleDate(date: Date): boolean {
  const year = date.getFullYear();
  return year >= 2020 && year <= 2100;
}

function shortenDiscordIDForLabel(id: string): string {
  const s = id.trim();
  if (s.length <= 12) {
    return s;
  }
  return `${s.slice(0, 4)}…${s.slice(-4)}`;
}

export function parseSessionDisplayLabel(
  sessionId: string,
  t: (key: string, options?: Record<string, unknown>) => string,
): string {
  if (!sessionId) return t('sessions.unknownSession');

  if (sessionId.endsWith('.wechat')) {
    const wechatLabel = t('sessions.prefixes.wechat');
    const at = sessionId.lastIndexOf('@');
    const local =
      at >= 0 ? sessionId.slice(0, at) : sessionId.replace(/\.wechat$/i, '').trim();
    const idPart = local.trim() || sessionId;
    return `${wechatLabel}-${shortenDiscordIDForLabel(idPart)}`;
  }

  const prefixes = ['sess_', 'cron_', 'feishu_', 'wechat_', 'xiaoyi_', 'dingtalk_', 'wecom_'];
  const prefixMap: Record<string, string> = {
    sess_: t('sessions.prefixes.session'),
    cron_: t('sessions.prefixes.cron'),
    feishu_: t('sessions.prefixes.feishu'),
    wechat_: t('sessions.prefixes.wechat'),
    xiaoyi_: t('sessions.prefixes.xiaoyi'),
    dingtalk_: t('sessions.prefixes.dingtalk'),
    wecom_: t('sessions.prefixes.wecom'),
  };

  for (const prefix of prefixes) {
    if (sessionId.startsWith(prefix)) {
      const parts = sessionId.split('_');
      const hexTs = parts[1] ?? '';
      if (/^[0-9a-fA-F]+$/.test(hexTs)) {
        const ms = Number.parseInt(hexTs, 16);
        if (Number.isFinite(ms)) {
          const date = new Date(ms);
          if (!Number.isNaN(date.getTime()) && isPlausibleDate(date)) {
            return `${prefixMap[prefix]}-${formatDateTime(date)}`;
          }
        }
      }
      return `${prefixMap[prefix]}-${t('sessions.unknownTime')}`;
    }
  }

  if (sessionId.startsWith('heartbeat_')) {
    const parts = sessionId.split('_');
    const hexTs = parts[1] ?? '';
    if (/^[0-9a-fA-F]+$/.test(hexTs)) {
      const ms = Number.parseInt(hexTs, 16);
      if (Number.isFinite(ms)) {
        const date = new Date(ms);
        if (!Number.isNaN(date.getTime()) && isPlausibleDate(date)) {
          return `${t('sessions.prefixes.heartbeat')}-${formatDateTime(date)}`;
        }
      }
    }
  }

  const prefix = sessionId.includes('_') ? sessionId.split('_')[0] : t('sessions.prefixes.unknown');
  return `${prefix}-${t('sessions.unknownTime')}`;
}

export function formatSessionRelativeTime(
  session: { last_message_at?: number; updated_at?: string; created_at?: string },
  language: string,
  t: (key: string, options?: Record<string, unknown>) => string,
): string {
  let date: Date | null = null;
  if (typeof session.last_message_at === 'number' && Number.isFinite(session.last_message_at)) {
    date = new Date(session.last_message_at * 1000);
  } else if (session.updated_at) {
    date = new Date(session.updated_at);
  } else if (session.created_at) {
    date = new Date(session.created_at);
  }
  if (!date || Number.isNaN(date.getTime())) {
    return '';
  }

  const diff = Date.now() - date.getTime();
  if (diff < 60_000) return t('time.relative.justNow');
  if (diff < 3_600_000) {
    return t('time.relative.minutesAgo', { count: Math.floor(diff / 60_000) });
  }
  if (diff < 86_400_000) {
    return t('time.relative.hoursAgo', { count: Math.floor(diff / 3_600_000) });
  }
  if (diff < 604_800_000) {
    return t('time.relative.daysAgo', { count: Math.floor(diff / 86_400_000) });
  }
  return date.toLocaleDateString(language, { month: 'short', day: 'numeric' });
}
