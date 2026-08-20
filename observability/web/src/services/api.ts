export interface PromInstantResult {
  metric: Record<string, string>;
  value: [number, string];
}
export interface PromRangeResult {
  metric: Record<string, string>;
  values: Array<[number, string]>;
}
export interface PromInstantResponse {
  status: string;
  data: { resultType: string; result: PromInstantResult[] };
}
export interface PromRangeResponse {
  status: string;
  data: { resultType: string; result: PromRangeResult[] };
}

export class ApiError extends Error {
  constructor(public status: number, public detail: string, public raw?: unknown) {
    super(detail || `HTTP ${status}`);
    this.name = 'ApiError';
  }
}

function buildQuery(query?: Record<string, string | number | boolean | null | undefined>): string {
  if (!query) return '';
  const usp = new URLSearchParams();
  for (const [k, v] of Object.entries(query)) {
    if (v === undefined || v === null || v === '') continue;
    usp.append(k, String(v));
  }
  const s = usp.toString();
  return s ? `?${s}` : '';
}

async function httpProm<T>(
  path: string,
  query?: Record<string, string>,
): Promise<T> {
  const url = `/observability${path}${buildQuery(
    (query ?? {}) as Record<string, string | number | boolean | null | undefined>,
  )}`;
  let resp: Response;
  try {
    resp = await fetch(url, { headers: { 'Content-Type': 'application/json' } });
  } catch (e) {
    throw new ApiError(0, `network error: ${(e as Error).message}`);
  }
  const text = await resp.text();
  let json: unknown = null;
  if (text) {
    try {
      json = JSON.parse(text);
    } catch {
      // 非 JSON
    }
  }
  if (!resp.ok) {
    const detail =
      (json && typeof json === 'object' && 'error' in (json as Record<string, unknown>)
        ? String((json as { error: unknown }).error)
        : '') || resp.statusText;
    throw new ApiError(resp.status, detail, json);
  }
  return json as T;
}

export const PrometheusApi = {
  query: (promql: string) =>
    httpProm<PromInstantResponse>('/api/v1/query', { query: promql }),
  queryRange: (promql: string, start: number, end: number, step: string) =>
    httpProm<PromRangeResponse>('/api/v1/query_range', {
      query: promql,
      start: String(start),
      end: String(end),
      step,
    }),
};

// ---------------------------------------------------------------------------
// Loki
// ---------------------------------------------------------------------------

export interface LokiStreamValue {
  stream: Record<string, string>;
  values: [string, string][];  // [timestamp_ns, log_line]
}
export interface LokiQueryRangeResponse {
  status: string;
  data: {
    resultType: string;
    result: LokiStreamValue[];
  };
}

async function httpLoki<T>(
  path: string,
  query?: Record<string, string>,
): Promise<T> {
  const url = `/loki${path}${buildQuery(
    (query ?? {}) as Record<string, string | number | boolean | null | undefined>,
  )}`;
  let resp: Response;
  try {
    resp = await fetch(url, { headers: { 'Content-Type': 'application/json' } });
  } catch (e) {
    throw new ApiError(0, `network error: ${(e as Error).message}`);
  }
  const text = await resp.text();
  let json: unknown = null;
  if (text) {
    try {
      json = JSON.parse(text);
    } catch {
      // non-JSON
    }
  }
  if (!resp.ok) {
    const detail =
      (json && typeof json === 'object' && 'error' in (json as Record<string, unknown>)
        ? String((json as { error: unknown }).error)
        : '') || resp.statusText;
    throw new ApiError(resp.status, detail, json);
  }
  return json as T;
}

export interface AuditLogEntry {
  timestamp: number;  // ms
  auditType: string;
  traceId: string;
  requestId: string;
  sessionId: string;
  userId: string;
  botId: string;
  groupId: string;
  agentName: string;
  agentPod: string;
  body: string;
  details: Record<string, string>;
  raw: string;
}

export function parseLokiAuditStreams(resp: LokiQueryRangeResponse): AuditLogEntry[] {
  const entries: AuditLogEntry[] = [];
  for (const stream of resp.data?.result ?? []) {
    const labels = stream.stream ?? {};
    for (const [tsNs, line] of stream.values) {
      const ts = Math.floor(Number(tsNs) / 1e6);
      entries.push({
        timestamp: ts,
        auditType: String(labels['audit_type'] ?? ''),
        traceId: String(labels['trace_id'] ?? ''),
        requestId: String(labels['request_id'] ?? ''),
        sessionId: String(labels['session_id'] ?? ''),
        userId: String(labels['user_id'] ?? ''),
        botId: String(labels['bot_id'] ?? ''),
        groupId: String(labels['group_id'] ?? ''),
        agentName: String(labels['agent_name'] ?? ''),
        agentPod: String(labels['agent_pod'] ?? ''),
        body: line,
        details: Object.entries(labels)
          .filter(([k]) => k.startsWith('audit_'))
          .reduce<Record<string, string>>((acc, [k, v]) => {
            acc[k.replace('audit_', '')] = String(v);
            return acc;
          }, {}),
        raw: line,
      });
    }
  }
  entries.sort((a, b) => b.timestamp - a.timestamp);
  return entries;
}

export const LokiApi = {
  queryRange: (query: string, start: number, end: number, limit = 500) =>
    httpLoki<LokiQueryRangeResponse>('/api/v1/query_range', {
      query,
      start: String(start),
      end: String(end),
      limit: String(limit),
      direction: 'backward',
    }),
};

// ---------------------------------------------------------------------------
// Tempo
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// Tempo search API（/api/search 返回扁平结构）
// ---------------------------------------------------------------------------

export interface TempoSearchResult {
  traceID: string;
  rootServiceName: string;
  rootTraceName: string;
  startTimeUnixNano: string;  // Tempo 实际返回的是纳秒字符串
  durationMs: number;
  spanSet: { spans: { spanID: string; name: string; attributes?: Record<string, string>[] }[] };
}

export interface TempoSearchResponse {
  traces: TempoSearchResult[];
  metrics: { totalItems: number };
}

// ---------------------------------------------------------------------------
// Tempo trace detail API（/api/traces/{id} 返回 OTLP/JSON）
// 直接以 OTLP 结构为前端类型，不做 Jaeger 风格转换
// ---------------------------------------------------------------------------

export interface OtlpAnyValue {
  stringValue?: string;
  intValue?: string;
  boolValue?: boolean;
  doubleValue?: number;
  arrayValue?: { values: OtlpAnyValue[] };
}
export interface OtlpAttribute {
  key: string;
  value: OtlpAnyValue;
}
export interface OtlpEvent {
  name: string;
  timeUnixNano?: string;
  attributes?: OtlpAttribute[];
}
export interface OtlpSpan {
  traceId: string;        // base64
  spanId: string;         // base64
  parentSpanId?: string;  // base64
  name: string;
  kind?: string;
  startTimeUnixNano: string;  // 纳秒，字符串
  endTimeUnixNano: string;
  attributes?: OtlpAttribute[];
  events?: OtlpEvent[];
  status?: { code?: string; message?: string };
}
export interface OtlpBatch {
  resource?: { attributes?: OtlpAttribute[] };
  scopeSpans?: { scope?: { name?: string }; spans: OtlpSpan[] }[];
}
export interface TempoTraceResponse {
  batches: OtlpBatch[];
  /** Tempo 原始响应里没有顶层 traceID；这里用 URL 里的 hex 形式补上，方便 UI 显示。 */
  traceID: string;
}

// ---- 辅助函数：让 UI 不必每次手写 OTLP 取值逻辑 ---------------------------

export function otlpValueToString(v?: OtlpAnyValue): string {
  if (!v) return '';
  if (v.stringValue !== undefined) return v.stringValue;
  if (v.intValue !== undefined) return v.intValue;
  if (v.boolValue !== undefined) return String(v.boolValue);
  if (v.doubleValue !== undefined) return String(v.doubleValue);
  if (v.arrayValue?.values?.length) {
    return v.arrayValue.values.map((x) => otlpValueToString(x)).join(',');
  }
  return '';
}

export function spanStartMs(span: OtlpSpan): number {
  return span.startTimeUnixNano ? Number(span.startTimeUnixNano) / 1e6 : 0;
}
export function spanEndMs(span: OtlpSpan): number {
  return span.endTimeUnixNano ? Number(span.endTimeUnixNano) / 1e6 : spanStartMs(span);
}
export function spanDurationMs(span: OtlpSpan): number {
  return Math.max(0, spanEndMs(span) - spanStartMs(span));
}

export function spanAttrMap(span: OtlpSpan): Record<string, string> {
  const out: Record<string, string> = {};
  for (const a of span.attributes ?? []) {
    if (a && a.key) out[a.key] = otlpValueToString(a.value);
  }
  return out;
}

/** 把 batches[].scopeSpans[].spans[] 平铺成一维数组，方便 buildTree / Math.min 等。 */
export function flattenSpans(trace: { batches?: OtlpBatch[] }): OtlpSpan[] {
  const out: OtlpSpan[] = [];
  for (const b of trace.batches ?? []) {
    for (const ss of b.scopeSpans ?? []) {
      for (const sp of ss.spans ?? []) out.push(sp);
    }
  }
  return out;
}

async function httpTempo<T>(
  path: string,
  query?: Record<string, string>,
): Promise<T> {
  const url = `/tempo${path}${buildQuery(
    (query ?? {}) as Record<string, string | number | boolean | null | undefined>,
  )}`;
  let resp: Response;
  try {
    resp = await fetch(url, { headers: { 'Content-Type': 'application/json' } });
  } catch (e) {
    throw new ApiError(0, `network error: ${(e as Error).message}`);
  }
  const text = await resp.text();
  let json: unknown = null;
  if (text) {
    try {
      json = JSON.parse(text);
    } catch {
      // non-JSON
    }
  }
  if (!resp.ok) {
    const detail =
      (json && typeof json === 'object' && 'error' in (json as Record<string, unknown>)
        ? String((json as { error: unknown }).error)
        : '') || resp.statusText;
    throw new ApiError(resp.status, detail, json);
  }
  return json as T;
}

export const TempoApi = {
  /**
   * 列出 trace。
   * 时间窗口：默认 7 天（168h），这是 Tempo querier.max_search_duration 的默认上限。
   *   不传 start/end 会搜不到；传超过 max_search_duration 的窗口会报错。
   *   如果管理员调大了 Tempo 的 max_search_duration，这里也应跟着改。
   * limit：传足够大的值，让 Tempo 用 max_search_results 自己截断，前端不假设条数。
   * opts.query:  TraceQL 过滤表达式
   * opts.start:  起始时间（unix seconds）
   * opts.end:    结束时间（unix seconds）
   */
  searchTraces: (limit = 10000, opts?: { query?: string; start?: number; end?: number }) => {
    const defaultEnd = Math.floor(Date.now() / 1000);
    const defaultStart = defaultEnd - 168 * 3600;
    const start = opts?.start ?? defaultStart;
    const end = opts?.end ?? defaultEnd;
    return httpTempo<TempoSearchResponse>('/api/search', {
      limit: String(limit),
      start: String(start),
      end: String(end),
      ...(opts?.query ? { q: opts.query } : {}),
    });
  },
  /** 获取某个 tag 的所有可选值（用于自动补全）。 */
  tagValues: (tag: string) => {
    const end = Math.floor(Date.now() / 1000);
    const start = end - 168 * 3600;
    return httpTempo<{ tagValues?: string[] }>(`/api/search/tag/${encodeURIComponent(tag)}/values`, {
      start: String(start),
      end: String(end),
    });
  },
  /** 获取完整 trace（OTLP/JSON 原样返回，顶层补 traceID 给 UI 显示）。 */
  getTrace: async (traceId: string): Promise<TempoTraceResponse> => {
    const raw = await httpTempo<{ batches?: OtlpBatch[] }>(`/api/traces/${traceId}`, {
      format: 'json',
    });
    return { batches: raw?.batches ?? [], traceID: traceId };
  },
};

// ---------------------------------------------------------------------------
// Audit Rules API
// ---------------------------------------------------------------------------

export interface AuditRule {
  id?: number;
  detector: string;
  rule_name: string;
  pattern: string;
  severity: string;
  action: string;
  enabled: number | boolean;
  description: string;
  created_at?: string;
  updated_at?: string;
}

async function httpAudit<T>(path: string, options?: RequestInit): Promise<T> {
  const resp = await fetch(path, { headers: { 'Content-Type': 'application/json' }, ...options });
  const text = await resp.text();
  let json: unknown = null;
  if (text) {
    try { json = JSON.parse(text); } catch { /* non-JSON */ }
  }
  if (!resp.ok) {
    const detail = (json && typeof json === 'object' && 'detail' in (json as Record<string, unknown>))
      ? String((json as { detail: unknown }).detail) : resp.statusText;
    throw new ApiError(resp.status, detail, json);
  }
  return json as T;
}

export const AuditRulesApi = {
  list: (detector?: string) => {
    const q = detector ? `?detector=${detector}` : '';
    return httpAudit<AuditRule[]>(`/api/audit/rules${q}`);
  },

  create: (rule: AuditRule) =>
    httpAudit<AuditRule>('/api/audit/rules', {
      method: 'POST',
      body: JSON.stringify(rule),
    }),

  update: (id: number, rule: Partial<AuditRule>) =>
    httpAudit<AuditRule>(`/api/audit/rules/${id}`, {
      method: 'PUT',
      body: JSON.stringify(rule),
    }),

  remove: (id: number) =>
    httpAudit<{ ok: boolean }>(`/api/audit/rules/${id}`, { method: 'DELETE' }),

  test: async (pattern: string, text: string) => {
    try {
      const regex = new RegExp(pattern, 'i');
      const match = regex.exec(text);
      return { matched: !!match, match: match ? match[0] : null };
    } catch {
      return { matched: false, match: null, error: 'Invalid regex' };
    }
  },
};
