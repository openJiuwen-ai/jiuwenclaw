export type MappingScopeType = 'user' | 'group' | 'bot';

export interface ConfigDefaultTemplateMapping {
  id: number;
  jiuwenclaw_id: string;
  policy_id: string;
  policy_name: string;
  policy_desc?: string | null;
  scope_type: MappingScopeType;
  scope_id: string;
  priority: number;
  template_id: string;
  template_type: string;
  enabled: boolean;
  data?: Record<string, unknown> | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface ConfigDefaultTemplateMappingCreateBody {
  policy_name: string;
  policy_desc?: string;
  scope_type: MappingScopeType;
  scope_id: string;
  priority?: number;
  template_id: string;
  template_type: string;
  enabled?: boolean;
  data?: Record<string, unknown>;
}

export type ConfigDefaultTemplateMappingUpdateBody = Partial<ConfigDefaultTemplateMappingCreateBody>;

export interface ConfigEffectiveGlobalPolicy {
  id: number;
  jiuwenclaw_id: string;
  policy_id: string;
  policy_name: string;
  policy_desc?: string | null;
  priority: number;
  template_ref: Record<string, string[]>;
  enabled: boolean;
  data?: Record<string, unknown> | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface ConfigEffectiveGlobalPolicyCreateBody {
  policy_name: string;
  policy_desc?: string;
  priority?: number;
  template_ref: Record<string, string[]>;
  enabled?: boolean;
  data?: Record<string, unknown>;
}

export type ConfigEffectiveGlobalPolicyUpdateBody = Partial<ConfigEffectiveGlobalPolicyCreateBody>;

export interface ConfigEffectiveServicePolicy {
  id: number;
  jiuwenclaw_id: string;
  policy_id: string;
  policy_name: string;
  policy_desc?: string | null;
  service_id: string;
  priority: number;
  match_expr?: string | null;
  template_ref: Record<string, string[]>;
  enabled: boolean;
  data?: Record<string, unknown> | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface ConfigEffectiveServicePolicyCreateBody {
  policy_name: string;
  policy_desc?: string;
  service_id: string;
  priority: number;
  match_expr?: string;
  template_ref: Record<string, string[]>;
  enabled?: boolean;
  data?: Record<string, unknown>;
}

export type ConfigEffectiveServicePolicyUpdateBody = Partial<ConfigEffectiveServicePolicyCreateBody>;

export interface ConfigEffectiveAgentPolicy {
  id: number;
  jiuwenclaw_id: string;
  policy_id: string;
  policy_name: string;
  policy_desc?: string | null;
  agent_id: string;
  service_policy_id: string;
  priority: number;
  match_expr?: string | null;
  template_ref: Record<string, string[]>;
  send_file_allowed: boolean;
  enabled: boolean;
  data?: Record<string, unknown> | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface ConfigEffectiveAgentPolicyCreateBody {
  policy_name: string;
  policy_desc?: string;
  agent_id: string;
  service_policy_id: string;
  priority?: number;
  match_expr?: string;
  template_ref: Record<string, string[]>;
  send_file_allowed?: boolean;
  enabled?: boolean;
  data?: Record<string, unknown>;
}

export type ConfigEffectiveAgentPolicyUpdateBody = Partial<ConfigEffectiveAgentPolicyCreateBody>;
