export interface ChannelConfig {
  id: number;
  channel_id: string;
  channel_name: string;
  channel_type: string;
  bot_id: string;
  config?: Record<string, unknown> | null;
  status: string;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface ChannelRegisterBody {
  channel_id: string;
  channel_name: string;
  channel_type: string;
  bot_id: string;
  config?: Record<string, unknown>;
  status: string;
}

export interface LogMaskingRule {
  id: number;
  jiuwenclaw_id: string;
  rule_id: string;
  rule_name: string;
  description?: string | null;
  pattern: string;
  replacement: string;
  priority: number;
  source: string;
  enabled: boolean;
  data?: Record<string, unknown> | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface LogMaskingRuleCreateBody {
  rule_name: string;
  description?: string;
  pattern: string;
  replacement?: string;
  priority?: number;
  enabled?: boolean;
  data?: Record<string, unknown>;
}

export type LogMaskingRuleUpdateBody = Partial<LogMaskingRuleCreateBody>;

export type PermissionAction = 'allow' | 'ask' | 'deny';
export type PermissionRuleAction = 'allow' | 'deny';

export interface PermissionToolEntry {
  key: string;
  name: string;
  action: PermissionAction;
}

export interface PermissionRuleEntry {
  key: string;
  id: string;
  description: string;
  pattern: string;
  action: PermissionRuleAction;
}

export interface PermissionsFormState {
  enabled: boolean;
  defaults: PermissionAction;
  denyGuidanceMessage: string;
  tools: PermissionToolEntry[];
  rules: PermissionRuleEntry[];
  approvalOverrides: unknown[];
  ownerScopes: Record<string, unknown>;
  externalDirectory?: Record<string, unknown>;
  commandIntentEnabled: boolean;
  commandIntentTimeout: number;
  commandIntentExtraBody: Record<string, unknown>;
  fileGuardWorkspaceRwEnabled: boolean;
  fileGuardGlobalJson: string;
  fileGuardTrustedExecJson: string;
  fileGuardToolBindingsJson: string;
}

export interface PermissionsConfig {
  id?: number;
  jiuwenclaw_id: string;
  body: Record<string, unknown>;
  source?: string;
  revision?: number;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface ListItemsResult<T> {
  items: T[];
}

export type LogLevel = 'DEBUG' | 'INFO' | 'WARNING' | 'ERROR' | 'CRITICAL' | 'NOTSET';

export interface LoggingConfig {
  id?: number;
  jiuwenclaw_id: string;
  level: LogLevel;
  console_level?: LogLevel | null;
  gateway?: LogLevel | null;
  channel?: LogLevel | null;
  agent_server?: LogLevel | null;
  full?: LogLevel | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface LoggingConfigUpsertBody {
  level: LogLevel;
  console_level?: LogLevel | null;
  gateway?: LogLevel | null;
  channel?: LogLevel | null;
  agent_server?: LogLevel | null;
  full?: LogLevel | null;
}
