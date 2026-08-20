import { useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useRouter } from '../../router';
import {
  ApiError,
  TempoApi,
  TempoSearchResult,
  OtlpSpan,
  TempoTraceResponse,
  spanAttrMap,
  spanStartMs,
  spanDurationMs,
  flattenSpans,
} from '../../services/api';


// ---- helpers --------------------------------------------------------------

function parentOf(span: OtlpSpan): string {
  return span.parentSpanId ?? '';
}

// TraceID 格式：32 位 hex
const TRACE_ID_RE = /^[0-9a-f]{32}$/i;
function isTraceId(s: string): boolean {
  return TRACE_ID_RE.test(s);
}

// 模糊解析时间输入，返回时间范围。
// 输入精度越低，范围越宽：
//   "2026"          → 2026-01-01 00:00:00 ~ 2026-12-31 23:59:59
//   "2026-08"       → 2026-08-01 00:00:00 ~ 2026-08-31 23:59:59
//   "2026-08-12"    → 2026-08-12 00:00:00 ~ 2026-08-12 23:59:59
//   "2026-08-12 19" → 19:00:00 ~ 19:59:59
//   "2026-08-12 19:30"    → 19:30:00 ~ 19:30:59
//   "2026-08-12 19:30:45" → 精确到那一秒
function parseTimeRange(input: string): { start?: number; end?: number } {
  const trimmed = input.trim();
  if (!trimmed) return {};
  const parts = trimmed.split(/[ T]/);
  const dateStr = parts[0];
  const timeStr = parts.slice(1).join(' ') || '';
  const dateParts = dateStr.split('-').map(Number);
  if (!dateParts[0]) return {};
  const year = dateParts[0];
  const month = dateParts[1];
  const day = dateParts[2];
  let hour: number | undefined, minute: number | undefined, second: number | undefined;
  if (timeStr) {
    const tp = timeStr.split(':').map(Number);
    hour = tp[0]; minute = tp[1]; second = tp[2];
  }
  const start = new Date(year, (month ?? 1) - 1, day ?? 1, hour ?? 0, minute ?? 0, second ?? 0);
  let end: Date;
  if (second !== undefined) end = new Date(start);
  else if (minute !== undefined) end = new Date(year, (month ?? 1) - 1, day ?? 1, hour!, minute, 59);
  else if (hour !== undefined) end = new Date(year, (month ?? 1) - 1, day ?? 1, hour, 59, 59);
  else if (day !== undefined) end = new Date(year, (month ?? 1) - 1, day, 23, 59, 59);
  else if (month !== undefined) end = new Date(year, month, 0, 23, 59, 59); // day=0 = 上月末
  else end = new Date(year, 11, 31, 23, 59, 59);
  return { start: Math.floor(start.getTime() / 1000), end: Math.floor(end.getTime() / 1000) + 1 };
}

// ---- 时间选择器（年/月/日/时/分/秒，每级可选可不选）----

interface TimeParts { year: string; month: string; day: string; hour: string; minute: string; second: string }

function parseTimeStr(s: string): TimeParts {
  const parts = (s || '').split(/[ T]/);
  const dp = (parts[0] || '').split('-');
  const tp = (parts[1] || '').split(':');
  return {
    year: dp[0] || '', month: dp[1] || '', day: dp[2] || '',
    hour: tp[0] || '', minute: tp[1] || '', second: tp[2] || '',
  };
}
function buildTimeStr(p: TimeParts): string {
  const date = [p.year, p.month, p.day].filter(Boolean).join('-');
  const time = [p.hour, p.minute, p.second].filter(Boolean).join(':');
  return [date, time].filter(Boolean).join(' ');
}

function pad2(n: number): string { return String(n).padStart(2, '0'); }

function TimeSelectGroup({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const p = parseTimeStr(value);
  const upd = (key: keyof TimeParts, v: string) => {
    // 选了新年/月时，如果旧的月/日不在新范围内，清空
    const next = { ...p, [key]: v };
    if (key === 'year') { next.month = ''; next.day = ''; }
    if (key === 'month') { next.day = ''; }
    onChange(buildTimeStr(next));
  };
  // 7 天范围
  const now = new Date();
  const ago = new Date(Date.now() - 7 * 24 * 3600 * 1000);
  // 年：7 天范围内的年份
  const yearOpts: number[] = [];
  for (let y = ago.getFullYear(); y <= now.getFullYear(); y++) yearOpts.push(y);
  // 月：选了年后，该年内在 7 天范围内的月份
  const monthOpts: number[] = [];
  if (p.year) {
    const y = Number(p.year);
    for (let m = 0; m < 12; m++) {
      const ms = new Date(y, m, 1);
      const me = new Date(y, m + 1, 0, 23, 59, 59);
      if (me >= ago && ms <= now) monthOpts.push(m + 1);
    }
  }
  // 日：选了年月后，该月内在 7 天范围内的日期
  const dayOpts: number[] = [];
  if (p.year && p.month) {
    const y = Number(p.year);
    const m = Number(p.month) - 1;
    const dim = new Date(y, m + 1, 0).getDate();
    for (let d = 1; d <= dim; d++) {
      const ds = new Date(y, m, d, 0, 0, 0);
      const de = new Date(y, m, d, 23, 59, 59);
      if (de >= ago && ds <= now) dayOpts.push(d);
    }
  }
  return (
    <span className="flex items-center gap-1 flex-wrap">
      <select className="input" style={{ width: '80px' }} value={p.year} onChange={(e) => upd('year', e.target.value)}>
        <option value="">年</option>
        {yearOpts.map((y) => <option key={y} value={String(y)}>{y}</option>)}
      </select>
      <select className="input" style={{ width: '60px' }} value={p.month} onChange={(e) => upd('month', e.target.value)} disabled={!p.year}>
        <option value="">月</option>
        {monthOpts.map((m) => <option key={m} value={pad2(m)}>{m}</option>)}
      </select>
      <select className="input" style={{ width: '60px' }} value={p.day} onChange={(e) => upd('day', e.target.value)} disabled={!p.month}>
        <option value="">日</option>
        {dayOpts.map((d) => <option key={d} value={pad2(d)}>{d}</option>)}
      </select>
      <select className="input" style={{ width: '60px' }} value={p.hour} onChange={(e) => upd('hour', e.target.value)} disabled={!p.day}>
        <option value="">时</option>
        {Array.from({ length: 24 }, (_, i) => i).map((h) => <option key={h} value={pad2(h)}>{h}</option>)}
      </select>
      <span className="text-muted">:</span>
      <select className="input" style={{ width: '60px' }} value={p.minute} onChange={(e) => upd('minute', e.target.value)} disabled={!p.hour}>
        <option value="">分</option>
        {Array.from({ length: 60 }, (_, i) => i).map((m) => <option key={m} value={pad2(m)}>{m}</option>)}
      </select>
      <span className="text-muted">:</span>
      <select className="input" style={{ width: '60px' }} value={p.second} onChange={(e) => upd('second', e.target.value)} disabled={!p.minute}>
        <option value="">秒</option>
        {Array.from({ length: 60 }, (_, i) => i).map((s) => <option key={s} value={pad2(s)}>{s}</option>)}
      </select>
    </span>
  );
}

// 从 trace 详情构造单条 search 结果（用于按 TraceID 搜索时显示在列表里）
function traceResponseToSearchResult(resp: TempoTraceResponse): TempoSearchResult {
  const spans = flattenSpans(resp);
  const root = spans.find((s) => !s.parentSpanId) ?? spans[0];
  const starts = spans.map((s) => Number(s.startTimeUnixNano)).filter((n) => n > 0);
  const ends = spans.map((s) => Number(s.endTimeUnixNano)).filter((n) => n > 0);
  const startMin = starts.length ? Math.min(...starts) : 0;
  const endMax = ends.length ? Math.max(...ends) : startMin;
  let serviceName = '';
  for (const b of resp.batches ?? []) {
    for (const a of b.resource?.attributes ?? []) {
      if (a.key === 'service.name' && a.value?.stringValue) {
        serviceName = a.value.stringValue;
        break;
      }
    }
    if (serviceName) break;
  }
  return {
    traceID: resp.traceID,
    rootServiceName: serviceName,
    rootTraceName: root?.name ?? '',
    startTimeUnixNano: String(startMin),
    durationMs: startMin ? Math.round((endMax - startMin) / 1e6) : 0,
    spanSet: { spans: [] },
  };
}

function extractRequestId(result: TempoSearchResult): string {
  const spans = result.spanSet?.spans ?? [];
  for (const s of spans) {
    const attrs = s.attributes;
    if (!Array.isArray(attrs)) continue;
    for (const a of attrs) {
      if (a && a.key === 'jiuwenclaw.request.id') return a.value ?? '';
    }
  }
  return '';
}

function formatMs(ms: number): string {
  if (ms < 1) return '<1ms';
  if (ms < 1000) return `${Math.round(ms)}ms`;
  if (ms < 60000) return `${(ms / 1000).toFixed(2)}s`;
  return `${Math.floor(ms / 60000)}m${Math.round((ms % 60000) / 1000)}s`;
}

function formatTime(unixMs: number): string {
  return new Date(unixMs).toLocaleString([], {
    month: 'numeric',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
}

// 上万显示"万",上亿显示"亿"
function formatTokenCount(n: number): string {
  if (!Number.isFinite(n) || n <= 0) return '0';
  if (n >= 1e8) return `${(n / 1e8).toFixed(2)}亿`;
  if (n >= 1e4) return `${(n / 1e4).toFixed(2)}万`;
  return String(n);
}

// ---- span type styling ----------------------------------------------------

type SpanCategory = 'root' | 'gateway' | 'agent' | 'llm' | 'tool' | 'other';

function categorize(span: OtlpSpan): SpanCategory {
  const name = span.name;
  if (name === 'channel.request') return 'root';
  if (name.startsWith('jiuwenclaw.gateway')) return 'gateway';
  if (name.startsWith('jiuwenclaw.agent.invoke')) return 'agent';
  if (name === 'gen_ai.chat') return 'llm';
  if (name.startsWith('gen_ai.tool.execute')) return 'tool';
  return 'other';
}

const SPAN_STYLE: Record<SpanCategory, { color: string; label: string }> = {
  root: { color: '#6b7280', label: 'Root' },
  gateway: { color: '#3b82f6', label: 'Gateway' },
  agent: { color: '#10b981', label: 'Agent' },
  llm: { color: '#f59e0b', label: 'LLM' },
  tool: { color: '#a855f7', label: 'Tool' },
  other: { color: '#6366f1', label: 'Span' },
};

// ---- tree builder ---------------------------------------------------------

interface TreeNode {
  span: OtlpSpan;
  children: TreeNode[];
  depth: number;
}

function buildTree(spans: OtlpSpan[]): TreeNode[] {
  const byId = new Map<string, OtlpSpan>();
  for (const s of spans) byId.set(s.spanId, s);

  const childrenMap = new Map<string, OtlpSpan[]>();
  const roots: OtlpSpan[] = [];
  for (const s of spans) {
    const pid = parentOf(s);
    if (!pid || !byId.has(pid)) {
      roots.push(s);
    } else {
      const arr = childrenMap.get(pid) ?? [];
      arr.push(s);
      childrenMap.set(pid, arr);
    }
  }

  const sortChildren = (arr: OtlpSpan[]) =>
    arr.sort((a, b) => spanStartMs(a) - spanStartMs(b));

  const build = (span: OtlpSpan, depth: number): TreeNode => ({
    span,
    depth,
    children: sortChildren(childrenMap.get(span.spanId) ?? []).map((c) => build(c, depth + 1)),
  });

  return sortChildren(roots).map((r) => build(r, 0));
}

// ---- span row renderer ----------------------------------------------------

function SpanRow({ node, traceStartMs, traceDurationMs }: { node: TreeNode; traceStartMs: number; traceDurationMs: number }) {
  const { t } = useTranslation();
  const { span, depth } = node;
  const cat = categorize(span);
  const style = SPAN_STYLE[cat];
  const tags = useMemo(() => spanAttrMap(span), [span]);
  const spanStart = spanStartMs(span);
  const spanDur = spanDurationMs(span);
  const offsetPct = traceDurationMs > 0 ? ((spanStart - traceStartMs) / traceDurationMs) * 100 : 0;
  const widthPct = traceDurationMs > 0 ? Math.max(2, (spanDur / traceDurationMs) * 100) : 100;

  return (
    <>
      <div className="flex items-start gap-2 py-1.5" style={{ paddingLeft: `${depth * 20}px` }}>
        <span
          className="inline-block w-2 h-2 rounded-full mt-1.5 flex-shrink-0"
          style={{ background: style.color }}
        />
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-sm font-medium mono truncate">{span.name}</span>
            <span className="text-xs px-1.5 py-0.5 rounded" style={{ background: `${style.color}22`, color: style.color }}>
              {style.label}
            </span>
            <span className="text-xs num text-muted">{formatMs(spanDur)}</span>
          </div>
          <div className="flex items-center gap-3 flex-wrap text-xs text-muted mt-0.5">
            {tags['gen_ai.request.model'] && (
              <span>{t('trace.model')}: <span className="mono">{tags['gen_ai.request.model']}</span></span>
            )}
            {tags['gen_ai.tool.name'] && (
              <span>{t('trace.toolName')}: <span className="mono">{tags['gen_ai.tool.name']}</span></span>
            )}
            {tags['jiuwenclaw.user.id'] && (
              <span>user: <span className="mono">{tags['jiuwenclaw.user.id']}</span></span>
            )}
            {tags['jiuwenclaw.group.id'] && (
              <span>group: <span className="mono">{tags['jiuwenclaw.group.id']}</span></span>
            )}
            {tags['jiuwenclaw.bot.id'] && (
              <span>bot: <span className="mono">{tags['jiuwenclaw.bot.id']}</span></span>
            )}
            {tags['jiuwenclaw.iteration'] && (
              <span>{t('trace.iteration')}: {tags['jiuwenclaw.iteration']}</span>
            )}
            {tags['gen_ai.usage.input_tokens'] && (
              <span className="num">Input Tokens: {formatTokenCount(Number(tags['gen_ai.usage.input_tokens']))}</span>
            )}
            {tags['gen_ai.usage.output_tokens'] && (
              <span className="num">Output Tokens: {formatTokenCount(Number(tags['gen_ai.usage.output_tokens']))}</span>
            )}
          </div>
        </div>
      </div>
      {/* mini waterfall bar */}
      <div
        className="h-1 rounded-full mb-1"
        style={{
          marginLeft: `${depth * 20}px`,
          width: `calc(100% - ${depth * 20}px)`,
        }}
      >
        <div className="relative h-full rounded-full" style={{ background: 'var(--bg-muted)' }}>
          <div
            className="absolute h-full rounded-full"
            style={{
              left: `${Math.min(98, offsetPct)}%`,
              width: `${Math.min(widthPct, 100 - offsetPct)}%`,
              background: style.color,
              opacity: 0.7,
            }}
          />
        </div>
      </div>
      {node.children.map((child) => (
        <SpanRow key={child.span.spanId} node={child} traceStartMs={traceStartMs} traceDurationMs={traceDurationMs} />
      ))}
    </>
  );
}

// ---- main component -------------------------------------------------------

type SortField = 'traceID' | 'userId' | 'botId' | 'groupId' | 'operation' | 'startTime' | 'duration' | 'tokens';
type SortDir = 'asc' | 'desc' | null;

// 不 hard code Tempo 的 max_search_results 限制，传足够大的值让 Tempo 自己截断。
// Tempo 默认 max_search_results=500，但管理员可能调大，前端不应假设。
const ALL_TRACES_LIMIT = 10000;

// 列头 + 数据行共用的 grid 列宽模板
const GRID_TEMPLATE = 'minmax(180px,2fr) 70px 70px 70px minmax(120px,1.5fr) minmax(140px,1.5fr) 90px 90px';

// 列头定义（顺序 = 渲染顺序）。TraceID 和操作列不参与排序。
const SORT_FIELDS: { key: SortField; label: string; sortable: boolean }[] = [
  { key: 'traceID', label: 'TraceID', sortable: false },
  { key: 'userId', label: 'UserID', sortable: true },
  { key: 'botId', label: 'BotID', sortable: true },
  { key: 'groupId', label: 'GroupID', sortable: true },
  { key: 'operation', label: '操作', sortable: false },
  { key: 'startTime', label: '开始时间', sortable: true },
  { key: 'duration', label: '总耗时', sortable: true },
  { key: 'tokens', label: 'Tokens', sortable: true },
];

const FIELD_LABELS: Record<SortField, string> = {
  traceID: 'TraceID',
  userId: 'UserID',
  botId: 'BotID',
  groupId: 'GroupID',
  operation: '操作',
  startTime: '开始时间',
  duration: '总耗时',
  tokens: 'Tokens',
};

// ---- 筛选条件 ----

type FilterKey = 'traceID' | 'user_id' | 'bot_id' | 'group_id' | 'start_time';
interface FilterItem {
  key: FilterKey | '';
  value: string;      // traceID/user_id/bot_id/group_id 的值；start_time 的"从"
  valueTo?: string;   // start_time 的"到"
}
const FILTER_KEY_OPTIONS: { key: FilterKey; label: string }[] = [
  { key: 'traceID', label: 'TraceID' },
  { key: 'user_id', label: 'UserID' },
  { key: 'bot_id', label: 'BotID' },
  { key: 'group_id', label: 'GroupID' },
  { key: 'start_time', label: '开始时间' },
];
// 用于自动补全的 Tempo tag 名（start_time 用日历，不需要补全）
const TAG_FOR_FILTER: Partial<Record<FilterKey, string>> = {
  user_id: 'jiuwenclaw.user.id',
  bot_id: 'jiuwenclaw.bot.id',
  group_id: 'jiuwenclaw.group.id',
};

export function TraceTab() {
  const { t } = useTranslation();
  const { params: routerParams } = useRouter();
  // 筛选条件数组，每行一个 { key, value }，AND 关系
  const [filters, setFilters] = useState<FilterItem[]>([{ key: '', value: '' }]);
  // tag 自动补全值缓存（key = FilterKey, value = 可选值列表）
  const [tagOptions, setTagOptions] = useState<Partial<Record<FilterKey, string[]>>>({});
  const tagLoadingRef = useRef<Set<string>>(new Set());

  const [results, setResults] = useState<TempoSearchResult[] | null>(null);
  const [resultsLoading, setResultsLoading] = useState(true);
  const [resultsError, setResultsError] = useState<string | null>(null);

  const [activeTrace, setActiveTrace] = useState<TempoTraceResponse | null>(null);
  const [traceLoading, setTraceLoading] = useState(false);
  const [traceError, setTraceError] = useState<string | null>(null);
  const [expandedTraceId, setExpandedTraceId] = useState<string | null>(null);
  const pendingTraceIdRef = useRef<string | null>(null);
  const traceCacheRef = useRef<Map<string, TempoTraceResponse>>(new Map());
  const [tokenByTraceId, setTokenByTraceId] = useState<Record<string, number>>({});
  const [routingByTraceId, setRoutingByTraceId] = useState<
    Record<string, { userId?: string; botId?: string; groupId?: string }>
  >({});

  // 排序状态：null = 默认（最近20条按时间倒序）；非 null = 按该字段排序取前20
  const [sortField, setSortField] = useState<SortField | null>(null);
  const [sortDir, setSortDir] = useState<SortDir>(null);
  const [sortLoading, setSortLoading] = useState(false);

  // 有筛选条件时禁用排序
  const activeFilters = filters.filter((f) => f.key && f.value);
  const isSearching = activeFilters.length > 0;

  // 从 URL 读取 traceId（从审核日志跳转过来时携带）
  const urlTraceId = routerParams.get('traceId');
  useEffect(() => {
    if (urlTraceId && isTraceId(urlTraceId)) {
      setFilters([{ key: 'traceID', value: urlTraceId }]);
    }
  }, [urlTraceId]);

  // tag 自动补全：用户选了某个 key（user_id/bot_id/group_id）时加载可选值
  const loadTagOptions = (key: FilterKey) => {
    const tag = TAG_FOR_FILTER[key];
    if (!tag || tagLoadingRef.current.has(key) || tagOptions[key]) return;
    tagLoadingRef.current.add(key);
    TempoApi.tagValues(tag)
      .then((resp) => {
        setTagOptions((prev) => ({ ...prev, [key]: resp.tagValues ?? [] }));
      })
      .catch(() => {})
      .finally(() => { tagLoadingRef.current.delete(key); });
  };

  // 筛选条件操作
  const addFilter = () => setFilters((prev) => [...prev, { key: '', value: '' }]);
  const removeFilter = (idx: number) => setFilters((prev) => {
    const next = prev.filter((_, i) => i !== idx);
    // 至少保留一个空行
    return next.length === 0 ? [{ key: '', value: '' }] : next;
  });
  const updateFilter = (idx: number, patch: Partial<FilterItem>) => {
    setFilters((prev) => prev.map((f, i) => {
      if (i !== idx) return f;
      const next = { ...f, ...patch };
      // 选了 TraceID 就清空其他条件（TraceID 是唯一的）
      if (patch.key === 'traceID') {
        return { key: 'traceID', value: next.value };
      }
      // 已有其他条件时选 TraceID 不应该发生（UI 层禁用），但防御性清空
      return next;
    }));
  };

  // search / list recent
  // - 筛选条件含 traceID 且值是 32 位 hex：直接 getTrace
  // - 其他筛选条件：构造 TraceQL + 时间范围，调 searchTraces
  // - 无筛选：拉最近 20 条（有排序时拉 500 条）
  useEffect(() => {
    let cancelled = false;
    (async () => {
      setResultsLoading(true);
      setResultsError(null);
      try {
        const traceIdFilter = activeFilters.find((f) => f.key === 'traceID');
        if (traceIdFilter && isTraceId(traceIdFilter.value)) {
          const resp = await TempoApi.getTrace(traceIdFilter.value);
          if (cancelled) return;
          traceCacheRef.current.set(traceIdFilter.value, resp);
          setResults([traceResponseToSearchResult(resp)]);
          // 从审核日志跳转来时自动展开并加载详情
          pendingTraceIdRef.current = traceIdFilter.value;
          setExpandedTraceId(traceIdFilter.value);
          setActiveTrace(resp);
          setTraceLoading(false);
        } else {
          const limit = sortField ? ALL_TRACES_LIMIT : 20;
          const traceqlConds: string[] = [];
          let startTime: number | undefined;
          let endTime: number | undefined;
          for (const f of activeFilters) {
            if (f.key === 'traceID') continue; // traceID 不是 TraceQL 条件
            if (f.key === 'start_time') {
              const r = parseTimeRange(f.value);
              if (r.start) startTime = r.start;
              if (r.end) endTime = r.end;
            } else if (f.key === 'user_id') {
              traceqlConds.push(`.jiuwenclaw.user.id = "${f.value}"`);
            } else if (f.key === 'bot_id') {
              traceqlConds.push(`.jiuwenclaw.bot.id = "${f.value}"`);
            } else if (f.key === 'group_id') {
              traceqlConds.push(`.jiuwenclaw.group.id = "${f.value}"`);
            }
          }
          const query = traceqlConds.length > 0 ? `{ ${traceqlConds.join(' && ')} }` : undefined;
          // Tempo max_search_duration 默认 168h(7天)，超过会报错。
          // 自动截断到 7 天范围内（取 end - 7天 到 end），不报错。
          const MAX_RANGE_S = 168 * 3600;
          const nowS = Math.floor(Date.now() / 1000);
          const minStart = nowS - MAX_RANGE_S;
          if (startTime !== undefined && startTime < minStart) startTime = minStart;
          if (endTime !== undefined && endTime > nowS) endTime = nowS;
          // 如果没指定时间，用默认 7 天窗口
          if (startTime === undefined && endTime === undefined) {
            // 用 api.ts 的默认值
          }
          const resp = await TempoApi.searchTraces(limit, { query, start: startTime, end: endTime });
          if (cancelled) return;
          setResults(resp.traces ?? []);
        }
      } catch (err) {
        if (cancelled) return;
        setResultsError(err instanceof ApiError ? err.detail : String(err));
      } finally {
        if (!cancelled) setResultsLoading(false);
      }
    })();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filters, sortField, sortDir]);

  // 后台预取每条 trace 的 token 消耗总量（用于按 Tokens 列排序）。
  // routing 信息已在 search useEffect 里从 spanSet 直接提取，这里不再预取。
  // 限制并发为 20，避免一次发 500 个请求打爆 Tempo。
  useEffect(() => {
    if (!results || results.length === 0) return;
    let cancelled = false;
    const CONCURRENCY = 20;
    const pending = results.filter((r) => !traceCacheRef.current.has(r.traceID));

    const run = async () => {
      const queue = [...pending];
      const workers = Array.from({ length: Math.min(CONCURRENCY, queue.length) }, async () => {
        while (queue.length > 0 && !cancelled) {
          const r = queue.shift();
          if (!r) break;
          try {
            const resp = await TempoApi.getTrace(r.traceID);
            if (cancelled) return;
            traceCacheRef.current.set(r.traceID, resp);
            let sum = 0;
            let userId: string | undefined;
            let botId: string | undefined;
            let groupId: string | undefined;
            for (const span of flattenSpans(resp)) {
              const tags = spanAttrMap(span);
              const v = Number(tags['gen_ai.usage.total_tokens']);
              if (Number.isFinite(v)) sum += v;
              if (!userId) userId = tags['jiuwenclaw.user.id'] || undefined;
              if (!botId) botId = tags['jiuwenclaw.bot.id'] || undefined;
              if (!groupId) groupId = tags['jiuwenclaw.group.id'] || undefined;
            }
            setTokenByTraceId((prev) => ({ ...prev, [r.traceID]: sum }));
            setRoutingByTraceId((prev) => ({ ...prev, [r.traceID]: { userId, botId, groupId } }));
          } catch {
            // 静默失败, 不影响列表展示
          }
        }
      });
      await Promise.all(workers);
    };

    if (pending.length > 0) {
      setSortLoading(true);
      run().finally(() => { if (!cancelled) setSortLoading(false); });
    }
    return () => { cancelled = true; };
  }, [results]);

  // 点列表项: 已展开则合拢, 否则展开并加载详情
  const onSelectTrace = async (traceId: string) => {
    if (expandedTraceId === traceId) {
      pendingTraceIdRef.current = null;
      setExpandedTraceId(null);
      setActiveTrace(null);
      setTraceError(null);
      return;
    }
    pendingTraceIdRef.current = traceId;
    setExpandedTraceId(traceId);
    setTraceError(null);
    // 优先用 cache (预取时已加载过)
    const cached = traceCacheRef.current.get(traceId);
    if (cached) {
      setActiveTrace(cached);
      setTraceLoading(false);
      return;
    }
    setActiveTrace(null);
    setTraceLoading(true);
    try {
      const resp = await TempoApi.getTrace(traceId);
      if (pendingTraceIdRef.current !== traceId) return;
      traceCacheRef.current.set(traceId, resp);
      setActiveTrace(resp);
    } catch (err) {
      if (pendingTraceIdRef.current !== traceId) return;
      setTraceError(err instanceof ApiError ? err.detail : String(err));
    } finally {
      if (pendingTraceIdRef.current === traceId) {
        setTraceLoading(false);
      }
    }
  };

  const onSortClick = (field: SortField) => {
    if (sortField !== field) {
      // 新字段，默认降序（最大的前20）
      setSortField(field);
      setSortDir('desc');
    } else if (sortDir === 'desc') {
      // 切换到升序（最小的前20）
      setSortDir('asc');
    } else {
      // 恢复默认（最近20条按时间倒序）
      setSortField(null);
      setSortDir(null);
    }
  };

  // 排序后的列表（取前20）
  const sortedResults = useMemo(() => {
    if (!results) return [];
    if (sortField === null || sortDir === null) {
      // 默认按开始时间倒序（最近20条）
      return [...results]
        .sort((a, b) => Number(b.startTimeUnixNano) - Number(a.startTimeUnixNano))
        .slice(0, 20);
    }
    const getVal = (r: TempoSearchResult): string | number => {
      switch (sortField) {
        case 'traceID': return r.traceID;
        case 'operation': return r.rootTraceName;
        case 'startTime': return Number(r.startTimeUnixNano);
        case 'duration': return r.durationMs;
        case 'tokens': return tokenByTraceId[r.traceID] ?? 0;
        case 'userId': return routingByTraceId[r.traceID]?.userId ?? '';
        case 'botId': return routingByTraceId[r.traceID]?.botId ?? '';
        case 'groupId': return routingByTraceId[r.traceID]?.groupId ?? '';
      }
    };
    const sorted = [...results].sort((a, b) => {
      const va = getVal(a);
      const vb = getVal(b);
      if (typeof va === 'number' && typeof vb === 'number') {
        return sortDir === 'desc' ? vb - va : va - vb;
      }
      return sortDir === 'desc'
        ? String(vb).localeCompare(String(va))
        : String(va).localeCompare(String(vb));
    });
    return sorted.slice(0, 20);
  }, [results, sortField, sortDir, tokenByTraceId, routingByTraceId]);

  const tree = useMemo(() => {
    if (!activeTrace?.batches) return [];
    return buildTree(flattenSpans(activeTrace));
  }, [activeTrace]);

  const allSpans = useMemo(
    () => (activeTrace ? flattenSpans(activeTrace) : []),
    [activeTrace],
  );
  const traceStartMs = allSpans.length
    ? Math.min(...allSpans.map((s) => spanStartMs(s)))
    : 0;
  const traceEndMs = allSpans.length
    ? Math.max(...allSpans.map((s) => spanStartMs(s) + spanDurationMs(s)))
    : 0;
  const traceDurationMs = traceEndMs - traceStartMs;

  return (
    <div className="space-y-4">
      {/* 筛选条件区 */}
      <div className="card p-3 space-y-2">
        <div className="text-sm font-semibold">筛选搜索</div>
        {/* 非 start_time 条件放一行 */}
        <div className="flex items-center gap-2 flex-wrap">
          {filters.filter(f => f.key !== 'start_time').map((f) => {
            const idx = filters.indexOf(f);
            return (
              <span key={idx} className="flex items-center gap-1">
                <select
                  className="input"
                  style={{ width: '120px' }}
                  value={f.key}
                  onChange={(e) => {
                    const newKey = e.target.value as FilterItem['key'];
                    updateFilter(idx, { key: newKey, value: '' });
                    if (newKey === 'user_id' || newKey === 'bot_id' || newKey === 'group_id') {
                      loadTagOptions(newKey as FilterKey);
                    }
                  }}
                >
                  <option value="">选择字段...</option>
                  {FILTER_KEY_OPTIONS.filter(({ key }) => {
                    const usedKeys = filters.filter((_, i) => i !== idx).map(x => x.key).filter(Boolean) as FilterKey[];
                    if (usedKeys.includes(key)) return false;
                    if (key === 'traceID' && usedKeys.length > 0) return false;
                    if (key !== 'traceID' && usedKeys.includes('traceID')) return false;
                    return true;
                  }).map(({ key, label }) => (
                    <option key={key} value={key}>{label}</option>
                  ))}
                </select>
                <input
                  className="input"
                  style={{ width: '160px' }}
                  list={`tag-options-${f.key}`}
                  placeholder={f.key ? `输入${FILTER_KEY_OPTIONS.find(o => o.key === f.key)?.label ?? ''}值` : '请先选择字段'}
                  value={f.value}
                  onChange={(e) => updateFilter(idx, { value: e.target.value })}
                />
                {(f.key === 'user_id' || f.key === 'bot_id' || f.key === 'group_id') && (
                  <datalist id={`tag-options-${f.key}`}>
                    {(tagOptions[f.key as FilterKey] ?? []).map((v) => (
                      <option key={v} value={v} />
                    ))}
                  </datalist>
                )}
                <button type="button" className="btn ghost sm" onClick={() => removeFilter(idx)} title="删除此条件">
                  ×
                </button>
              </span>
            );
          })}
          {/* 选了 TraceID 后不允许添加其他条件 */}
          {filters.some(f => f.key === 'traceID') ? (
            <span className="text-xs text-muted">TraceID 是唯一的，不能添加其他条件</span>
          ) : (
            <button type="button" className="btn ghost sm" onClick={addFilter}>
              + 添加条件
            </button>
          )}
        </div>
        {/* start_time 单独一行 */}
        {filters.some(f => f.key === 'start_time') && (() => {
          const idx = filters.findIndex(f => f.key === 'start_time');
          const f = filters[idx];
          return (
            <div className="flex items-center gap-2 flex-wrap">
              <span className="text-sm text-muted whitespace-nowrap">开始时间</span>
              <TimeSelectGroup value={f.value} onChange={(v) => updateFilter(idx, { value: v })} />
              <button type="button" className="btn ghost sm" onClick={() => removeFilter(idx)} title="删除此条件">
                ×
              </button>
            </div>
          );
        })()}
      </div>

      {resultsError && <div className="card p-4 text-danger">{resultsError}</div>}

      <div className="card p-4">
        <div className="text-sm font-semibold mb-3 flex items-center gap-2 flex-wrap">
          {isSearching ? (
            <span>{t('trace.searchResults')}</span>
          ) : sortField && sortDir ? (
            <span>
              按「{FIELD_LABELS[sortField]}」{sortDir === 'desc' ? '降序' : '升序'}（Tempo 返回的所有 trace 中，取前 20）
            </span>
          ) : (
            <span>最近的 20 次请求</span>
          )}
          {sortLoading && <span className="text-xs text-muted font-normal">· 加载中...</span>}
          {!sortField && !isSearching && <span className="text-xs text-muted font-normal">（点列头按该字段排序）</span>}
        </div>
        {resultsLoading ? (
          <div className="text-muted text-sm py-6 text-center">{t('common.loading')}</div>
        ) : !results || results.length === 0 ? (
          <div className="text-muted text-sm py-6 text-center">{t('common.empty')}</div>
        ) : (
          <div className="space-y-1">
            {/* 列头 */}
            <div
              className="grid gap-2 px-2 py-1 text-xs text-muted border-b"
              style={{ gridTemplateColumns: GRID_TEMPLATE }}
            >
              {SORT_FIELDS.map(({ key, label, sortable }) => {
                // 搜索 TraceID 时禁用排序（结果只有一条，排序无意义）
                const canSort = sortable && !isSearching;
                return (
                  <button
                    key={key}
                    type="button"
                    className={`text-left font-semibold transition-colors ${canSort ? 'hover:text-accent cursor-pointer' : 'cursor-default'}`}
                    onClick={canSort ? () => onSortClick(key) : undefined}
                  >
                    {label}
                    <span className="ml-1">
                      {sortField === key
                        ? sortDir === 'desc' ? '↓' : sortDir === 'asc' ? '↑' : ''
                        : ''}
                    </span>
                  </button>
                );
              })}
            </div>
            {/* 数据行 */}
            {sortedResults.map((r) => {
              const reqId = extractRequestId(r);
              const isExpanded = expandedTraceId === r.traceID;
              const rt = routingByTraceId[r.traceID];
              const tk = tokenByTraceId[r.traceID];
              return (
                <div key={r.traceID} className="rounded border overflow-hidden">
                  <button
                    type="button"
                    className={`w-full text-left p-2 border-l-4 transition-colors grid gap-2 text-sm ${
                      isExpanded
                        ? 'border-l-accent bg-accent-subtle'
                        : 'border-l-transparent hover:bg-muted/50'
                    }`}
                    style={{ gridTemplateColumns: GRID_TEMPLATE }}
                    onClick={() => onSelectTrace(r.traceID)}
                    title={reqId ? `request.id: ${reqId}` : r.traceID}
                  >
                    <span className="mono truncate select-all">{r.traceID}</span>
                    <span className="mono truncate">{rt?.userId || '-'}</span>
                    <span className="mono truncate">{rt?.botId || '-'}</span>
                    <span className="mono truncate">{rt?.groupId || '-'}</span>
                    <span className="mono truncate">{r.rootTraceName || '-'}</span>
                    <span className="num text-muted truncate">{formatTime(Number(r.startTimeUnixNano) / 1e6)}</span>
                    <span className="num text-muted truncate">{formatMs(r.durationMs)}</span>
                    <span className="num text-muted truncate">{tk !== undefined ? formatTokenCount(tk) : '...'}</span>
                  </button>
                  {isExpanded && (
                    <div className="p-3 border-t bg-muted/30">
                      {traceLoading ? (
                        <div className="text-muted text-sm py-6 text-center">{t('common.loading')}</div>
                      ) : traceError ? (
                        <div className="text-danger text-sm py-6 text-center">{traceError}</div>
                      ) : activeTrace ? (
                        <div className="max-h-[600px] overflow-y-auto">
                          {tree.map((node) => (
                            <SpanRow
                              key={node.span.spanId}
                              node={node}
                              traceStartMs={traceStartMs}
                              traceDurationMs={traceDurationMs}
                            />
                          ))}
                        </div>
                      ) : null}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
