import type {
  ConfigDefaultTemplateMapping,
  ConfigDefaultTemplateMappingCreateBody,
  ConfigDefaultTemplateMappingUpdateBody,
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
  ProvisionLocalInstanceBody,
  ResponseModel,
  ServiceStatusList,
} from '../types';

const API_BASE = (import.meta.env.VITE_API_BASE ?? '/api').replace(/\/$/, '');

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
    const detail =
      (json && typeof json === 'object' && 'detail' in (json as Record<string, unknown>)
        ? String((json as { detail: unknown }).detail)
        : '') || resp.statusText;
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
  health: () => http<{ status: string }>('/health'),
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
  list: (params?: { page?: number; page_size?: number; status?: string }) =>
    http<InstancePageRaw>('/v1/instances', { query: params }),
  get: (id: string) => http<InstanceDetail>(`/v1/instances/${encodeURIComponent(id)}`),
  create: (body: CreateInstanceBody) => http<InstanceSummary>('/v1/instances', { method: 'POST', body }),
  provisionLocal: (body: ProvisionLocalInstanceBody) =>
    http<Record<string, unknown>>('/v1/instances/provision-local', { method: 'POST', body }),
  patch: (id: string, data: Record<string, unknown>) =>
    http<InstanceDetail>(`/v1/instances/${encodeURIComponent(id)}`, { method: 'PATCH', body: { data } }),
  remove: (id: string, force = false) =>
    http<{ deleted: boolean }>(`/v1/instances/${encodeURIComponent(id)}`, {
      method: 'DELETE',
      query: { force },
    }),
  servicesStatus: (id: string) =>
    http<ServiceStatusList>(`/v1/instances/${encodeURIComponent(id)}/services/status`),
};

// ---------- Templates ----------

export const ModelTemplateApi = {
  list: (params?: { page?: number; page_size?: number; enabled?: boolean; model_type?: string }) =>
    http<PageResult<ModelTemplate>>('/v1/model-templates', { query: params }),
  get: (id: string) => http<ModelTemplate>(`/v1/model-templates/${encodeURIComponent(id)}`),
  create: (body: ModelTemplateCreateBody) =>
    http<ModelTemplate>('/v1/model-templates', { method: 'POST', body }),
  update: (id: string, body: ModelTemplateUpdateBody) =>
    http<ModelTemplate>(`/v1/model-templates/${encodeURIComponent(id)}`, { method: 'PUT', body }),
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
  }) => http<PageResult<ExtensionConfigTemplate>>('/v1/extension-config-templates', { query: params }),
  get: (id: string) =>
    http<ExtensionConfigTemplate>(`/v1/extension-config-templates/${encodeURIComponent(id)}`),
  create: (body: ExtensionConfigTemplateCreateBody) =>
    http<ExtensionConfigTemplate>('/v1/extension-config-templates', { method: 'POST', body }),
  update: (id: string, body: ExtensionConfigTemplateUpdateBody) =>
    http<ExtensionConfigTemplate>(`/v1/extension-config-templates/${encodeURIComponent(id)}`, {
      method: 'PUT',
      body,
    }),
  remove: (id: string) =>
    http<{ deleted: boolean; template_id: string }>(
      `/v1/extension-config-templates/${encodeURIComponent(id)}`,
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
      user_id?: string;
      group_id?: string;
      template_type?: string;
      template_id?: string;
      enabled?: boolean;
    }
  ) =>
    http<PageResult<ConfigDefaultTemplateMapping>>(
      `${policyBase(instanceId)}/config-default-template-mappings`,
      { query: params }
    ),
  create: (instanceId: string, body: ConfigDefaultTemplateMappingCreateBody) =>
    http<ConfigDefaultTemplateMapping>(
      `${policyBase(instanceId)}/config-default-template-mappings`,
      { method: 'POST', body }
    ),
  update: (instanceId: string, mappingId: number, body: ConfigDefaultTemplateMappingUpdateBody) =>
    http<ConfigDefaultTemplateMapping>(
      `${policyBase(instanceId)}/config-default-template-mappings/${mappingId}`,
      { method: 'PUT', body }
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
    params?: { page?: number; page_size?: number; enabled?: boolean }
  ) =>
    http<PageResult<ConfigEffectiveGlobalPolicy>>(
      `${policyBase(instanceId)}/config-effective/global-policies`,
      { query: params }
    ),
  create: (instanceId: string, body: ConfigEffectiveGlobalPolicyCreateBody) =>
    http<ConfigEffectiveGlobalPolicy>(
      `${policyBase(instanceId)}/config-effective/global-policies`,
      { method: 'POST', body }
    ),
  update: (instanceId: string, policyId: number, body: ConfigEffectiveGlobalPolicyUpdateBody) =>
    http<ConfigEffectiveGlobalPolicy>(
      `${policyBase(instanceId)}/config-effective/global-policies/${policyId}`,
      { method: 'PUT', body }
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
    params?: { page?: number; page_size?: number; enabled?: boolean }
  ) =>
    http<PageResult<ConfigEffectiveServicePolicy>>(
      `${policyBase(instanceId)}/config-effective/service-policies`,
      { query: params }
    ),
  create: (instanceId: string, body: ConfigEffectiveServicePolicyCreateBody) =>
    http<ConfigEffectiveServicePolicy>(
      `${policyBase(instanceId)}/config-effective/service-policies`,
      { method: 'POST', body }
    ),
  update: (instanceId: string, policyId: number, body: ConfigEffectiveServicePolicyUpdateBody) =>
    http<ConfigEffectiveServicePolicy>(
      `${policyBase(instanceId)}/config-effective/service-policies/${policyId}`,
      { method: 'PUT', body }
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
    params?: { page?: number; page_size?: number; service_policy_id?: number; enabled?: boolean }
  ) =>
    http<PageResult<ConfigEffectiveAgentPolicy>>(
      `${policyBase(instanceId)}/config-effective/agent-policies`,
      { query: params }
    ),
  create: (instanceId: string, body: ConfigEffectiveAgentPolicyCreateBody) =>
    http<ConfigEffectiveAgentPolicy>(
      `${policyBase(instanceId)}/config-effective/agent-policies`,
      { method: 'POST', body }
    ),
  update: (instanceId: string, policyId: number, body: ConfigEffectiveAgentPolicyUpdateBody) =>
    http<ConfigEffectiveAgentPolicy>(
      `${policyBase(instanceId)}/config-effective/agent-policies/${policyId}`,
      { method: 'PUT', body }
    ),
  remove: (instanceId: string, policyId: number) =>
    http<{ deleted: boolean; id: number }>(
      `${policyBase(instanceId)}/config-effective/agent-policies/${policyId}`,
      { method: 'DELETE' }
    ),
};
