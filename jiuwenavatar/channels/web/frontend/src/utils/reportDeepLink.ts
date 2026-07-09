export interface ReportDeepLink {
  avatarId?: string;
  filterRead?: '' | 'unread' | 'read';
  /** 每次浮标跳转递增，确保重复点击同一分身也能重新应用筛选 */
  navToken?: number;
}

export function parseReportDeepLink(hash: string): ReportDeepLink | null {
  const raw = hash.startsWith('#') ? hash.slice(1) : hash;
  if (!raw.startsWith('reports')) {
    return null;
  }
  const query = raw.includes('?') ? raw.split('?')[1] : '';
  const params = new URLSearchParams(query);
  const avatarId = params.get('avatar')?.trim() || undefined;
  const read = params.get('read')?.trim() || '';
  const navRaw = params.get('_nav')?.trim();
  const navToken = navRaw ? Number(navRaw) || Date.now() : undefined;
  const filterRead =
    read === 'unread' || read === 'read' ? (read as 'unread' | 'read') : undefined;
  if (!avatarId && !filterRead) {
    return { avatarId: undefined, filterRead: undefined, navToken };
  }
  return { avatarId, filterRead, navToken };
}

export function buildReportDeepLink(link: ReportDeepLink, navToken?: number): string {
  const params = new URLSearchParams();
  if (link.avatarId) {
    params.set('avatar', link.avatarId);
  }
  if (link.filterRead) {
    params.set('read', link.filterRead);
  }
  const token = navToken ?? link.navToken;
  if (token) {
    params.set('_nav', String(token));
  }
  const qs = params.toString();
  return qs ? `#reports?${qs}` : '#reports';
}
