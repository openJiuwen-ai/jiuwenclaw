export interface ContextUsagePart {
  category: string;
  tokens: number;
  percentage_of_window: number | null;
}

/** Fields consumed from the context-usage.v1 post-call event. Ratios are not percentages. */
export interface ContextUsageSnapshot {
  event_type: 'context.usage';
  schema_version: 'context-usage.v1';
  phase: 'post_call';
  request_id: string;
  product_session_id: string;
  depth: number;
  team_id: string | null;
  member_name: string | null;
  context_window: {
    limit_tokens: number | null;
    input_tokens: number | null;
    occupancy_rate: number | null;
  };
  parts: Record<string, ContextUsagePart>;
  session_kv_cache_hit_rate: number | null;
}
