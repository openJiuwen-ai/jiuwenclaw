import { FormEvent, ReactNode, useEffect, useMemo, useState } from 'react';
import { setRuntimeScope } from './services/runtimeScope';
import { isEnterpriseMode } from './edition';
import { EnterpriseContext } from './services/enterpriseContext';

const ACCESS_KEY = 'openjiuwen_access_token';
const REFRESH_KEY = 'openjiuwen_refresh_token';
const CONTEXT_KEY = 'jiuwenclaw:enterprise-context';
type CachedContext = { userId: string; gatewayId: string; orgId: string; botId: string };

function readCachedContext(): CachedContext | null {
  try {
    const value = JSON.parse(localStorage.getItem(CONTEXT_KEY) || 'null') as CachedContext | null;
    return value && value.userId && value.gatewayId && value.orgId && value.botId ? value : null;
  } catch { return null; }
}


type User = { user_id: string; display_name: string };
type Org = { group_id: string; name: string };
type Gateway = { jiuwenclaw_id: string; jiuwenclaw_name: string; gateway_endpoint: string | null };
type Agent = { template_id: string; template_name: string; resource_id?: string };
type TokenResponse = { access_token: string; refresh_token: string };

async function request<T>(url: string, init: RequestInit = {}, unwrap = false): Promise<T> {
  const token = localStorage.getItem(ACCESS_KEY);
  const headers = new Headers(init.headers);
  if (token) headers.set('Authorization', `Bearer ${token}`);
  if (init.body && !(init.body instanceof URLSearchParams)) headers.set('Content-Type', 'application/json');
  const response = await fetch(url, { ...init, headers });
  const json = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(String(json.detail || json.message || response.statusText));
  return (unwrap && json && typeof json === 'object' && 'data' in json ? json.data : json) as T;
}

function runtimeScopeReady(): boolean {
  const query = new URLSearchParams(window.location.search);
  return Boolean(query.get('user_id') && query.get('group_id') && query.get('bot_id') && query.get('gateway_id'));
}

function persistRuntimeScopeUrl(userId: string, groupId: string, botId: string, gatewayId: string): void {
  const query = new URLSearchParams(window.location.search);
  query.set('user_id', userId);
  query.set('group_id', groupId);
  query.set('bot_id', botId);
  query.set('gateway_id', gatewayId);
  window.history.replaceState({}, '', `${window.location.pathname}?${query.toString()}`);
}

export function EnterpriseEntry({ children }: { children: ReactNode }) {
  const enabled = isEnterpriseMode();
  const cached = useMemo(readCachedContext, []);
  const [ready, setReady] = useState(!enabled || runtimeScopeReady());
  const [user, setUser] = useState<User | null>(null);
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [orgs, setOrgs] = useState<Org[]>([]);
  const [gateways, setGateways] = useState<Gateway[]>([]);
  const [org, setOrg] = useState<Org | null>(null);
  const [gateway, setGateway] = useState<Gateway | null>(null);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [selectedBot, setSelectedBot] = useState<string | null>(null);

  useEffect(() => {
    if (!enabled || user || !localStorage.getItem(ACCESS_KEY)) return;
    void Promise.all([
      request<User>('/idp/v1/auth/me'),
      request<{ orgs: Org[] }>('/idp/v1/auth/me/orgs'),
      request<{ gateways: Gateway[] }>('/manager-api/v1/user-console/gateways', {}, true),
    ]).then(([me, orgResult, gatewayResult]) => {
      setUser(me); setOrgs(orgResult.orgs); setGateways(gatewayResult.gateways);
      const query = new URLSearchParams(window.location.search);
      const saved = cached?.userId === me.user_id ? cached : null;
      const gatewayId = query.get('gateway_id') || saved?.gatewayId;
      const orgId = query.get('group_id') || saved?.orgId;
      const botId = query.get('bot_id') || saved?.botId;
      setGateway(gatewayResult.gateways.find((item) => item.jiuwenclaw_id === gatewayId) || null);
      setOrg(orgResult.orgs.find((item) => item.group_id === orgId) || null);
      setSelectedBot(botId || null);
    }).catch(() => { localStorage.removeItem(ACCESS_KEY); localStorage.removeItem(REFRESH_KEY); localStorage.removeItem(CONTEXT_KEY); });
  }, [enabled, ready]);

  useEffect(() => {
    if (!org || !gateway) { setAgents([]); return; }
    void request<{ agents: Agent[] }>(
      `/manager-api/v1/user-console/agents?group_id=${encodeURIComponent(org.group_id)}&jiuwenclaw_id=${encodeURIComponent(gateway.jiuwenclaw_id)}`,
      {}, true,
    ).then((result) => setAgents(result.agents)).catch((e) => setError(e.message));
  }, [org, gateway]);

  useEffect(() => {
    if (!user || !org || !gateway || !selectedBot) return;
    const agent = agents.find((item) => (item.resource_id || item.template_id) === selectedBot);
    if (!agent) return;
    setRuntimeScope({ userId: user.user_id, groupId: org.group_id, botId: selectedBot, gatewayId: gateway.jiuwenclaw_id });
    persistRuntimeScopeUrl(user.user_id, org.group_id, selectedBot, gateway.jiuwenclaw_id);
    setReady(true);
  }, [agents, gateway, org, selectedBot, user]);

  if (ready && user && org && gateway && selectedBot) return <EnterpriseContext.Provider value={{ user, org, orgs, gateway, gateways, agents, selectedBot, onOrgChange: (id) => { setOrg(orgs.find((item) => item.group_id === id) || null); setSelectedBot(null); setReady(false); }, onGatewayChange: (id) => { setGateway(gateways.find((item) => item.jiuwenclaw_id === id) || null); setOrg(null); setSelectedBot(null); setReady(false); }, onBotChange: (id) => { const agent = agents.find((item) => (item.resource_id || item.template_id) === id); if (agent) enter(agent); } }}>{children}</EnterpriseContext.Provider>;

  async function login(event: FormEvent) {
    event.preventDefault(); setError('');
    try {
      const body = new URLSearchParams({ username: username.trim(), password });
      const token = await request<TokenResponse>('/idp/v1/auth/token', { method: 'POST', body });
      localStorage.setItem(ACCESS_KEY, token.access_token);
      localStorage.setItem(REFRESH_KEY, token.refresh_token);
      const [me, orgResult, gatewayResult] = await Promise.all([
        request<User>('/idp/v1/auth/me'),
        request<{ orgs: Org[] }>('/idp/v1/auth/me/orgs'),
        request<{ gateways: Gateway[] }>('/manager-api/v1/user-console/gateways', {}, true),
      ]);
      setUser(me); setOrgs(orgResult.orgs); setGateways(gatewayResult.gateways);
    } catch (e) { setError(e instanceof Error ? e.message : '登录失败'); }
  }

  function enter(agent: Agent) {
    if (!user || !org || !gateway) return;
    const botId = agent.resource_id || agent.template_id;
    localStorage.setItem(CONTEXT_KEY, JSON.stringify({ userId: user.user_id, gatewayId: gateway.jiuwenclaw_id, orgId: org.group_id, botId }));
    setSelectedBot(botId || null);
    setRuntimeScope({
      userId: user.user_id,
      groupId: org.group_id,
      botId,
      gatewayId: gateway.jiuwenclaw_id,
    });
    persistRuntimeScopeUrl(user.user_id, org.group_id, botId, gateway.jiuwenclaw_id);
    setReady(true);
  }

  const shell = (content: ReactNode) => <div className="enterprise-entry"><div className="enterprise-entry__glow" /><div className="enterprise-entry__card"><div className="enterprise-entry__brand">JIUWEN<span>CLAW</span></div>{content}{error && <div className="enterprise-entry__error">{error}</div>}</div></div>;
  if (!user) return shell(<form onSubmit={login}><div className="enterprise-entry__eyebrow">ENTERPRISE WORKSPACE</div><h1>欢迎回来</h1><p>使用管理面统一身份中心账号登录，进入你的智能工作空间。</p><input className="enterprise-entry__input" placeholder="用户名" value={username} onChange={e => setUsername(e.target.value)} /><input className="enterprise-entry__input" type="password" placeholder="密码" value={password} onChange={e => setPassword(e.target.value)} /><button className="enterprise-entry__button" disabled={!username || !password}>登录工作空间</button></form>);
  if (!gateway) return shell(<><div className="enterprise-entry__eyebrow">STEP 1 / 3</div><h1>选择组网</h1><p>请选择管理面已授权的组网。</p>{gateways.map(g => <button className="enterprise-entry__option" key={g.jiuwenclaw_id} onClick={() => setGateway(g)}><strong>{g.jiuwenclaw_name}</strong><small>{g.gateway_endpoint || g.jiuwenclaw_id}</small></button>)}</>);
  if (!org) return shell(<><div className="enterprise-entry__eyebrow">STEP 2 / 3</div><h1>选择租户</h1><p>选择你要进入的企业工作空间。</p>{orgs.map(o => <button className="enterprise-entry__option" key={o.group_id} onClick={() => setOrg(o)}><strong>{o.name}</strong><small>{o.group_id}</small></button>)}</>);
  return <>{!ready && shell(<><div className="enterprise-entry__eyebrow">STEP 3 / 3</div><h1>选择 Bot</h1><p>选择一个 Bot 开始工作，之后会自动记住你的选择。</p>{agents.map(a => <button className="enterprise-entry__option" key={a.resource_id || a.template_id} onClick={() => enter(a)}><strong>{a.template_name}</strong><small>{a.resource_id || a.template_id}</small></button>)}{agents.length === 0 && <p>暂无可用 Bot</p>}</>)}</>;

}
