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
