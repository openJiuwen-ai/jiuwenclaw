export interface ConfigDefaultTemplateMapping {
  id: number;
  jiuwenclaw_id: string;
  user_id?: string | null;
  group_id?: string | null;
  priority: number;
  template_id: string;
  template_type: string;
  enabled: boolean;
  data?: Record<string, unknown> | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface ConfigDefaultTemplateMappingCreateBody {
  user_id?: string;
  group_id?: string;
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
  priority: number;
  template_ref: Record<string, string>;
  enabled: boolean;
  data?: Record<string, unknown> | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface ConfigEffectiveGlobalPolicyCreateBody {
  priority?: number;
  template_ref: Record<string, string>;
  enabled?: boolean;
  data?: Record<string, unknown>;
}

export type ConfigEffectiveGlobalPolicyUpdateBody = Partial<ConfigEffectiveGlobalPolicyCreateBody>;

export interface ConfigEffectiveServicePolicy {
  id: number;
  jiuwenclaw_id: string;
  service_id: string;
  priority: number;
  match_expr?: string | null;
  template_ref: Record<string, string>;
  enabled: boolean;
  data?: Record<string, unknown> | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface ConfigEffectiveServicePolicyCreateBody {
  service_id: string;
  priority: number;
  match_expr?: string;
  template_ref: Record<string, string>;
  enabled?: boolean;
  data?: Record<string, unknown>;
}

export type ConfigEffectiveServicePolicyUpdateBody = Partial<ConfigEffectiveServicePolicyCreateBody>;

export interface ConfigEffectiveAgentPolicy {
  id: number;
  jiuwenclaw_id: string;
  agent_id: string;
  service_policy_id: number;
  priority: number;
  match_expr?: string | null;
  template_ref: Record<string, string>;
  enabled: boolean;
  data?: Record<string, unknown> | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface ConfigEffectiveAgentPolicyCreateBody {
  agent_id: string;
  service_policy_id: number;
  priority?: number;
  match_expr?: string;
  template_ref: Record<string, string>;
  enabled?: boolean;
  data?: Record<string, unknown>;
}

export type ConfigEffectiveAgentPolicyUpdateBody = Partial<ConfigEffectiveAgentPolicyCreateBody>;
