export type ModelTypeValue = string | string[];

export interface ModelTemplate {
  id: number;
  template_id: string;
  template_name: string;
  description?: string | null;
  model_type: ModelTypeValue;
  model_tags?: string[] | null;
  api_base: string;
  api_key: string;
  model_id: string;
  model_provider: string;
  parameters?: Record<string, unknown> | null;
  timeout: number;
  retry_count: number;
  enable_streaming: boolean;
  enable_function_calling: boolean;
  verify_ssl: boolean;
  enabled: boolean;
  data?: Record<string, unknown> | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface ModelTemplateCreateBody {
  template_name: string;
  description?: string;
  model_type: ModelTypeValue;
  model_tags?: string[];
  api_base: string;
  api_key: string;
  model_id: string;
  model_provider: string;
  parameters?: Record<string, unknown>;
  timeout?: number;
  retry_count?: number;
  enable_streaming?: boolean;
  enable_function_calling?: boolean;
  verify_ssl?: boolean;
  enabled?: boolean;
  data?: Record<string, unknown>;
}

export type ModelTemplateUpdateBody = Partial<ModelTemplateCreateBody>;

export interface ExtensionConfigTemplate {
  id: number;
  template_id: string;
  template_name: string;
  description?: string | null;
  component: string;
  hook_type: string;
  hook_config: Record<string, unknown>;
  custom_config?: Record<string, unknown> | null;
  enabled: boolean;
  data?: Record<string, unknown> | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface ExtensionConfigTemplateCreateBody {
  template_name: string;
  description?: string;
  component: string;
  hook_type: string;
  hook_config: Record<string, unknown>;
  custom_config?: Record<string, unknown>;
  enabled?: boolean;
  data?: Record<string, unknown>;
}

export type ExtensionConfigTemplateUpdateBody = Partial<ExtensionConfigTemplateCreateBody>;

export interface SkillWhitelistTemplate {
  id: number;
  template_id: string;
  template_name: string;
  description?: string | null;
  skill_id: string;
  skill_version: string;
  skill_source: string;
  enabled: boolean;
  data?: Record<string, unknown> | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface SkillWhitelistTemplateCreateBody {
  template_name: string;
  description?: string;
  skill_id: string;
  skill_version: string;
  skill_source: string;
  enabled?: boolean;
  data?: Record<string, unknown>;
}

export type SkillWhitelistTemplateUpdateBody = Partial<SkillWhitelistTemplateCreateBody>;

export interface ServiceConfigTemplate {
  id: number;
  template_id: string;
  template_name: string;
  description?: string | null;
  agent_image: string;
  namespace: string;
  pod_name?: string | null;
  container_name: string;
  container_port: number;
  port_name: string;
  image_pull_policy: string;
  replicas: number;
  kubeconfig?: string | null;
  agent_runtime?: string | null;
  readiness_initial_delay: number;
  readiness_period: number;
  ready_timeout: number;
  ready_poll_interval: number;
  nfs_server?: string | null;
  nfs_path: string;
  nfs_mount_path?: string | null;
  agent_cpu_request?: string | null;
  agent_memory_request?: string | null;
  agent_cpu_limit?: string | null;
  agent_memory_limit?: string | null;
  jiuwenbox_cpu_request?: string | null;
  jiuwenbox_memory_request?: string | null;
  jiuwenbox_cpu_limit?: string | null;
  jiuwenbox_memory_limit?: string | null;
  min_idle_services: number;
  max_services: number;
  service_concurrency: number;
  service_ttl: number;
  autoscale_interval: number;
  message_timeout: number;
  session_concurrency: number;
  session_ttl: number;
  enabled: boolean;
  data?: Record<string, unknown> | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface ServiceConfigTemplateCreateBody {
  template_name: string;
  description?: string;
  agent_image: string;
  namespace: string;
  pod_name?: string;
  container_name: string;
  container_port: number;
  port_name?: string;
  image_pull_policy?: string;
  replicas?: number;
  kubeconfig?: string;
  agent_runtime?: string;
  readiness_initial_delay?: number;
  readiness_period?: number;
  ready_timeout?: number;
  ready_poll_interval?: number;
  nfs_server?: string;
  nfs_path?: string;
  nfs_mount_path?: string;
  agent_cpu_request?: string;
  agent_memory_request?: string;
  agent_cpu_limit?: string;
  agent_memory_limit?: string;
  jiuwenbox_cpu_request?: string;
  jiuwenbox_memory_request?: string;
  jiuwenbox_cpu_limit?: string;
  jiuwenbox_memory_limit?: string;
  min_idle_services?: number;
  max_services?: number;
  service_concurrency?: number;
  service_ttl?: number;
  autoscale_interval?: number;
  message_timeout?: number;
  session_concurrency?: number;
  session_ttl?: number;
  enabled?: boolean;
  data?: Record<string, unknown>;
}

export type ServiceConfigTemplateUpdateBody = Partial<ServiceConfigTemplateCreateBody>;
