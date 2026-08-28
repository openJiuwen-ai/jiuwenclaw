import { type ReactNode, useCallback, useEffect, useMemo, useState } from 'react';
import { isEnterpriseMode } from './edition';
import {
  EnterpriseContext,
  type EnterpriseAgent,
  type EnterpriseContextSnapshot,
  type EnterpriseContextValue,
  type EnterpriseGateway,
  type EnterpriseOrg,
  type EnterpriseUser,
} from './services/enterpriseContext';
import { parseRuntimeScope, setRuntimeScope } from './services/runtimeScope';

const ACCESS_KEY = 'openjiuwen_access_token';
const REFRESH_KEY = 'openjiuwen_refresh_token';

type EntryPhase = 'loading' | 'ready' | 'empty' | 'error' | 'redirecting';

class EnterpriseApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = 'EnterpriseApiError';
  }
}

interface ManagerResponse<T> {
  code: number;
  message?: string;
  data: T;
}

interface ContextCandidate {
  gateway: EnterpriseGateway;
  org: EnterpriseOrg;
}

interface ResolvedContext extends ContextCandidate {
  agents: EnterpriseAgent[];
  selectedBot: string;
}

function agentRuntimeId(agent: EnterpriseAgent): string {
  return agent.resource_id || agent.template_id;
}

function movePreferredFirst<T>(items: T[], matches: (item: T) => boolean): T[] {
  const preferredIndex = items.findIndex(matches);
  if (preferredIndex <= 0) return items;
  return [items[preferredIndex], ...items.slice(0, preferredIndex), ...items.slice(preferredIndex + 1)];
}

/**
 * Build a deterministic list of authorized gateway/organization combinations.
 * URL values only affect ordering; every selected combination is still checked
 * against the server before it becomes active.
 */
export function orderedContextCandidates(
  gateways: EnterpriseGateway[],
  orgs: EnterpriseOrg[],
  preferredGatewayId?: string,
  preferredOrgId?: string,
): ContextCandidate[] {
  const orderedGateways = movePreferredFirst([...gateways], item => item.jiuwenclaw_id === preferredGatewayId);
  const orderedOrgs = movePreferredFirst([...orgs], item => item.group_id === preferredOrgId);
  return orderedGateways.flatMap(gateway => orderedOrgs.map(org => ({ gateway, org })));
}

export function chooseAgent(agents: EnterpriseAgent[], preferredBotId?: string): EnterpriseAgent | null {
  return agents.find(agent => agentRuntimeId(agent) === preferredBotId) ?? agents[0] ?? null;
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
    throw new EnterpriseApiError(0, `网络请求失败：${error instanceof Error ? error.message : String(error)}`);
  }

  let body: unknown = null;
  try {
    body = await response.json();
  } catch {
    // The status code below remains the source of truth for a non-JSON error.
  }
  if (!response.ok) {
    throw new EnterpriseApiError(response.status, requestMessage(body, `HTTP ${response.status}`));
  }
  return body as T;
}

async function loadUser(): Promise<EnterpriseUser> {
  return requestJson<EnterpriseUser>('/idp/v1/auth/me');
}

async function loadOrgs(): Promise<EnterpriseOrg[]> {
  const result = await requestJson<{ orgs: EnterpriseOrg[] }>('/idp/v1/auth/me/orgs');
  return result.orgs ?? [];
}

async function loadGateways(): Promise<EnterpriseGateway[]> {
  const result = await requestJson<ManagerResponse<{ gateways: EnterpriseGateway[] }>>('/manager-api/v1/user-console/gateways');
  if (result.code !== 200) throw new EnterpriseApiError(result.code, result.message || '加载组网失败');
  return result.data?.gateways ?? [];
}

async function loadAgents(groupId: string, gatewayId: string): Promise<EnterpriseAgent[]> {
  const query = new URLSearchParams({ group_id: groupId, jiuwenclaw_id: gatewayId });
  const result = await requestJson<ManagerResponse<{ agents: EnterpriseAgent[] }>>(`/manager-api/v1/user-console/agents?${query.toString()}`);
  if (result.code !== 200) throw new EnterpriseApiError(result.code, result.message || '加载 Agent 失败');
  return result.data?.agents ?? [];
}

async function resolveFirstContext(candidates: ContextCandidate[], preferredBotId?: string): Promise<ResolvedContext | null> {
  let firstError: unknown = null;
  for (const candidate of candidates) {
    try {
      const agents = await loadAgents(candidate.org.group_id, candidate.gateway.jiuwenclaw_id);
      const selected = chooseAgent(agents, preferredBotId);
      if (selected) return { ...candidate, agents, selectedBot: agentRuntimeId(selected) };
    } catch (error) {
      if (error instanceof EnterpriseApiError && error.status === 401) throw error;
      firstError ??= error;
    }
  }
  if (firstError) throw firstError;
  return null;
}

function entryPath(): string {
  return window.location.pathname.startsWith('/chat') ? '/chat/' : '/';
}

function contextUrl(userId: string, resolved: ResolvedContext): string {
  const query = new URLSearchParams({
    user_id: userId,
    group_id: resolved.org.group_id,
    bot_id: resolved.selectedBot,
    gateway_id: resolved.gateway.jiuwenclaw_id,
  });
  return `${entryPath()}?${query.toString()}`;
}

function activateContext(userId: string, resolved: ResolvedContext, navigate: boolean): void {
  setRuntimeScope({
    userId,
    groupId: resolved.org.group_id,
    botId: resolved.selectedBot,
    gatewayId: resolved.gateway.jiuwenclaw_id,
  });
  const nextUrl = contextUrl(userId, resolved);
  if (navigate) window.location.replace(nextUrl);
  else window.history.replaceState({}, '', nextUrl);
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

function redirectToLogin(): void {
  clearLogin();
  window.location.replace('/auth');
}

function errorText(error: unknown): string {
  return error instanceof Error && error.message ? error.message : '加载企业用户上下文失败';
}

function EntryStatus({ phase, error, onLogout }: { phase: EntryPhase; error: string; onLogout: () => void }) {
  const empty = phase === 'empty';
  const failed = phase === 'error';
  return (
    <div className="enterprise-entry">
      <div className="enterprise-entry__glow" />
      <div className="enterprise-entry__card">
        <div className="enterprise-entry__brand">
          JIUWEN<span>CLAW</span>
        </div>
        <div className="enterprise-entry__eyebrow">ENTERPRISE WORKSPACE</div>
        <h1>{empty ? '暂无可用 Agent' : failed ? '加载失败' : phase === 'redirecting' ? '正在前往登录页' : '正在加载工作空间'}</h1>
        <p>{empty ? '当前账号没有可用的组织、组网和 Agent 组合，请联系管理员完成授权。' : failed ? error : '正在校验账号权限并选择一个可用 Agent。'}</p>
        {(empty || failed) && (
          <button type="button" className="enterprise-entry__button" onClick={onLogout}>
            返回登录页
          </button>
        )}
      </div>
    </div>
  );
}

export function EnterpriseEntry({ children }: { children: ReactNode }) {
  const enterprise = isEnterpriseMode();
  const [phase, setPhase] = useState<EntryPhase>(() => (enterprise && !accessToken() ? 'redirecting' : 'loading'));
  const [context, setContext] = useState<EnterpriseContextSnapshot | null>(null);
  const [error, setError] = useState('');
  const [contextError, setContextError] = useState('');
  const [contextSwitching, setContextSwitching] = useState(false);

  const logout = useCallback(() => {
    void (async () => {
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
      redirectToLogin();
    })();
  }, []);

  useEffect(() => {
    if (!enterprise) return;
    if (!accessToken()) {
      redirectToLogin();
      return;
    }

    let cancelled = false;
    const bootstrap = async () => {
      try {
        const preferred = parseRuntimeScope(window.location.search);
        const [user, orgs, gateways] = await Promise.all([loadUser(), loadOrgs(), loadGateways()]);
        const resolved = await resolveFirstContext(orderedContextCandidates(gateways, orgs, preferred.gatewayId, preferred.groupId), preferred.botId);
        if (cancelled) return;
        if (!resolved) {
          setPhase('empty');
          return;
        }
        activateContext(user.user_id, resolved, false);
        setContext({
          user,
          org: resolved.org,
          orgs,
          gateway: resolved.gateway,
          gateways,
          agents: resolved.agents,
          selectedBot: resolved.selectedBot,
        });
        setPhase('ready');
      } catch (bootstrapError) {
        if (cancelled) return;
        if (bootstrapError instanceof EnterpriseApiError && bootstrapError.status === 401) {
          redirectToLogin();
          return;
        }
        setError(errorText(bootstrapError));
        setPhase('error');
      }
    };
    void bootstrap();
    return () => {
      cancelled = true;
    };
  }, [enterprise]);

  const switchContext = useCallback(
    async (candidates: ContextCandidate[], preferredBotId?: string, missingMessage = '所选范围内暂无可用 Agent') => {
      if (!context || contextSwitching) return;
      setContextSwitching(true);
      setContextError('');
      try {
        const resolved = await resolveFirstContext(candidates, preferredBotId);
        if (!resolved) {
          setContextError(missingMessage);
          return;
        }
        activateContext(context.user.user_id, resolved, true);
      } catch (switchError) {
        if (switchError instanceof EnterpriseApiError && switchError.status === 401) {
          redirectToLogin();
          return;
        }
        setContextError(errorText(switchError));
      } finally {
        setContextSwitching(false);
      }
    },
    [context, contextSwitching],
  );

  const contextValue = useMemo<EnterpriseContextValue | null>(() => {
    if (!context) return null;
    return {
      ...context,
      contextError,
      contextSwitching,
      onOrgChange: orgId => {
        const org = context.orgs.find(item => item.group_id === orgId);
        if (!org) return;
        const gateways = movePreferredFirst([...context.gateways], item => item.jiuwenclaw_id === context.gateway.jiuwenclaw_id);
        void switchContext(
          gateways.map(gateway => ({ gateway, org })),
          undefined,
          '该组织暂无可用 Agent',
        );
      },
      onGatewayChange: gatewayId => {
        const gateway = context.gateways.find(item => item.jiuwenclaw_id === gatewayId);
        if (!gateway) return;
        const orgs = movePreferredFirst([...context.orgs], item => item.group_id === context.org.group_id);
        void switchContext(
          orgs.map(org => ({ gateway, org })),
          undefined,
          '该组网暂无可用 Agent',
        );
      },
      onBotChange: botId => {
        const selected = context.agents.find(agent => agentRuntimeId(agent) === botId);
        if (!selected || botId === context.selectedBot) return;
        activateContext(
          context.user.user_id,
          {
            gateway: context.gateway,
            org: context.org,
            agents: context.agents,
            selectedBot: botId,
          },
          true,
        );
      },
      onLogout: logout,
    };
  }, [context, contextError, contextSwitching, logout, switchContext]);

  if (!enterprise) return <>{children}</>;
  if (phase !== 'ready' || !contextValue) return <EntryStatus phase={phase} error={error} onLogout={logout} />;
  return <EnterpriseContext.Provider value={contextValue}>{children}</EnterpriseContext.Provider>;
}
