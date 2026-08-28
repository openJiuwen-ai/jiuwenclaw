import { FormEvent, ReactNode, useEffect, useState } from 'react';
import { setRuntimeScope } from './services/runtimeScope';
import { isEnterpriseMode } from './edition';

const ACCESS_KEY = 'openjiuwen_access_token';
const REFRESH_KEY = 'openjiuwen_refresh_token';

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

export function EnterpriseEntry({ children }: { children: ReactNode }) {
  const enabled = isEnterpriseMode();
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

  useEffect(() => {
    if (!enabled || ready || !localStorage.getItem(ACCESS_KEY)) return;
    void Promise.all([
      request<User>('/idp/v1/auth/me'),
      request<{ orgs: Org[] }>('/idp/v1/auth/me/orgs'),
      request<{ gateways: Gateway[] }>('/manager-api/v1/user-console/gateways', {}, true),
    ]).then(([me, orgResult, gatewayResult]) => {
      setUser(me); setOrgs(orgResult.orgs); setGateways(gatewayResult.gateways);
    }).catch(() => { localStorage.removeItem(ACCESS_KEY); localStorage.removeItem(REFRESH_KEY); });
  }, [enabled, ready]);

  useEffect(() => {
    if (!org || !gateway) { setAgents([]); return; }
    void request<{ agents: Agent[] }>(
      `/manager-api/v1/user-console/agents?group_id=${encodeURIComponent(org.group_id)}&jiuwenclaw_id=${encodeURIComponent(gateway.jiuwenclaw_id)}`,
      {}, true,
    ).then((result) => setAgents(result.agents)).catch((e) => setError(e.message));
  }, [org, gateway]);

  if (ready) return <>{children}</>;

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
    setRuntimeScope({
      userId: user.user_id,
      groupId: org.group_id,
      botId,
      gatewayId: gateway.jiuwenclaw_id,
    });
    const query = new URLSearchParams({
      user_id: user.user_id,
      group_id: org.group_id,
      bot_id: botId,
      gateway_id: gateway.jiuwenclaw_id,
    });
    window.history.replaceState({}, '', `${window.location.pathname}?${query}`);
    setReady(true);
  }

  const shell = (content: ReactNode) => <div style={{ minHeight: '100vh', display: 'grid', placeItems: 'center', background: '#f5f7fb', padding: 24 }}><div style={{ width: 420, background: '#fff', padding: 28, borderRadius: 12, boxShadow: '0 8px 30px #0001' }}>{content}{error && <div style={{ color: '#dc2626', marginTop: 12 }}>{error}</div>}</div></div>;
  if (!user) return shell(<form onSubmit={login}><h2>用户登录</h2><p>使用管理面统一身份中心账号登录</p><input style={{ width: '100%', padding: 10, marginTop: 12 }} placeholder="用户名" value={username} onChange={e => setUsername(e.target.value)} /><input style={{ width: '100%', padding: 10, marginTop: 12 }} type="password" placeholder="密码" value={password} onChange={e => setPassword(e.target.value)} /><button style={{ width: '100%', padding: 10, marginTop: 16 }} disabled={!username || !password}>登录</button></form>);
  if (!gateway) return shell(<><h2>选择组网</h2>{gateways.map(g => <button key={g.jiuwenclaw_id} style={{ width: '100%', padding: 10, marginTop: 8 }} onClick={() => setGateway(g)}>{g.jiuwenclaw_name}</button>)}</>);
  if (!org) return shell(<><h2>选择组织</h2>{orgs.map(o => <button key={o.group_id} style={{ width: '100%', padding: 10, marginTop: 8 }} onClick={() => setOrg(o)}>{o.name}</button>)}</>);
  return shell(<><h2>选择 Bot</h2>{agents.map(a => <button key={a.resource_id || a.template_id} style={{ width: '100%', padding: 10, marginTop: 8 }} onClick={() => enter(a)}>{a.template_name}</button>)}{agents.length === 0 && <p>暂无可用 Bot</p>}</>);
}
