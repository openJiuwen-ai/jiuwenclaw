import { useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import {
  PromInstantResponse,
  PromRangeResponse,
  PrometheusApi,
  TempoApi,
} from '../../services/api';

type RangeKey = '24h' | '1w' | '1mo' | '1y';

const RANGES: Record<RangeKey, {
  ms: number;
  step: string;
  prom: string;
  window: string;
  tick: Intl.DateTimeFormatOptions;
}> = {
  '24h': { ms: 86_400_000, step: '300s', prom: '24h', window: '5m', tick: { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' } },
  '1w': { ms: 604_800_000, step: '1800s', prom: '7d', window: '30m', tick: { month: 'numeric', day: 'numeric' } },
  '1mo': { ms: 2_592_000_000, step: '7200s', prom: '30d', window: '2h', tick: { month: 'numeric', day: 'numeric' } },
  '1y': { ms: 31_536_000_000, step: '86400s', prom: '365d', window: '1d', tick: { month: 'short' } },
};

type DimensionKey = 'user_id' | 'bot_id' | 'group_id';

const DIMENSION_LABEL: Record<DimensionKey, string> = {
  user_id: 'jiuwenclaw_user_id',
  bot_id: 'jiuwenclaw_bot_id',
  group_id: 'jiuwenclaw_group_id',
};

const TOKEN_TYPE_LABEL: Record<string, string> = {
  'gen_ai_token_type=input': 'input',
  'gen_ai_token_type=output': 'output',
  'gen_ai_token_type=cache_read': 'cache_read',
};

const LINE_COLORS: Record<string, string> = {
  input: '#3b82f6',
  output: '#ef4444',
  cache_read: '#f59e0b',
};

const RANKING_DIMENSIONS: DimensionKey[] = ['user_id', 'bot_id', 'group_id'];
const RANKING_BAR_COLOR = '#6366f1';

// 筛选条件
type FilterKey = 'user_id' | 'bot_id' | 'group_id';
interface FilterItem { key: FilterKey | ''; value: string }
const FILTER_KEY_OPTIONS: { key: FilterKey; label: string }[] = [
  { key: 'user_id', label: 'UserID' },
  { key: 'bot_id', label: 'BotID' },
  { key: 'group_id', label: 'GroupID' },
];
const TAG_FOR_FILTER: Record<FilterKey, string> = {
  user_id: 'jiuwenclaw.user.id',
  bot_id: 'jiuwenclaw.bot.id',
  group_id: 'jiuwenclaw.group.id',
};

type Row = Record<string, number | string | null>;

function toDateString(d: Date): string {
  return d.toISOString().slice(0, 10); // YYYY-MM-DD
}

function promEscape(value: string): string {
  // Prometheus label matcher value escaping: backslash, double-quote, newline.
  return value.replace(/\\/g, '\\\\').replace(/"/g, '\\"').replace(/\n/g, '\\n');
}

function toChartData(resp: PromRangeResponse): Row[] {
  const series = resp.data?.result ?? [];
  const byTs = new Map<number, Row>();
  for (const s of series) {
    const label = Object.values(s.metric).join('|') || 'value';
    for (const [ts, valStr] of s.values) {
      const v = parseFloat(valStr);
      if (!byTs.has(ts)) byTs.set(ts, { t: ts });
      (byTs.get(ts) as Row)[label] = isNaN(v) ? null : v;
    }
  }
  return [...byTs.entries()]
    .map(([t, vals]) => ({ ...vals, t } as Row))
    .sort((a, b) => (a.t as number) - (b.t as number));
}

function seriesKeys(rows: Row[]): string[] {
  const set = new Set<string>();
  for (const r of rows) for (const k of Object.keys(r)) if (k !== 't') set.add(k);
  return [...set];
}

function remapTokenType(rows: Row[]): Row[] {
  // Rename `gen_ai_token_type=input` -> `input` for friendlier legend colors.
  return rows.map((r) => {
    const out: Row = { t: r.t };
    for (const k of Object.keys(r)) {
      if (k === 't') continue;
      const key = TOKEN_TYPE_LABEL[k] ?? k;
      out[key] = r[k];
    }
    return out;
  });
}

function topKToRows(resp: PromInstantResponse): { id: string; value: number }[] {
  const series = resp.data?.result ?? [];
  const out: { id: string; value: number }[] = [];
  for (const s of series) {
    const id = s.metric?.[Object.keys(s.metric)[0]] ?? '';
    const raw = s.value?.[1];
    const v = raw != null ? parseFloat(raw) : NaN;
    out.push({ id, value: isNaN(v) ? 0 : v });
  }
  out.sort((a, b) => b.value - a.value);
  return out;
}

function formatTokens(n: number): string {
  if (!isFinite(n)) return '0';
  if (n >= 100000000) {
    return (n / 100000000).toFixed(2) + '亿';
  }
  if (n >= 10000) {
    return (n / 10000).toFixed(2) + '万';
  }
  return Math.round(n).toLocaleString();
}

// Y 轴专用：取整，不带小数
function formatTokensInt(n: number): string {
  if (!isFinite(n)) return '0';
  if (n >= 100000000) return Math.floor(n / 100000000) + '亿';
  if (n >= 10000) return Math.floor(n / 10000) + '万';
  return Math.round(n).toLocaleString();
}

interface BigChartData {
  rows: Row[];
  keys: string[];
  startSec: number;
  endSec: number;
}

interface RankingData {
  user_id: { id: string; value: number }[];
  bot_id: { id: string; value: number }[];
  group_id: { id: string; value: number }[];
}

function BigChartCard({
  title,
  data,
  startSec,
  endSec,
  tick,
  loading,
}: {
  title: string;
  data: BigChartData | null;
  startSec: number;
  endSec: number;
  tick: Intl.DateTimeFormatOptions;
  loading: boolean;
}) {
  const { t } = useTranslation();
  const fmt = (ts: number) => new Date(ts * 1000).toLocaleString([], tick);
  const rows = data?.rows ?? [];
  const keys = data?.keys ?? [];
  return (
    <div className="card p-4">
      <div className="text-sm font-semibold mb-2">{title}</div>
      <div style={{ width: '100%', height: 360 }}>
        {loading ? (
          <div
            className="text-muted text-sm"
            style={{ height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center' }}
          >
            {t('common.loading')}
          </div>
        ) : rows.length === 0 ? (
          <div
            className="text-muted text-sm"
            style={{ height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center' }}
          >
            {t('common.empty')}
          </div>
        ) : (
          <ResponsiveContainer>
            <LineChart data={rows} margin={{ top: 5, right: 20, left: 0, bottom: 5 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
              <XAxis
                dataKey="t"
                type="number"
                domain={[startSec, endSec]}
                tickFormatter={fmt}
                tick={{ fontSize: 11 }}
              />
              <YAxis tick={{ fontSize: 11 }} tickFormatter={(v) => formatTokensInt(Number(v))} />
              <Tooltip
                labelFormatter={fmt}
                formatter={(value) => formatTokens(Number(value))}
              />
              <Legend />
              {keys.map((k) => (
                <Line
                  key={k}
                  type="monotone"
                  dataKey={k}
                  stroke={LINE_COLORS[k] ?? '#6366f1'}
                  dot={{ r: 3 }}
                  connectNulls
                  isAnimationActive={false}
                  label={(props: any) => {
                    const { x, y, value } = props;
                    const formatted = (value != null && isFinite(Number(value))) ? formatTokens(Number(value)) : '';
                    return (
                      <text x={x} y={y} dy={-8} fill="#6b7280" fontSize={11} textAnchor="middle">
                        {formatted}
                      </text>
                    );
                  }}
                />
              ))}
            </LineChart>
          </ResponsiveContainer>
        )}
      </div>
    </div>
  );
}

function RankingCard({
  dimension,
  rows,
  loading,
  onSelect,
}: {
  dimension: DimensionKey;
  rows: { id: string; value: number }[];
  loading: boolean;
  onSelect: (id: string) => void;
}) {
  const { t } = useTranslation();
  const maxValue = rows.length > 0 ? Math.max(...rows.map((r) => r.value), 1) : 1;
  return (
    <div className="card p-4">
      <div className="text-sm font-semibold mb-3">
        {t('observability.topNTitle', { field: dimension, n: 5 })}
      </div>
      {loading ? (
        <div className="text-muted text-sm py-6 text-center">{t('common.loading')}</div>
      ) : rows.length === 0 ? (
        <div className="text-muted text-sm py-6 text-center">{t('common.empty')}</div>
      ) : (
        <ol className="space-y-2">
          {rows.map((r, i) => {
            const pct = Math.max(4, Math.round((r.value / maxValue) * 100));
            const idDisplay = r.id || t('observability.unknownId');
            return (
              <li key={`${i}-${r.id}`}>
                <button
                  type="button"
                  className="w-full text-left space-y-1"
                  onClick={() => onSelect(r.id)}
                  title={t('observability.clickToFilter', { id: idDisplay })}
                >
                  <div className="flex items-center justify-between text-xs">
                    <span className="mono truncate">{idDisplay}</span>
                    <span className="num text-muted">{formatTokens(r.value)}</span>
                  </div>
                  <div
                    style={{
                      height: 6,
                      width: '100%',
                      background: 'var(--bg-muted)',
                      borderRadius: 999,
                      overflow: 'hidden',
                    }}
                  >
                    <div
                      style={{
                        height: '100%',
                        width: `${pct}%`,
                        background: RANKING_BAR_COLOR,
                        borderRadius: 999,
                      }}
                    />
                  </div>
                </button>
              </li>
            );
          })}
        </ol>
      )}
    </div>
  );
}

export function TokenUsageTab() {
  const { t } = useTranslation();
  // 筛选条件（UserID/BotID/GroupID，AND 组合）
  const [filters, setFilters] = useState<FilterItem[]>([{ key: '', value: '' }]);
  const [tagOptions, setTagOptions] = useState<Partial<Record<FilterKey, string[]>>>({});
  const tagLoadingRef = useRef<Set<string>>(new Set());
  // 时间范围（精确到日，默认最近一周）
  const defaultEnd = new Date();
  const defaultStart = new Date(defaultEnd.getTime() - 7 * 24 * 3600 * 1000);
  const [startDate, setStartDate] = useState(toDateString(defaultStart));
  const [endDate, setEndDate] = useState(toDateString(defaultEnd));
  // Top 5 排行的独立 range 选择
  const [rankingRange, setRankingRange] = useState<RangeKey>('1mo');

  const [bigChart, setBigChart] = useState<BigChartData | null>(null);
  const [bigChartLoading, setBigChartLoading] = useState(true);
  const [bigChartError, setBigChartError] = useState<string | null>(null);

  const [rankings, setRankings] = useState<RankingData | null>(null);
  const [rankingsLoading, setRankingsLoading] = useState(true);
  const [rankingsError, setRankingsError] = useState<string | null>(null);

  const activeFilters = filters.filter((f) => f.key && f.value);
  const hasFilter = activeFilters.length > 0;

  // tag 自动补全
  const loadTagOptions = (key: FilterKey) => {
    const tag = TAG_FOR_FILTER[key];
    const ref = tagLoadingRef.current;
    if (ref.has(key) || tagOptions[key]) return;
    ref.add(key);
    TempoApi.tagValues(tag)
      .then((resp) => setTagOptions((prev) => ({ ...prev, [key]: resp.tagValues ?? [] })))
      .catch(() => {})
      .finally(() => { ref.delete(key); });
  };

  const addFilter = () => setFilters((prev) => [...prev, { key: '', value: '' }]);
  const removeFilter = (idx: number) => setFilters((prev) => {
    const next = prev.filter((_, i) => i !== idx);
    return next.length === 0 ? [{ key: '', value: '' }] : next;
  });
  const updateFilter = (idx: number, patch: Partial<FilterItem>) =>
    setFilters((prev) => prev.map((f, i) => (i === idx ? { ...f, ...patch } : f)));

  // 构造 PromQL matcher（如 {jiuwenclaw_user_id="3",jiuwenclaw_bot_id="3"}）
  const promqlMatcher = useMemo(() => {
    if (activeFilters.length === 0) return '';
    const pairs = activeFilters.map((f) => `${DIMENSION_LABEL[f.key as DimensionKey]}="${promEscape(f.value)}"`);
    return `{${pairs.join(',')}}`;
  }, [activeFilters]);

  // 从时间范围算 step/window/tick
  const { step, window: promWindow, tick } = useMemo(() => {
    const startMs = new Date(startDate).getTime();
    const endMs = new Date(endDate).getTime();
    const diffMs = endMs - startMs;
    if (diffMs <= 86_400_000) return { step: '300s', window: '5m', tick: RANGES['24h'].tick };
    if (diffMs <= 604_800_000) return { step: '86400s', window: '1d', tick: RANGES['1w'].tick };
    if (diffMs <= 2_592_000_000) return { step: '7200s', window: '2h', tick: RANGES['1mo'].tick };
    return { step: '86400s', window: '1d', tick: RANGES['1y'].tick };
  }, [startDate, endDate]);

  // Big chart: cumulative token usage curve
  useEffect(() => {
    let cancelled = false;
    (async () => {
      setBigChartLoading(true);
      setBigChartError(null);
      try {
        const s = Math.floor(new Date(startDate).getTime() / 1000);
        const e = Math.floor(new Date(endDate).getTime() / 1000) + 86399; // 包含整天
        const promql = `sum(max_over_time(gen_ai_client_token_usage_total${promqlMatcher}[${promWindow}])) by (gen_ai_token_type)`;
        const resp = await PrometheusApi.queryRange(promql, s, e, step);
        if (cancelled) return;
        const rows = remapTokenType(toChartData(resp));
        setBigChart({ rows, keys: seriesKeys(rows), startSec: s, endSec: e });
      } catch (err) {
        if (!cancelled) setBigChartError(err instanceof Error ? err.message : String(err));
      } finally {
        if (!cancelled) setBigChartLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [startDate, endDate, promqlMatcher, step, promWindow]);

  // Rankings: top-5 by each dimension（受独立 rankingRange 控制）
  useEffect(() => {
    let cancelled = false;
    (async () => {
      setRankingsLoading(true);
      setRankingsError(null);
      try {
        const prom = RANGES[rankingRange].prom;
        const queries: Record<DimensionKey, string> = {
          user_id: `topk(5, sum(max_over_time(gen_ai_client_token_usage_total{jiuwenclaw_user_id!=""}[${prom}])) by (jiuwenclaw_user_id))`,
          bot_id: `topk(5, sum(max_over_time(gen_ai_client_token_usage_total{jiuwenclaw_bot_id!=""}[${prom}])) by (jiuwenclaw_bot_id))`,
          group_id: `topk(5, sum(max_over_time(gen_ai_client_token_usage_total{jiuwenclaw_group_id!=""}[${prom}])) by (jiuwenclaw_group_id))`,
        };
        const [u, b, g] = await Promise.all([
          PrometheusApi.query(queries.user_id),
          PrometheusApi.query(queries.bot_id),
          PrometheusApi.query(queries.group_id),
        ]);
        if (cancelled) return;
        setRankings({ user_id: topKToRows(u), bot_id: topKToRows(b), group_id: topKToRows(g) });
      } catch (err) {
        if (!cancelled) setRankingsError(err instanceof Error ? err.message : String(err));
      } finally {
        if (!cancelled) setRankingsLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [rankingRange]);

  const bigChartTitle = hasFilter
    ? `Token 用量（${activeFilters.map((f) => `${f.key}=${f.value}`).join('，')}）`
    : t('observability.tokenUsageOverall');

  const totalTokens = useMemo(() => {
    if (!bigChart || bigChart.rows.length === 0) return null;
    const lastRow = bigChart.rows[bigChart.rows.length - 1];
    let sum = 0;
    let hasValue = false;
    for (const k of bigChart.keys) {
      const v = lastRow[k];
      if (typeof v === 'number' && isFinite(v)) { sum += v; hasValue = true; }
    }
    return hasValue ? sum : null;
  }, [bigChart]);

  const onSelectFromRanking = (dim: DimensionKey) => (id: string) => {
    // 点 ranking bar 后，设置对应的 filter
    setFilters([{ key: dim, value: id }]);
  };

  return (
    <div className="space-y-4">
      {/* 筛选条件区（Big Chart 用） */}
      <div className="card p-3 space-y-2">
        <div className="text-sm font-semibold">筛选搜索</div>
        {/* 时间范围（开始+结束，一行） */}
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-sm text-muted whitespace-nowrap">开始时间</span>
          <input
            type="date"
            className="input"
            style={{ width: '160px' }}
            value={startDate}
            onChange={(e) => setStartDate(e.target.value)}
          />
          <span className="text-sm text-muted whitespace-nowrap">结束时间</span>
          <input
            type="date"
            className="input"
            style={{ width: '160px' }}
            value={endDate}
            onChange={(e) => setEndDate(e.target.value)}
          />
        </div>
        {/* 筛选条件（UserID/BotID/GroupID，一行） */}
        <div className="flex items-center gap-2 flex-wrap">
          {filters.map((f, idx) => (
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
                  return !usedKeys.includes(key);
                }).map(({ key, label }) => (
                  <option key={key} value={key}>{label}</option>
                ))}
              </select>
              <input
                className="input"
                style={{ width: '160px' }}
                list={`token-tag-options-${f.key}`}
                placeholder={f.key ? `输入${FILTER_KEY_OPTIONS.find(o => o.key === f.key)?.label ?? ''}值` : '请先选择字段'}
                value={f.value}
                onChange={(e) => updateFilter(idx, { value: e.target.value })}
              />
              {(f.key === 'user_id' || f.key === 'bot_id' || f.key === 'group_id') && (
                <datalist id={`token-tag-options-${f.key}`}>
                  {(tagOptions[f.key as FilterKey] ?? []).map((v) => (
                    <option key={v} value={v} />
                  ))}
                </datalist>
              )}
              <button type="button" className="btn ghost sm" onClick={() => removeFilter(idx)} title="删除此条件">
                ×
              </button>
            </span>
          ))}
          <button type="button" className="btn ghost sm" onClick={addFilter}>
            + 添加条件
          </button>
        </div>
      </div>

      {bigChartError && <div className="card p-4 text-danger">{bigChartError}</div>}
      {rankingsError && <div className="card p-4 text-danger">{rankingsError}</div>}

      {/* 总 token 用量 + 大图表 */}
      <div className="flex items-center gap-2 text-sm whitespace-nowrap">
        <span className="text-muted">{t('observability.totalTokens')}</span>
        <span className="num font-semibold">
          {bigChartLoading ? t('common.loading') : totalTokens != null ? formatTokens(totalTokens) : t('common.empty')}
        </span>
      </div>

      <BigChartCard
        title={bigChartTitle}
        data={bigChart}
        startSec={bigChart?.startSec ?? 0}
        endSec={bigChart?.endSec ?? 0}
        tick={tick}
        loading={bigChartLoading}
      />

      {/* Top 5 排行（带独立 range 选择） */}
      <div className="flex items-center gap-3">
        <span className="text-sm font-semibold">Top 5 排行</span>
        <select
          className="border rounded px-2 py-1 text-sm bg-transparent"
          value={rankingRange}
          onChange={(ev) => setRankingRange(ev.target.value as RangeKey)}
        >
          <option value="24h">24h</option>
          <option value="1w">1 周</option>
          <option value="1mo">1 月</option>
          <option value="1y">1 年</option>
        </select>
      </div>
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        {RANKING_DIMENSIONS.map((dim) => (
          <RankingCard
            key={dim}
            dimension={dim}
            rows={rankings?.[dim] ?? []}
            loading={rankingsLoading}
            onSelect={onSelectFromRanking(dim)}
          />
        ))}
      </div>

    </div>
  );
}
