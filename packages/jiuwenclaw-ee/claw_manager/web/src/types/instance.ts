export interface InstanceSummary {
  jiuwenclaw_id: string;
  jiuwenclaw_name: string;
  status: string;
  k8s_namespace: string;
  group_id: string;
  space_id: string;
  created_at?: string | null;
}

export interface InstanceDetail extends InstanceSummary {
  description?: string | null;
  k8s_master_host: string;
  k8s_auth_type: string;
  resource_quota?: Record<string, unknown> | null;
  data?: Record<string, unknown> | null;
}

export interface CreateInstanceBody {
  jiuwenclaw_name: string;
  description?: string;
  k8s_master_host: string;
  k8s_auth_type: string;
  k8s_auth_config: Record<string, unknown>;
  k8s_namespace: string;
  resource_quota?: Record<string, unknown> | null;
  creator_id?: string;
  group_id?: string;
  space_id?: string;
  management_api_base?: string;
}

export interface ProvisionLocalInstanceBody {
  jiuwenclaw_name?: string;
  creator_id?: string;
  description?: string;
}

export interface ServiceStatusItem {
  service_id: string;
  service_type: string;
  component_role: string;
  status: string;
  last_heartbeat?: string | null;
  endpoint?: string | null;
  version?: string | null;
}

export interface ServiceStatusList {
  items: ServiceStatusItem[];
}

export interface ManagerWsStatus {
  enabled: boolean;
  running: boolean;
  host?: string;
  port?: number;
  registered_instances: string[];
  pid: number;
}
