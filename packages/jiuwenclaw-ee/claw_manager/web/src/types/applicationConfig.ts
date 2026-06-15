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

export interface ListItemsResult<T> {
  items: T[];
}
