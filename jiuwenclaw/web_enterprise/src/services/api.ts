/**
 * HTTP API 客户端：会话历史（Web Pod 本地 SQLite）。
 *
 * 与 webClient（WS 通道）并列；仅用于读取 Web Pod 落库的会话历史：
 * GET /api/sessions（列表）、GET /api/sessions/{id}（详情）。
 * user_id 来自 useExtSettingsStore.userId（登录态）。
 */

const API_BASE = '/api';

export interface HistorySession {
  session_id: string;
  user: string | null;
  title: string | null;
  message_count: number;
  last_preview: string | null;
  created_at: number;
  updated_at: number;
}

export interface HistoryMessage {
  role: 'user' | 'assistant';
  content: string;
  event_type: string | null;
  timestamp: number;
  request_id: string;
}

export interface HistoryDetail extends HistorySession {
  messages: HistoryMessage[];
}

export async function fetchSessions(limit = 50, offset = 0, userId?: string): Promise<HistorySession[]> {
  const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
  if (userId) params.set('user_id', userId);
  const res = await fetch(`${API_BASE}/sessions?${params.toString()}`);
  if (!res.ok) throw new Error(`fetchSessions failed: HTTP ${res.status}`);
  const data = (await res.json()) as { sessions?: HistorySession[] };
  return data.sessions ?? [];
}

export async function fetchSessionDetail(sessionId: string, userId?: string): Promise<HistoryDetail | null> {
  const target =
    `${API_BASE}/sessions/${encodeURIComponent(sessionId)}` +
    (userId ? `?user_id=${encodeURIComponent(userId)}` : '');
  const res = await fetch(target);
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(`fetchSessionDetail failed: HTTP ${res.status}`);
  return (await res.json()) as HistoryDetail;
}
