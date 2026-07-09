/**
 * 每个分身独立维护「最近使用的会话 ID」，切换分身时可恢复上下文。
 */

import type { Session } from '../types';

const AVATAR_SESSION_MAP_KEY = 'jiuwenavatar.avatarLastSessions';

/** localStorage 键：空字符串分身用 __default__ */
export function avatarStorageKey(avatarId: string | null | undefined): string {
  const trimmed = (avatarId || '').trim();
  return trimmed || '__default__';
}

type AvatarSessionMap = Record<string, string>;

function readMap(): AvatarSessionMap {
  try {
    const raw = localStorage.getItem(AVATAR_SESSION_MAP_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw) as unknown;
    if (!parsed || typeof parsed !== 'object') return {};
    const out: AvatarSessionMap = {};
    for (const [key, value] of Object.entries(parsed as Record<string, unknown>)) {
      if (typeof key === 'string' && typeof value === 'string' && value.trim()) {
        out[key] = value.trim();
      }
    }
    return out;
  } catch {
    return {};
  }
}

function writeMap(map: AvatarSessionMap): void {
  try {
    localStorage.setItem(AVATAR_SESSION_MAP_KEY, JSON.stringify(map));
  } catch {
    // ignore quota / private mode
  }
}

export function rememberAvatarSession(
  avatarId: string | null | undefined,
  sessionId: string | null | undefined,
): void {
  const sid = (sessionId || '').trim();
  if (!sid || sid === 'new') return;
  const key = avatarStorageKey(avatarId);
  const map = readMap();
  if (map[key] === sid) return;
  map[key] = sid;
  writeMap(map);
}

export function getRememberedSessionForAvatar(
  avatarId: string | null | undefined,
): string | null {
  const key = avatarStorageKey(avatarId);
  const map = readMap();
  const sid = map[key];
  return sid && sid.trim() ? sid.trim() : null;
}

function sessionAvatarId(session: Session): string {
  const raw = (session as Session & { avatar_id?: string }).avatar_id;
  return typeof raw === 'string' ? raw.trim() : '';
}

function sessionSortKey(session: Session): number {
  const last = session.last_message_at;
  if (typeof last === 'number' && Number.isFinite(last)) return last;
  const updated = Date.parse(session.updated_at || '');
  if (Number.isFinite(updated)) return updated / 1000;
  const created = Date.parse(session.created_at || '');
  if (Number.isFinite(created)) return created / 1000;
  return 0;
}

/** 从会话列表中找该分身最近活跃的 sess_* 会话 */
export function findLatestSessionForAvatar(
  sessions: Session[],
  avatarId: string | null | undefined,
): string | null {
  const rows = filterChatSessionsForAvatar(sessions, avatarId);
  return rows[0]?.session_id ?? null;
}

/** 当前分身下的 web 聊天会话（sess_*），按最近活跃倒序 */
export function filterChatSessionsForAvatar(
  sessions: Session[],
  avatarId: string | null | undefined,
): Session[] {
  const expected = (avatarId || '').trim();
  return sessions
    .filter((s) => {
      if (!s.session_id?.startsWith('sess_')) return false;
      return sessionAvatarId(s) === expected;
    })
    .sort((a, b) => sessionSortKey(b) - sessionSortKey(a));
}

/**
 * 解析切换分身时应恢复的会话：优先 localStorage 映射，其次列表里该分身最近会话。
 */
export function resolveSessionForAvatar(
  avatarId: string | null | undefined,
  sessions: Session[],
): string | null {
  const remembered = getRememberedSessionForAvatar(avatarId);
  if (remembered) {
    const meta = sessions.find((s) => s.session_id === remembered);
    if (meta && sessionAvatarId(meta) === (avatarId || '').trim()) {
      return remembered;
    }
    if (remembered.startsWith('sess_')) {
      return remembered;
    }
  }
  return findLatestSessionForAvatar(sessions, avatarId);
}
