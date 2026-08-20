import { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useRouter } from '../../router';
import { LokiApi, parseLokiAuditStreams, AuditLogEntry } from '../../services/api';

const AUDIT_TYPE_LABELS: Record<string, string> = {
  tool_action: '工具审核',
  privacy_pii: '隐私审核',
  guardrails_safety: '安全审核',
};

const AUDIT_TYPE_COLORS: Record<string, string> = {
  tool_action: '#3b82f6',
  privacy_pii: '#f59e0b',
  guardrails_safety: '#ef4444',
};

function formatTime(ms: number): string {
  return new Date(ms).toLocaleString([], {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
}

export function AuditLogTab() {
  const { t } = useTranslation();
  const { navigate } = useRouter();
  const defaultEnd = new Date();
  const defaultStart = new Date(defaultEnd.getTime() - 24 * 3600 * 1000);
  const [startDate, setStartDate] = useState(defaultStart.toISOString().slice(0, 10));
  const [endDate, setEndDate] = useState(defaultEnd.toISOString().slice(0, 10));
  const [auditType, setAuditType] = useState('');
  const [userId, setUserId] = useState('');
  const [groupId, setGroupId] = useState('');
  const [entries, setEntries] = useState<AuditLogEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);

  const logql = useMemo(() => {
    let q = '{service_name="jiuwenclaw-agentserver"}';
    const filters: string[] = [];
    if (auditType) {
      filters.push(`audit_type="${auditType}"`);
    }
    if (userId) filters.push(`user_id="${userId}"`);
    if (groupId) filters.push(`group_id="${groupId}"`);
    if (filters.length > 0) {
      q += ` | ${filters.join(' | ')}`;
    }
    return q;
  }, [auditType, userId, groupId]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      setError(null);
      try {
        const s = Math.floor(new Date(startDate).getTime() / 1000);
        const e = Math.floor(new Date(endDate).getTime() / 1000) + 86399;
        const resp = await LokiApi.queryRange(logql, s, e, 500);
        if (cancelled) return;
        setEntries(parseLokiAuditStreams(resp));
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [logql, startDate, endDate]);

  return (
    <div className="space-y-4">
      {/* 筛选区 */}
      <div className="card p-3 space-y-2">
        <div className="flex items-center justify-between">
          <div className="text-sm font-semibold">筛选搜索</div>
          <button className="btn ghost sm" onClick={() => navigate('/observability?tab=auditRules')}>审计规则配置 →</button>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          <select
            className="input"
            style={{ width: '140px' }}
            value={auditType}
            onChange={(e) => setAuditType(e.target.value)}
          >
            <option value="">全部类型</option>
            <option value="tool_action">工具审核</option>
            <option value="privacy_pii">隐私审核</option>
            <option value="guardrails_safety">安全审核</option>
          </select>
          <input
            type="date"
            className="input"
            style={{ width: '150px' }}
            value={startDate}
            onChange={(e) => setStartDate(e.target.value)}
          />
          <input
            type="date"
            className="input"
            style={{ width: '150px' }}
            value={endDate}
            onChange={(e) => setEndDate(e.target.value)}
          />
          <input
            className="input"
            style={{ width: '140px' }}
            placeholder="UserID"
            value={userId}
            onChange={(e) => setUserId(e.target.value)}
          />
          <input
            className="input"
            style={{ width: '140px' }}
            placeholder="GroupID"
            value={groupId}
            onChange={(e) => setGroupId(e.target.value)}
          />
        </div>
      </div>

      {error && <div className="card p-4 text-danger">{error}</div>}

      {/* 列表 */}
      <div className="card p-0 overflow-hidden">
        {loading ? (
          <div className="p-8 text-center text-muted text-sm">{t('common.loading')}</div>
        ) : entries.length === 0 ? (
          <div className="p-8 text-center text-muted text-sm">{t('common.empty')}</div>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b text-left text-xs text-muted">
                <th className="px-3 py-2">时间</th>
                <th className="px-3 py-2">类型</th>
                <th className="px-3 py-2">摘要</th>
                <th className="px-3 py-2">用户</th>
                <th className="px-3 py-2">Trace</th>
              </tr>
            </thead>
            <tbody>
              {entries.map((entry, i) => {
                const key = `${i}-${entry.timestamp}`;
                const isExpanded = expanded === key;
                const color = AUDIT_TYPE_COLORS[entry.auditType] ?? '#6b7280';
                return (
                  <>
                    <tr
                      key={key}
                      className={`border-b cursor-pointer ${isExpanded ? 'bg-accent-subtle' : 'hover:bg-muted/50'}`}
                      onClick={() => setExpanded(isExpanded ? null : key)}
                    >
                      <td className="px-3 py-2 whitespace-nowrap text-muted num">
                        {formatTime(entry.timestamp)}
                      </td>
                      <td className="px-3 py-2 whitespace-nowrap">
                        <span
                          className="px-2 py-0.5 rounded text-xs font-medium"
                          style={{ background: color + '20', color }}
                        >
                          {AUDIT_TYPE_LABELS[entry.auditType] ?? entry.auditType}
                        </span>
                      </td>
                      <td className="px-3 py-2 truncate max-w-md">{entry.body}</td>
                      <td className="px-3 py-2 whitespace-nowrap mono text-xs">
                        {entry.userId || '-'}
                      </td>
                      <td className="px-3 py-2 whitespace-nowrap mono text-xs">
                        {entry.traceId ? (
                          <button
                            className="text-blue-500 hover:underline mono text-xs"
                            onClick={(e) => {
                              e.stopPropagation();
                              navigate(`/observability?tab=trace&traceId=${entry.traceId}`);
                            }}
                          >
                            {entry.traceId.slice(0, 8)}…
                          </button>
                        ) : '-'}
                      </td>
                    </tr>
                    {isExpanded && (
                      <tr key={`${key}-detail`}>
                        <td colSpan={5} className="px-3 py-3 bg-white dark:bg-[var(--bg-card)]">
                          <div className="space-y-1 text-xs">
                            <div><span className="text-muted">审计类型:</span> {entry.auditType}</div>
                            <div><span className="text-muted">Trace ID:</span> <span className="mono">{entry.traceId || '-'}</span></div>
                            <div><span className="text-muted">Request ID:</span> <span className="mono">{entry.requestId || '-'}</span></div>
                            <div><span className="text-muted">Session ID:</span> <span className="mono">{entry.sessionId || '-'}</span></div>
                            <div><span className="text-muted">Agent ID:</span> <span className="mono">{entry.agentName || '-'}</span></div>
                            <div><span className="text-muted">Agent Pod:</span> <span className="mono">{entry.agentPod || '-'}</span></div>
                            <div><span className="text-muted">User ID:</span> {entry.userId || '-'}</div>
                            <div><span className="text-muted">Bot ID:</span> {entry.botId || '-'}</div>
                            <div><span className="text-muted">Group ID:</span> {entry.groupId || '-'}</div>
                            <div className="pt-2 border-t mt-2">
                              <div className="text-muted mb-1">详情字段:</div>
                              {Object.keys(entry.details).length > 0 ? (
                                <table className="text-xs">
                                  <tbody>
                                    {Object.entries(entry.details).map(([k, v]) => (
                                      <tr key={k}>
                                        <td className="pr-4 text-muted align-top">{k}</td>
                                        <td className="mono break-all">{v}</td>
                                      </tr>
                                    ))}
                                  </tbody>
                                </table>
                              ) : (
                                <span className="text-muted">无</span>
                              )}
                            </div>
                          </div>
                        </td>
                      </tr>
                    )}
                  </>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
