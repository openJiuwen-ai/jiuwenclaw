import type {
  EnterpriseAgent,
  EnterpriseGateway,
  EnterpriseOrg,
  EnterpriseUser,
} from '../../services/enterpriseContext';
import { EnterpriseAuthError, type EnterpriseAuthProvider } from '../types';

const ACCESS_KEY = 'openjiuwen_access_token';
const REFRESH_KEY = 'openjiuwen_refresh_token';

interface ManagerResponse<T> {
  code: number;
  message?: string;
  data: T;
}

function accessToken(): string | null {
  return typeof localStorage === 'undefined' ? null : localStorage.getItem(ACCESS_KEY);
}

function authHeaders(): HeadersInit {
  const token = accessToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

function requestMessage(body: unknown, fallback: string): string {
  if (body && typeof body === 'object') {
    const value = body as Record<string, unknown>;
    if (typeof value.detail === 'string' && value.detail.trim()) return value.detail;
    if (typeof value.message === 'string' && value.message.trim()) return value.message;
  }
  return fallback;
}

async function requestJson<T>(path: string): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, { headers: authHeaders() });
  } catch (error) {
    throw new EnterpriseAuthError(0, `网络请求失败：${error instanceof Error ? error.message : String(error)}`);
  }
  let body: unknown = null;
  try {
    body = await response.json();
  } catch {
    // The HTTP status remains authoritative for non-JSON responses.
  }
  if (!response.ok) throw new EnterpriseAuthError(response.status, requestMessage(body, `HTTP ${response.status}`));
  return body as T;
}

function clearLogin(): void {
  if (typeof localStorage !== 'undefined') {
    localStorage.removeItem(ACCESS_KEY);
    localStorage.removeItem(REFRESH_KEY);
  }
  if (typeof document !== 'undefined') {
    document.cookie = `${ACCESS_KEY}=; Path=/; Max-Age=0; SameSite=Strict`;
  }
}

export const managerAuthProvider: EnterpriseAuthProvider = {
  id: 'manager',
  startupMessage: '【正式身份认证模式，依赖manager ID认证服务】',
  isAuthenticated: () => Boolean(accessToken()),
  redirectToLogin() {
    clearLogin();
    window.location.replace('/auth');
  },
  getCurrentUser: () => requestJson<EnterpriseUser>('/idp/v1/auth/me'),
  async listOrganizations() {
    const result = await requestJson<{ orgs: EnterpriseOrg[] }>('/idp/v1/auth/me/orgs');
    return result.orgs ?? [];
  },
  async listGateways() {
    const result = await requestJson<ManagerResponse<{ gateways: EnterpriseGateway[] }>>('/manager-api/v1/user-console/gateways');
    if (result.code !== 200) throw new EnterpriseAuthError(result.code, result.message || '加载组网失败');
    return result.data?.gateways ?? [];
  },
  async listAgents(groupId, gatewayId) {
    const query = new URLSearchParams({ group_id: groupId, jiuwenclaw_id: gatewayId });
    const result = await requestJson<ManagerResponse<{ agents: EnterpriseAgent[] }>>(`/manager-api/v1/user-console/agents?${query.toString()}`);
    if (result.code !== 200) throw new EnterpriseAuthError(result.code, result.message || '加载 Agent 失败');
    return result.data?.agents ?? [];
  },
  async logout() {
    const refreshToken = typeof localStorage === 'undefined' ? null : localStorage.getItem(REFRESH_KEY);
    if (refreshToken) {
      try {
        await fetch('/idp/v1/auth/logout', {
          method: 'POST',
          headers: { ...authHeaders(), 'Content-Type': 'application/json' },
          body: JSON.stringify({ refresh_token: refreshToken }),
        });
      } catch {
        // Local logout must still complete when the identity service is unavailable.
      }
    }
    this.redirectToLogin();
  },
};
