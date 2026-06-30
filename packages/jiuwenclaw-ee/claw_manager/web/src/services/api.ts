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

const API_BASE = (import.meta.env.VITE_API_BASE ?? '/api').replace(/\/$/, '');

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

async function http<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  const url = `${API_BASE}${path}${buildQuery(opts.query)}`;
  const init: RequestInit = {
    method: opts.method ?? 'GET',
    headers: { 'Content-Type': 'application/json' },
  };
  if (opts.body !== undefined) {
    init.body = JSON.stringify(opts.body);
  }
  let resp: Response;
  try {
    resp = await fetch(url, init);
  } catch (e) {
    throw new ApiError(0, `network error: ${(e as Error).message}`);
  }
  let json: unknown = null;
  const text = await resp.text();
  if (text) {
    try {
      json = JSON.parse(text);
    } catch {
      // 非 JSON 响应
    }
  }
  if (!resp.ok) {
    const rawDetail =
      json && typeof json === 'object' && 'detail' in (json as Record<string, unknown>)
        ? (json as { detail: unknown }).detail
        : undefined;
    const detail = formatApiErrorDetail(rawDetail) || resp.statusText;
    throw new ApiError(resp.status, detail, json);
  }
  // 兼容 ResponseModel<T> 包装
  if (json && typeof json === 'object' && 'code' in (json as Record<string, unknown>) && 'data' in (json as Record<string, unknown>)) {
    const wrapped = json as ResponseModel<T>;
    if (wrapped.code !== 200) {
      throw new ApiError(resp.status, wrapped.message || 'unknown error', json);
    }
    return wrapped.data as T;
  }
  return json as T;
}

// ---------- System ----------

export const SystemApi = {
  health: () => http<{ status: string; allow_local_provision?: boolean }>('/health'),
  managerWsStatus: () => http<ManagerWsStatus>('/manager-ws/status'),
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
