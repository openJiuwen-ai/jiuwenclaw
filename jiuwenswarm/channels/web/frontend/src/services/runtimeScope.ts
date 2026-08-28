export interface RuntimeScope {
  userId?: string;
  groupId?: string;
  botId?: string;
  gatewayId?: string;
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
    // 租户身份只允许由登录后的 Manager Web 选择上下文注入。
    userId: pickQueryValue(query, 'user_id'),
    groupId: pickQueryValue(query, 'group_id'),
    botId: pickQueryValue(query, 'bot_id'),
    gatewayId: pickQueryValue(query, 'gateway_id'),
  };
}

// 企业版嵌入入口会通过 query 注入租户路由身份；SPA 随后可能跳转到
// /chat/new 并移除 query。仅保存在当前页面内存，不写 local/sessionStorage。
let runtimeScope: RuntimeScope =
  typeof window === 'undefined' ? {} : parseRuntimeScope(window.location.search);

export function setRuntimeScope(scope: RuntimeScope): void {
  runtimeScope = {
    userId: pickString(scope.userId),
    groupId: pickString(scope.groupId),
    botId: pickString(scope.botId),
    gatewayId: pickString(scope.gatewayId),
  };
}

export function getRuntimeScope(): RuntimeScope {
  if (typeof window === 'undefined') {
    return runtimeScope;
  }
  const current = parseRuntimeScope(window.location.search);
  runtimeScope = {
    userId: current.userId ?? runtimeScope.userId,
    groupId: current.groupId ?? runtimeScope.groupId,
    botId: current.botId ?? runtimeScope.botId,
    gatewayId: current.gatewayId ?? runtimeScope.gatewayId,
  };
  return runtimeScope;
}

/** Add the current runtime scope to a WebSocket handshake query. */
export function appendRuntimeScopeQuery(
  query: URLSearchParams,
  scope: RuntimeScope = getRuntimeScope()
): URLSearchParams {
  if (scope.userId) query.set('user_id', scope.userId);
  if (scope.groupId) query.set('group_id', scope.groupId);
  if (scope.botId) query.set('bot_id', scope.botId);
  if (scope.gatewayId) query.set('gateway_id', scope.gatewayId);
  return query;
}

/** Build HTTP routing headers without adding the scope to a business payload. */
export function buildRuntimeIdentityHeaders(
  requestId: string,
  params: Record<string, unknown>,
  scope: RuntimeScope = getRuntimeScope()
): Record<string, string> {
  const headers: Record<string, string> = { 'X-Request-Id': requestId };
  const accessToken = typeof localStorage === 'undefined' ? null : localStorage.getItem('openjiuwen_access_token');
  if (accessToken) headers.Authorization = `Bearer ${accessToken}`;
  const userId = scope.userId ?? pickString(params.user_id);
  const groupId = scope.groupId ?? pickString(params.group_id);
  const botId = scope.botId ?? pickString(params.bot_id);
  const sessionId = pickString(params.session_id);
  if (userId) headers['X-User-Id'] = userId;
  if (groupId) headers['X-Group-Id'] = groupId;
  if (botId) headers['X-Bot-Id'] = botId;
  if (scope.gatewayId) headers['X-Jiuwenclaw-Id'] = scope.gatewayId;
  if (sessionId) headers['X-Session-Id'] = sessionId;
  return headers;
}
