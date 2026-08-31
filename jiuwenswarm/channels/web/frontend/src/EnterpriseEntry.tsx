import { type ReactNode, useCallback, useEffect, useMemo, useState } from 'react';
import { isLoginAuthSimulateEnabled } from './auth/config';
import { resolveEnterpriseAuthProvider } from './auth/providerRegistry';
import { EnterpriseAuthError, type EnterpriseAuthProvider } from './auth/types';
import { isEnterprise } from './edition';
import {
  EnterpriseContext,
  type EnterpriseAgent,
  type EnterpriseContextSnapshot,
  type EnterpriseContextValue,
  type EnterpriseGateway,
  type EnterpriseOrg,
} from './services/enterpriseContext';
import { parseRuntimeScope, setRuntimeScope } from './services/runtimeScope';

type EntryPhase = 'loading' | 'ready' | 'empty' | 'error' | 'redirecting';

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

async function resolveFirstContext(
  provider: EnterpriseAuthProvider,
  candidates: ContextCandidate[],
  preferredBotId?: string,
): Promise<ResolvedContext | null> {
  let firstError: unknown = null;
  for (const candidate of candidates) {
    try {
      const agents = await provider.listAgents(candidate.org.group_id, candidate.gateway.jiuwenclaw_id);
      const selected = chooseAgent(agents, preferredBotId);
      if (selected) return { ...candidate, agents, selectedBot: agentRuntimeId(selected) };
    } catch (error) {
      if (error instanceof EnterpriseAuthError && error.status === 401) throw error;
      firstError ??= error;
    }
  }
  if (firstError) throw firstError;
  return null;
}

function entryPath(): string {
  return window.location.pathname.startsWith('/chat') ? '/chat/' : '/';
}

function contextUrl(userId: string, resolved: ResolvedContext, debugContext = false): string {
  const query = new URLSearchParams({
    user_id: userId,
    group_id: resolved.org.group_id,
    bot_id: resolved.selectedBot,
    gateway_id: resolved.gateway.jiuwenclaw_id,
  });
  if (debugContext) query.set('debug_context', '1');
  return `${entryPath()}?${query.toString()}`;
}

function activateContext(userId: string, resolved: ResolvedContext, navigate: boolean, debugContext = false): void {
  setRuntimeScope({
    userId,
    groupId: resolved.org.group_id,
    botId: resolved.selectedBot,
    gatewayId: resolved.gateway.jiuwenclaw_id,
  });
  const nextUrl = contextUrl(userId, resolved, debugContext);
  if (navigate) window.location.replace(nextUrl);
  else window.history.replaceState({}, '', nextUrl);
}

export function isDebugContext(search: string): boolean {
  return new URLSearchParams(search).get('debug_context') === '1';
}

export function buildRequestedDebugContext(
  orgs: EnterpriseOrg[],
  gateways: EnterpriseGateway[],
  agents: EnterpriseAgent[],
  preferred: ReturnType<typeof parseRuntimeScope>,
): ResolvedContext | null {
  if (!preferred.groupId || !preferred.gatewayId || !preferred.botId) return null;
  const org = orgs.find(item => item.group_id === preferred.groupId) ?? {
    group_id: preferred.groupId,
    name: preferred.groupId,
  };
  const gateway = gateways.find(item => item.jiuwenclaw_id === preferred.gatewayId) ?? {
    jiuwenclaw_id: preferred.gatewayId,
    jiuwenclaw_name: preferred.gatewayId,
    gateway_endpoint: null,
  };
  return { org, gateway, agents, selectedBot: preferred.botId };
}

async function resolveRequestedDebugContext(
  provider: EnterpriseAuthProvider,
  orgs: EnterpriseOrg[],
  gateways: EnterpriseGateway[],
  preferred: ReturnType<typeof parseRuntimeScope>,
): Promise<ResolvedContext | null> {
  if (!preferred.groupId || !preferred.gatewayId || !preferred.botId) return null;
  let agents: EnterpriseAgent[] = [];
  try {
    agents = await provider.listAgents(preferred.groupId, preferred.gatewayId);
  } catch (error) {
    if (error instanceof EnterpriseAuthError && error.status === 401) throw error;
    // 自定义 ID 用于联调错误路由；列表查询失败不能覆盖手工输入的上下文。
  }
  return buildRequestedDebugContext(orgs, gateways, agents, preferred);
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
  const enterprise = isEnterprise();
  const simulateLogin = enterprise && isLoginAuthSimulateEnabled();
  const provider = useMemo(
    () => (enterprise ? resolveEnterpriseAuthProvider(simulateLogin) : null),
    [enterprise, simulateLogin],
  );
  const [phase, setPhase] = useState<EntryPhase>(() => (provider && !provider.isAuthenticated() ? 'redirecting' : 'loading'));
  const [context, setContext] = useState<EnterpriseContextSnapshot | null>(null);
  const [error, setError] = useState('');
  const [contextError, setContextError] = useState('');
  const [contextSwitching, setContextSwitching] = useState(false);

  const logout = useCallback(() => {
    if (provider) void provider.logout();
  }, [provider]);

  useEffect(() => {
    if (!enterprise || !provider) return;
    console.info(provider.startupMessage);
    if (!provider.isAuthenticated()) {
      provider.redirectToLogin();
      return;
    }

    let cancelled = false;
    const bootstrap = async () => {
      try {
        const preferred = parseRuntimeScope(window.location.search);
        const [user, orgs, gateways] = await Promise.all([
          provider.getCurrentUser(),
          provider.listOrganizations(),
          provider.listGateways(),
        ]);
        const resolved = isDebugContext(window.location.search)
          ? await resolveRequestedDebugContext(provider, orgs, gateways, preferred)
          : await resolveFirstContext(
              provider,
              orderedContextCandidates(gateways, orgs, preferred.gatewayId, preferred.groupId),
              preferred.botId,
            );
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
        if (bootstrapError instanceof EnterpriseAuthError && bootstrapError.status === 401) {
          provider.redirectToLogin();
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
  }, [enterprise, provider]);

  const switchContext = useCallback(
    async (candidates: ContextCandidate[], preferredBotId?: string, missingMessage = '所选范围内暂无可用 Agent') => {
      if (!context || contextSwitching || !provider) return;
      setContextSwitching(true);
      setContextError('');
      try {
        const resolved = await resolveFirstContext(provider, candidates, preferredBotId);
        if (!resolved) {
          setContextError(missingMessage);
          return;
        }
        activateContext(context.user.user_id, resolved, true);
      } catch (switchError) {
        if (switchError instanceof EnterpriseAuthError && switchError.status === 401) {
          provider.redirectToLogin();
          return;
        }
        setContextError(errorText(switchError));
      } finally {
        setContextSwitching(false);
      }
    },
    [context, contextSwitching, provider],
  );

  const contextValue = useMemo<EnterpriseContextValue | null>(() => {
    if (!context) return null;
    return {
      ...context,
      contextError,
      contextSwitching,
      onOrgChange: orgId => {
        const org = context.orgs.find(item => item.group_id === orgId);
        if (!org) {
          const normalized = orgId.trim();
          if (!normalized) return;
          activateContext(
            context.user.user_id,
            { ...context, org: { group_id: normalized, name: normalized } },
            true,
            true,
          );
          return;
        }
        const gateways = movePreferredFirst([...context.gateways], item => item.jiuwenclaw_id === context.gateway.jiuwenclaw_id);
        void switchContext(
          gateways.map(gateway => ({ gateway, org })),
          undefined,
          '该组织暂无可用 Agent',
        );
      },
      onGatewayChange: gatewayId => {
        const gateway = context.gateways.find(item => item.jiuwenclaw_id === gatewayId);
        if (!gateway) {
          const normalized = gatewayId.trim();
          if (!normalized) return;
          activateContext(
            context.user.user_id,
            {
              ...context,
              gateway: {
                jiuwenclaw_id: normalized,
                jiuwenclaw_name: normalized,
                gateway_endpoint: null,
              },
            },
            true,
            true,
          );
          return;
        }
        const orgs = movePreferredFirst([...context.orgs], item => item.group_id === context.org.group_id);
        void switchContext(
          orgs.map(org => ({ gateway, org })),
          undefined,
          '该组网暂无可用 Agent',
        );
      },
      onBotChange: botId => {
        const selected = context.agents.find(agent => agentRuntimeId(agent) === botId);
        if (botId === context.selectedBot) return;
        if (!selected) {
          const normalized = botId.trim();
          if (!normalized) return;
          activateContext(
            context.user.user_id,
            {
              gateway: context.gateway,
              org: context.org,
              agents: context.agents,
              selectedBot: normalized,
            },
            true,
            true,
          );
          return;
        }
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
