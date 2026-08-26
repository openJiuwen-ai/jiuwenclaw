export interface RuntimeScope {
  userId?: string;
  groupId?: string;
  botId?: string;
}

function pickString(value: unknown): string | undefined {
  if (typeof value !== 'string') {
    return undefined;
  }
  const normalized = value.trim();
  return normalized || undefined;
}

function pickQueryValue(query: URLSearchParams, key: string): string | undefined {
  const value = query.get(key)?.trim();
  return value || undefined;
}

/**
 * Read the runtime routing scope supplied by the embedding shell.
 *
 * The values are transport context rather than business form fields. They are
 * intentionally kept in memory and are not written to localStorage or
 * sessionStorage. Authentication and authorization remain the responsibility
 * of the outer shell and server-side access control.
 */
export function parseRuntimeScope(search: string): RuntimeScope {
  const query = new URLSearchParams(search);
  return {
    userId: pickQueryValue(query, 'user_id'),
    groupId: pickQueryValue(query, 'group_id'),
    botId: pickQueryValue(query, 'bot_id'),
  };
}

export function getRuntimeScope(): RuntimeScope {
  if (typeof window === 'undefined') {
    return {};
  }
  return parseRuntimeScope(window.location.search);
}

/** Add the current runtime scope to a WebSocket handshake query. */
export function appendRuntimeScopeQuery(
  query: URLSearchParams,
  scope: RuntimeScope = getRuntimeScope()
): URLSearchParams {
  if (scope.userId) query.set('user_id', scope.userId);
  if (scope.groupId) query.set('group_id', scope.groupId);
  if (scope.botId) query.set('bot_id', scope.botId);
  return query;
}

/** Build HTTP routing headers without adding the scope to a business payload. */
export function buildRuntimeIdentityHeaders(
  requestId: string,
  params: Record<string, unknown>,
  scope: RuntimeScope = getRuntimeScope()
): Record<string, string> {
  const headers: Record<string, string> = { 'X-Request-Id': requestId };
  const userId = scope.userId ?? pickString(params.user_id);
  const groupId = scope.groupId ?? pickString(params.group_id);
  const botId = scope.botId ?? pickString(params.bot_id);
  const sessionId = pickString(params.session_id);
  if (userId) headers['X-User-Id'] = userId;
  if (groupId) headers['X-Group-Id'] = groupId;
  if (botId) headers['X-Bot-Id'] = botId;
  if (sessionId) headers['X-Session-Id'] = sessionId;
  return headers;
}
