import type {
  ConfigDefaultTemplateMapping,
  ConfigDefaultTemplateMappingCreateBody,
  ConfigDefaultTemplateMappingUpdateBody,
  MappingScopeType,
  ConfigEffectiveAgentPolicy,
  ConfigEffectiveAgentPolicyCreateBody,
  ConfigEffectiveAgentPolicyUpdateBody,
  ConfigEffectiveGlobalPolicy,
  ConfigEffectiveGlobalPolicyCreateBody,
  ConfigEffectiveGlobalPolicyUpdateBody,
  ConfigEffectiveServicePolicy,
  ConfigEffectiveServicePolicyCreateBody,
  ConfigEffectiveServicePolicyUpdateBody,
  CreateInstanceBody,
  ExtensionConfigTemplate,
  ExtensionConfigTemplateCreateBody,
  ExtensionConfigTemplateUpdateBody,
  InstanceDetail,
  InstanceSummary,
  ManagerWsStatus,
  ModelTemplate,
  ModelTemplateCreateBody,
  ModelTemplateUpdateBody,
  PageResult,
  SkillWhitelistTemplate,
  SkillWhitelistTemplateCreateBody,
  SkillWhitelistTemplateUpdateBody,
  ServiceConfigTemplate,
  ServiceConfigTemplateCreateBody,
  ServiceConfigTemplateUpdateBody,
  ProvisionLocalInstanceBody,
  ResponseModel,
  ChannelConfig,
  ChannelRegisterBody,
  LogMaskingRule,
  LogMaskingRuleCreateBody,
  LogMaskingRuleUpdateBody,
  ListItemsResult,
} from '../types';

// 平台管理 API(claw_manager) 与 认证/目录 API(独立认证服务) 两个反代前缀。
const API_BASE = (import.meta.env.VITE_API_BASE ?? '/api').replace(/\/$/, '');
const IDP_BASE = (import.meta.env.VITE_IDP_BASE ?? '/idp').replace(/\/$/, '');

// ---------- 认证 token（access JWT + refresh，localStorage 持久化）----------
const ACCESS_KEY = 'claw_access_token';
const REFRESH_KEY = 'claw_refresh_token';
let accessToken: string | null = localStorage.getItem(ACCESS_KEY);
let refreshToken: string | null = localStorage.getItem(REFRESH_KEY);
let unauthorizedHandler: (() => void) | null = null;

export function setTokens(access: string | null, refresh?: string | null): void {
  accessToken = access;
  if (access) localStorage.setItem(ACCESS_KEY, access);
  else localStorage.removeItem(ACCESS_KEY);
  if (refresh !== undefined) {
    refreshToken = refresh;
    if (refresh) localStorage.setItem(REFRESH_KEY, refresh);
    else localStorage.removeItem(REFRESH_KEY);
  }
}
export function clearTokens(): void {
  setTokens(null, null);
}
export function getAccessToken(): string | null {
  return accessToken;
}
export function hasSession(): boolean {
  return !!accessToken;
}
/** 注册"会话失效(401)"回调：由 AuthProvider 设置为登出并回到登录页。 */
export function setUnauthorizedHandler(fn: (() => void) | null): void {
  unauthorizedHandler = fn;
}

interface FastApiValidationErrorItem {
  type?: string;
  loc?: unknown[];
  msg?: string;
}

/** 将 FastAPI / Pydantic 的 detail（string | object[]）转为可读文案。 */
export function formatApiErrorDetail(detail: unknown): string {
  if (detail == null) return '';
  if (typeof detail === 'string') return detail.trim();
  if (Array.isArray(detail)) {
    const messages = detail
      .map((item) => {
        if (typeof item === 'string') return item.trim();
        if (item && typeof item === 'object' && 'msg' in item) {
          return String((item as FastApiValidationErrorItem).msg || '')
            .trim()
            .replace(/^Value error,\s*/i, '');
        }
        return '';
      })
      .filter(Boolean);
    return messages.join('；') || '请求参数校验失败';
  }
  if (typeof detail === 'object') {
    const obj = detail as Record<string, unknown>;
    if (typeof obj.message === 'string') return obj.message.trim();
    if (typeof obj.msg === 'string') return obj.msg.trim();
  }
  return String(detail);
}

export class ApiError extends Error {
  constructor(public status: number, public detail: string, public raw?: unknown) {
    super(detail || `HTTP ${status}`);
    this.name = 'ApiError';
  }
}

interface RequestOptions {
  method?: string;
  body?: unknown;
  query?: Record<string, string | number | boolean | null | undefined>;
}

function buildQuery(query?: RequestOptions['query']) {
  if (!query) return '';
  const usp = new URLSearchParams();
  for (const [k, v] of Object.entries(query)) {
    if (v === undefined || v === null || v === '') continue;
    usp.append(k, String(v));
  }
  const s = usp.toString();
  return s ? `?${s}` : '';
}

/** 用 refresh token 续期一次（成功则写入新 token）。 */
async function tryRefresh(): Promise<boolean> {
  if (!refreshToken) return false;
  try {
    const resp = await fetch(`${IDP_BASE}/v1/auth/refresh`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh_token: refreshToken }),
    });
    if (!resp.ok) return false;
    const t = (await resp.json()) as TokenResponse;
    setTokens(t.access_token, t.refresh_token);
    return true;
  } catch {
    return false;
  }
}

async function requestCore<T>(
  base: string, path: string, opts: RequestOptions, unwrap: boolean, retried = false,
): Promise<T> {
  const url = `${base}${path}${buildQuery(opts.query)}`;
  const headers: Record<string, string> = { 'Content-Type': 'application/json' };
  if (accessToken) headers.Authorization = `Bearer ${accessToken}`;
  const init: RequestInit = { method: opts.method ?? 'GET', headers };
  if (opts.body !== undefined) init.body = JSON.stringify(opts.body);

  let resp: Response;
  try {
    resp = await fetch(url, init);
  } catch (e) {
    throw new ApiError(0, `network error: ${(e as Error).message}`);
  }

  // 会话失效：401 且不是 token/refresh 端点 → 先试 refresh 续期重试一次,失败再全局登出。
  if (
    resp.status === 401 && accessToken && !retried &&
    !path.includes('/auth/token') && !path.includes('/auth/refresh')
  ) {
    if (await tryRefresh()) return requestCore<T>(base, path, opts, unwrap, true);
    unauthorizedHandler?.();
  }

  let json: unknown = null;
  const text = await resp.text();
  if (text) {
    try { json = JSON.parse(text); } catch { /* 非 JSON 响应 */ }
  }
  if (!resp.ok) {
    const rawDetail =
      json && typeof json === 'object' && 'detail' in (json as Record<string, unknown>)
        ? (json as { detail: unknown }).detail
        : undefined;
    throw new ApiError(resp.status, formatApiErrorDetail(rawDetail) || resp.statusText, json);
  }
  // manager API 返回 ResponseModel<T> 包装；认证服务返回原始 JSON(unwrap=false)。
  if (unwrap && json && typeof json === 'object' && 'code' in (json as Record<string, unknown>) && 'data' in (json as Record<string, unknown>)) {
    const wrapped = json as ResponseModel<T>;
    if (wrapped.code !== 200) {
      throw new ApiError(resp.status, wrapped.message || 'unknown error', json);
    }
    return wrapped.data as T;
  }
  return json as T;
}

/** 平台管理 API(claw_manager, /api)——拆 ResponseModel。 */
function http<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  return requestCore<T>(API_BASE, path, opts, true);
}
/** 认证/目录 API(独立认证服务, /idp)——原始 JSON。 */
function idpHttp<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  return requestCore<T>(IDP_BASE, path, opts, false);
}

// ---------- System ----------

export const SystemApi = {
  health: () => http<{ status: string }>('/health'),
  managerWsStatus: () => http<ManagerWsStatus>('/manager-ws/status'),
};

// ---------- Auth ----------

export interface AuthUser {
  user_id: string;
  display_name: string;
  is_admin: boolean;
  status: string;
  groups?: string[];
}
export interface TokenResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
  refresh_token: string;
}

// 认证全部走独立认证服务(经 /idp 反代)。claw_manager 不再有登录端点。
export const AuthApi = {
  /** OAuth2 密码流：表单 POST /token → 存 access+refresh，再取 /me 返回用户。 */
  login: async (username: string, password: string): Promise<AuthUser> => {
    const body = new URLSearchParams({ username, password });
    const resp = await fetch(`${IDP_BASE}/v1/auth/token`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body,
    });
    if (!resp.ok) {
      let detail = resp.statusText;
      try {
        const j = (await resp.json()) as { detail?: unknown };
        detail = formatApiErrorDetail(j?.detail) || detail;
      } catch { /* 非 JSON */ }
      throw new ApiError(resp.status, detail);
    }
    const t = (await resp.json()) as TokenResponse;
    setTokens(t.access_token, t.refresh_token);
    return idpHttp<AuthUser>('/v1/auth/me');
  },
  me: () => idpHttp<AuthUser>('/v1/auth/me'),
  myOrgs: () => idpHttp<{ orgs: Org[] }>('/v1/auth/me/orgs'),
  logout: async (): Promise<void> => {
    try {
      if (refreshToken) {
        await idpHttp('/v1/auth/logout', { method: 'POST', body: { refresh_token: refreshToken } });
      }
    } catch { /* 忽略登出请求错误 */ }
    clearTokens();
  },
};

// ---------- IAM（组织 / 用户 / bot）----------

export interface Org {
  group_id: string;
  name: string;
  status: string;
  created_at: string | null;
  updated_at: string | null;
}
export interface IamUser {
  user_id: string;
  display_name: string;
  is_admin: boolean;
  status: string;
  created_at: string | null;
  updated_at: string | null;
  group_ids?: string[];
}
export type BotScopeType = 'global' | 'org' | 'user';
export interface BotVisibility {
  id: number;
  bot_id: string;
  scope_type: BotScopeType;
  scope_id: string;
}
export interface Bot {
  bot_id: string;
  name: string;
  description: string | null;
  status: string;
  created_at: string | null;
  updated_at: string | null;
  visibility?: BotVisibility[];
}
interface IamPaged<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

export const OrgApi = {
  list: (page = 1, page_size = 200) => idpHttp<IamPaged<Org>>('/v1/orgs/', { query: { page, page_size } }),
  create: (body: { group_id?: string; name: string }) => idpHttp<Org>('/v1/orgs/', { method: 'POST', body }),
  update: (gid: string, body: { name?: string; status?: string }) =>
    idpHttp<Org>(`/v1/orgs/${encodeURIComponent(gid)}`, { method: 'PATCH', body }),
  remove: (gid: string) => idpHttp<{ deleted: boolean }>(`/v1/orgs/${encodeURIComponent(gid)}`, { method: 'DELETE' }),
  listMembers: (gid: string) => idpHttp<{ users: IamUser[] }>(`/v1/orgs/${encodeURIComponent(gid)}/members`),
  addMembers: (gid: string, user_ids: string[]) =>
    idpHttp<{ added: string[] }>(`/v1/orgs/${encodeURIComponent(gid)}/members`, { method: 'POST', body: { user_ids } }),
  removeMember: (gid: string, userId: string) =>
    idpHttp<{ removed: boolean }>(`/v1/orgs/${encodeURIComponent(gid)}/members/${encodeURIComponent(userId)}`, { method: 'DELETE' }),
};

/** 无组织保留组的 group_id（与后端 NO_ORG_GROUP_ID 一致）。 */
export const NO_ORG_GROUP_ID = '__none__';

export const UserApi = {
  list: (page = 1, page_size = 200) => idpHttp<IamPaged<IamUser>>('/v1/users/', { query: { page, page_size } }),
  get: (id: string) => idpHttp<IamUser>(`/v1/users/${encodeURIComponent(id)}`),
  create: (body: { user_id?: string; display_name: string; is_admin?: boolean; username: string; password: string }) =>
    idpHttp<IamUser>('/v1/users/', { method: 'POST', body }),
  update: (id: string, body: { display_name?: string; is_admin?: boolean; status?: string; password?: string }) =>
    idpHttp<IamUser>(`/v1/users/${encodeURIComponent(id)}`, { method: 'PATCH', body }),
  remove: (id: string) => idpHttp<{ deleted: boolean }>(`/v1/users/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  setOrgs: (id: string, group_ids: string[]) =>
    idpHttp<{ group_ids: string[] }>(`/v1/users/${encodeURIComponent(id)}/orgs`, { method: 'PUT', body: { group_ids } }),
};

export const BotApi = {
  list: (page = 1, page_size = 200) => http<IamPaged<Bot>>('/v1/bots/', { query: { page, page_size } }),
  get: (id: string) => http<Bot>(`/v1/bots/${encodeURIComponent(id)}`),
  create: (body: { bot_id?: string; name: string; description?: string }) =>
    http<Bot>('/v1/bots/', { method: 'POST', body }),
  update: (id: string, body: { name?: string; description?: string; status?: string }) =>
    http<Bot>(`/v1/bots/${encodeURIComponent(id)}`, { method: 'PATCH', body }),
  remove: (id: string) => http<{ deleted: boolean }>(`/v1/bots/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  setVisibility: (id: string, scopes: { scope_type: string; scope_id: string | null }[]) =>
    http<{ visibility: BotVisibility[] }>(`/v1/bots/${encodeURIComponent(id)}/visibility`, { method: 'PUT', body: { scopes } }),
};

// 当前登录用户视角（用户面）：组织来自认证服务,可见 bot 来自管理 API。
export const MeApi = {
  orgs: () => idpHttp<{ orgs: Org[] }>('/v1/auth/me/orgs'),
  bots: (groupId: string) => http<{ bots: Bot[] }>('/v1/me/bots', { query: { group_id: groupId } }),
};

// ---------- Instances ----------

interface InstancePageRaw {
  items: InstanceSummary[];
  total: number;
  page: number;
  page_size: number;
}

export const InstanceApi = {
  list: (params?: {
    page?: number;
    page_size?: number;
    status?: string;
    search?: string;
    sort_by?: 'jiuwenclaw_name' | 'status' | 'last_heartbeat' | 'k8s_namespace' | 'updated_at';
    sort_order?: 'asc' | 'desc';
  }) =>
    http<InstancePageRaw>('/v1/instances/', { query: params }),
  get: (id: string) => http<InstanceDetail>(`/v1/instances/${encodeURIComponent(id)}`),
  create: (body: CreateInstanceBody) => http<InstanceSummary>('/v1/instances/', { method: 'POST', body }),
  provisionLocal: (body: ProvisionLocalInstanceBody) =>
    http<Record<string, unknown>>('/v1/instances/provision-local', { method: 'POST', body }),
  update: (id: string, body: { data?: Record<string, unknown> }) =>
    http<InstanceDetail>(`/v1/instances/${encodeURIComponent(id)}`, { method: 'PATCH', body }),
  remove: (id: string, force = false) =>
    http<{ deleted: boolean }>(`/v1/instances/${encodeURIComponent(id)}`, {
      method: 'DELETE',
      query: { force },
    }),
};

// ---------- Templates ----------

export const ModelTemplateApi = {
  list: (params?: {
    page?: number;
    page_size?: number;
    enabled?: boolean;
    model_type?: string;
    model_provider?: string;
    search?: string;
    sort_by?:
      | 'template_name'
      | 'description'
      | 'model_provider'
      | 'model_id'
      | 'model_type'
      | 'api_base'
      | 'updated_at';
    sort_order?: 'asc' | 'desc';
  }) =>
    http<PageResult<ModelTemplate>>('/v1/model-templates', { query: params }),
  get: (id: string) => http<ModelTemplate>(`/v1/model-templates/${encodeURIComponent(id)}`),
  create: (body: ModelTemplateCreateBody) =>
    http<ModelTemplate>('/v1/model-templates', { method: 'POST', body }),
  update: (id: string, body: ModelTemplateUpdateBody) =>
    http<ModelTemplate>(`/v1/model-templates/${encodeURIComponent(id)}`, { method: 'PATCH', body }),
  remove: (id: string) =>
    http<{ deleted: boolean; template_id: string }>(`/v1/model-templates/${encodeURIComponent(id)}`, {
      method: 'DELETE',
    }),
};

export const ExtensionTemplateApi = {
  list: (params?: {
    page?: number;
    page_size?: number;
    enabled?: boolean;
    component?: string;
    hook_type?: string;
    search?: string;
    sort_by?: 'template_name' | 'description' | 'component' | 'hook_type' | 'updated_at';
    sort_order?: 'asc' | 'desc';
  }) => http<PageResult<ExtensionConfigTemplate>>('/v1/extension-config-templates', { query: params }),
  get: (id: string) =>
    http<ExtensionConfigTemplate>(`/v1/extension-config-templates/${encodeURIComponent(id)}`),
  create: (body: ExtensionConfigTemplateCreateBody) =>
    http<ExtensionConfigTemplate>('/v1/extension-config-templates', { method: 'POST', body }),
  update: (id: string, body: ExtensionConfigTemplateUpdateBody) =>
    http<ExtensionConfigTemplate>(`/v1/extension-config-templates/${encodeURIComponent(id)}`, {
      method: 'PATCH',
      body,
    }),
  remove: (id: string) =>
    http<{ deleted: boolean; template_id: string }>(
      `/v1/extension-config-templates/${encodeURIComponent(id)}`,
      { method: 'DELETE' }
    ),
};

export const SkillWhitelistTemplateApi = {
  list: (params?: {
    page?: number;
    page_size?: number;
    enabled?: boolean;
    skill_id?: string;
    skill_source?: string;
    search?: string;
    sort_by?:
      | 'template_name'
      | 'description'
      | 'skill_source'
      | 'skill_id'
      | 'skill_version'
      | 'updated_at';
    sort_order?: 'asc' | 'desc';
  }) => http<PageResult<SkillWhitelistTemplate>>('/v1/skill-whitelist-templates', { query: params }),
  get: (id: string) =>
    http<SkillWhitelistTemplate>(`/v1/skill-whitelist-templates/${encodeURIComponent(id)}`),
  create: (body: SkillWhitelistTemplateCreateBody) =>
    http<SkillWhitelistTemplate>('/v1/skill-whitelist-templates', { method: 'POST', body }),
  update: (id: string, body: SkillWhitelistTemplateUpdateBody) =>
    http<SkillWhitelistTemplate>(`/v1/skill-whitelist-templates/${encodeURIComponent(id)}`, {
      method: 'PATCH',
      body,
    }),
  remove: (id: string) =>
    http<{ deleted: boolean; template_id: string }>(
      `/v1/skill-whitelist-templates/${encodeURIComponent(id)}`,
      { method: 'DELETE' }
    ),
};

export const ServiceConfigTemplateApi = {
  list: (params?: {
    page?: number;
    page_size?: number;
    enabled?: boolean;
    namespace?: string;
    search?: string;
    sort_by?: 'template_name' | 'description' | 'agent_image' | 'updated_at';
    sort_order?: 'asc' | 'desc';
  }) => http<PageResult<ServiceConfigTemplate>>('/v1/service-config-templates', { query: params }),
  get: (id: string) =>
    http<ServiceConfigTemplate>(`/v1/service-config-templates/${encodeURIComponent(id)}`),
  create: (body: ServiceConfigTemplateCreateBody) =>
    http<ServiceConfigTemplate>('/v1/service-config-templates', { method: 'POST', body }),
  update: (id: string, body: ServiceConfigTemplateUpdateBody) =>
    http<ServiceConfigTemplate>(`/v1/service-config-templates/${encodeURIComponent(id)}`, {
      method: 'PATCH',
      body,
    }),
  remove: (id: string) =>
    http<{ deleted: boolean; template_id: string }>(
      `/v1/service-config-templates/${encodeURIComponent(id)}`,
      { method: 'DELETE' }
    ),
};

// ---------- Policies (per-instance) ----------

function policyBase(instanceId: string) {
  return `/v1/instances/${encodeURIComponent(instanceId)}`;
}

export const MappingApi = {
  list: (
    instanceId: string,
    params?: {
      page?: number;
      page_size?: number;
      scope_type?: MappingScopeType;
      scope_id?: string;
      template_type?: string;
      template_id?: string;
      enabled?: boolean;
      search?: string;
      sort_by?:
        | 'policy_name'
        | 'policy_desc'
        | 'priority'
        | 'scope_type'
        | 'scope_id'
        | 'template_type'
        | 'template_id'
        | 'updated_at';
      sort_order?: 'asc' | 'desc';
    }
  ) =>
    http<PageResult<ConfigDefaultTemplateMapping>>(
      `${policyBase(instanceId)}/config-default-template-mappings/`,
      { query: params }
    ),
  create: (instanceId: string, body: ConfigDefaultTemplateMappingCreateBody) =>
    http<ConfigDefaultTemplateMapping>(
      `${policyBase(instanceId)}/config-default-template-mappings/`,
      { method: 'POST', body }
    ),
  update: (instanceId: string, mappingId: number, body: ConfigDefaultTemplateMappingUpdateBody) =>
    http<ConfigDefaultTemplateMapping>(
      `${policyBase(instanceId)}/config-default-template-mappings/${mappingId}`,
      { method: 'PATCH', body }
    ),
  remove: (instanceId: string, mappingId: number) =>
    http<{ deleted: boolean; id: number }>(
      `${policyBase(instanceId)}/config-default-template-mappings/${mappingId}`,
      { method: 'DELETE' }
    ),
};

export const GlobalPolicyApi = {
  list: (
    instanceId: string,
    params?: {
      page?: number;
      page_size?: number;
      enabled?: boolean;
      search?: string;
      sort_by?: 'policy_name' | 'policy_desc' | 'priority' | 'updated_at';
      sort_order?: 'asc' | 'desc';
    }
  ) =>
    http<PageResult<ConfigEffectiveGlobalPolicy>>(
      `${policyBase(instanceId)}/config-effective/global-policies/`,
      { query: params }
    ),
  create: (instanceId: string, body: ConfigEffectiveGlobalPolicyCreateBody) =>
    http<ConfigEffectiveGlobalPolicy>(
      `${policyBase(instanceId)}/config-effective/global-policies/`,
      { method: 'POST', body }
    ),
  update: (instanceId: string, policyId: number, body: ConfigEffectiveGlobalPolicyUpdateBody) =>
    http<ConfigEffectiveGlobalPolicy>(
      `${policyBase(instanceId)}/config-effective/global-policies/${policyId}`,
      { method: 'PATCH', body }
    ),
  remove: (instanceId: string, policyId: number) =>
    http<{ deleted: boolean; id: number }>(
      `${policyBase(instanceId)}/config-effective/global-policies/${policyId}`,
      { method: 'DELETE' }
    ),
};

export const ServicePolicyApi = {
  list: (
    instanceId: string,
    params?: {
      page?: number;
      page_size?: number;
      enabled?: boolean;
      search?: string;
      sort_by?: 'policy_name' | 'policy_desc' | 'priority' | 'match_expr' | 'service_id' | 'updated_at';
      sort_order?: 'asc' | 'desc';
    }
  ) =>
    http<PageResult<ConfigEffectiveServicePolicy>>(
      `${policyBase(instanceId)}/config-effective/service-policies/`,
      { query: params }
    ),
  create: (instanceId: string, body: ConfigEffectiveServicePolicyCreateBody) =>
    http<ConfigEffectiveServicePolicy>(
      `${policyBase(instanceId)}/config-effective/service-policies/`,
      { method: 'POST', body }
    ),
  update: (instanceId: string, policyId: number, body: ConfigEffectiveServicePolicyUpdateBody) =>
    http<ConfigEffectiveServicePolicy>(
      `${policyBase(instanceId)}/config-effective/service-policies/${policyId}`,
      { method: 'PATCH', body }
    ),
  remove: (instanceId: string, policyId: number) =>
    http<{ deleted: boolean; id: number }>(
      `${policyBase(instanceId)}/config-effective/service-policies/${policyId}`,
      { method: 'DELETE' }
    ),
};

export const AgentPolicyApi = {
  list: (
    instanceId: string,
    params?: {
      page?: number;
      page_size?: number;
      service_policy_id?: string;
      enabled?: boolean;
      send_file_allowed?: boolean;
      search?: string;
      sort_by?:
        | 'policy_name'
        | 'policy_desc'
        | 'service_policy_id'
        | 'priority'
        | 'match_expr'
        | 'agent_id'
        | 'updated_at';
      sort_order?: 'asc' | 'desc';
    }
  ) =>
    http<PageResult<ConfigEffectiveAgentPolicy>>(
      `${policyBase(instanceId)}/config-effective/agent-policies/`,
      { query: params }
    ),
  create: (instanceId: string, body: ConfigEffectiveAgentPolicyCreateBody) =>
    http<ConfigEffectiveAgentPolicy>(
      `${policyBase(instanceId)}/config-effective/agent-policies/`,
      { method: 'POST', body }
    ),
  update: (instanceId: string, policyId: number, body: ConfigEffectiveAgentPolicyUpdateBody) =>
    http<ConfigEffectiveAgentPolicy>(
      `${policyBase(instanceId)}/config-effective/agent-policies/${policyId}`,
      { method: 'PATCH', body }
    ),
  remove: (instanceId: string, policyId: number) =>
    http<{ deleted: boolean; id: number }>(
      `${policyBase(instanceId)}/config-effective/agent-policies/${policyId}`,
      { method: 'DELETE' }
    ),
};

// ---------- Application Config (per-instance) ----------

function instanceBase(instanceId: string) {
  return `/v1/instances/${encodeURIComponent(instanceId)}`;
}

export const ChannelApi = {
  list: (
    instanceId: string,
    params?: { channel_type?: string; status?: string }
  ) =>
    http<ListItemsResult<ChannelConfig>>(`${instanceBase(instanceId)}/channels`, { query: params }),
  register: (instanceId: string, body: ChannelRegisterBody) =>
    http<{ channel_id: string }>(`${instanceBase(instanceId)}/channels`, { method: 'POST', body }),
  activate: (instanceId: string, channelId: string) =>
    http<{ channel_id: string; status: string }>(
      `${instanceBase(instanceId)}/channels/${encodeURIComponent(channelId)}/activate`,
      { method: 'POST' }
    ),
  deactivate: (instanceId: string, channelId: string, body?: { graceful?: boolean; timeout?: number }) =>
    http<{ channel_id: string; status: string }>(
      `${instanceBase(instanceId)}/channels/${encodeURIComponent(channelId)}/deactivate`,
      { method: 'POST', body: body ?? { graceful: true, timeout: 30 } }
    ),
  remove: (instanceId: string, channelId: string) =>
    http<void>(`${instanceBase(instanceId)}/channels/${encodeURIComponent(channelId)}`, {
      method: 'DELETE',
    }),
};

export const LogMaskingRuleApi = {
  list: (instanceId: string, params?: { enabled?: boolean }) =>
    http<ListItemsResult<LogMaskingRule>>(`${instanceBase(instanceId)}/log-masking-rules`, {
      query: params,
    }),
  get: (instanceId: string, ruleId: string) =>
    http<LogMaskingRule>(
      `${instanceBase(instanceId)}/log-masking-rules/${encodeURIComponent(ruleId)}`
    ),
  create: (instanceId: string, body: LogMaskingRuleCreateBody) =>
    http<LogMaskingRule>(`${instanceBase(instanceId)}/log-masking-rules`, { method: 'POST', body }),
  update: (instanceId: string, ruleId: string, body: LogMaskingRuleUpdateBody) =>
    http<LogMaskingRule>(
      `${instanceBase(instanceId)}/log-masking-rules/${encodeURIComponent(ruleId)}`,
      { method: 'PATCH', body }
    ),
  remove: (instanceId: string, ruleId: string) =>
    http<void>(`${instanceBase(instanceId)}/log-masking-rules/${encodeURIComponent(ruleId)}`, {
      method: 'DELETE',
    }),
};
