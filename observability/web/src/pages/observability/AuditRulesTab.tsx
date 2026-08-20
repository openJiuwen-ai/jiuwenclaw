import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useRouter } from '../../router';
import { AuditRulesApi, AuditRule } from '../../services/api';

const DETECTOR_OPTIONS = [
  { value: '', label: '全部' },
  { value: 'tool_risk', label: '工具风险' },
  { value: 'pii', label: 'PII 扫描' },
  { value: 'safety', label: '安全过滤' },
];

const SEVERITY_OPTIONS = [
  { value: 'high', label: 'High' },
  { value: 'medium', label: 'Medium' },
  { value: 'low', label: 'Low' },
];

const ACTION_OPTIONS = [
  { value: 'log', label: 'Log' },
  { value: 'warn', label: 'Warn' },
  { value: 'block', label: 'Block' },
];

function emptyRule(): AuditRule {
  return {
    detector: 'safety',
    rule_name: '',
    pattern: '',
    severity: 'medium',
    action: 'log',
    enabled: 1,
    description: '',
  };
}

export function AuditRulesTab() {
  const { t } = useTranslation();
  const { navigate } = useRouter();
  const [rules, setRules] = useState<AuditRule[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [filter, setFilter] = useState('');
  const [editing, setEditing] = useState<AuditRule | null>(null);
  const [isNew, setIsNew] = useState(false);
  const [testText, setTestText] = useState('');
  const [testResult, setTestResult] = useState<{ matched: boolean; match: string | null; error?: string } | null>(null);

  const load = () => {
    setLoading(true);
    AuditRulesApi.list(filter || undefined)
      .then((data) => { setRules(data); setError(null); })
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
  };

  useEffect(() => { load(); }, [filter]);

  const handleSave = () => {
    if (!editing) return;
    const rule = { ...editing, enabled: editing.enabled ? 1 : 0 };
    const saveFn = isNew
      ? AuditRulesApi.create(rule)
      : AuditRulesApi.update(editing.id!, rule);
    saveFn
      .then(() => { setEditing(null); setIsNew(false); load(); })
      .catch((e) => setError(String(e)));
  };

  const handleDelete = (id: number) => {
    AuditRulesApi.remove(id).then(() => load());
  };

  const handleToggle = (rule: AuditRule) => {
    AuditRulesApi.update(rule.id!, { enabled: rule.enabled ? 0 : 1 }).then(() => load());
  };

  const handleTest = () => {
    if (!editing || !testText) return;
    AuditRulesApi.test(editing.pattern, testText).then(setTestResult);
  };

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <button className="btn ghost sm" onClick={() => navigate('/observability?tab=auditLog')}>← 返回</button>
          <h1 className="text-xl font-semibold">审计规则配置</h1>
        </div>
        <button
          className="btn primary sm"
          onClick={() => { setEditing(emptyRule()); setIsNew(true); setTestResult(null); }}
        >
          + 新增规则
        </button>
      </div>

      {/* Filter */}
      <div className="flex items-center gap-2">
        {DETECTOR_OPTIONS.map((opt) => (
          <button
            key={opt.value}
            className={`btn ghost sm ${filter === opt.value ? 'active' : ''}`}
            onClick={() => setFilter(opt.value)}
          >
            {opt.label}
          </button>
        ))}
      </div>

      {error && <div className="card p-4 text-danger">{error}</div>}

      {/* Rules table */}
      <div className="card p-0 overflow-hidden">
        {loading ? (
          <div className="p-8 text-center text-muted text-sm">{t('common.loading')}</div>
        ) : rules.length === 0 ? (
          <div className="p-8 text-center text-muted text-sm">{t('common.empty')}</div>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b text-left text-xs text-muted">
                <th className="px-3 py-2">规则名称</th>
                <th className="px-3 py-2">正则表达式</th>
                <th className="px-3 py-2">启用</th>
                <th className="px-3 py-2">检测器</th>
                <th className="px-3 py-2">严重度</th>
                <th className="px-3 py-2">动作</th>
                <th className="px-3 py-2">说明</th>
                <th className="px-3 py-2">操作</th>
              </tr>
            </thead>
            <tbody>
              {rules.map((rule) => {
                const isExpanded = expandedId === rule.id;
                return (
                  <>
                    <tr
                      key={rule.id}
                      className={`border-b cursor-pointer ${isExpanded ? 'bg-accent-subtle' : 'hover:bg-muted/50'}`}
                      onClick={() => setExpandedId(isExpanded ? null : rule.id!)}
                    >
                      <td className="px-3 py-2 whitespace-nowrap mono text-xs">{rule.rule_name}</td>
                      <td className="px-3 py-2 truncate max-w-xs mono text-xs">{rule.pattern}</td>
                      <td className="px-3 py-2">
                        <input
                          type="checkbox"
                          checked={!!rule.enabled}
                          onChange={(e) => { e.stopPropagation(); handleToggle(rule); }}
                          onClick={(e) => e.stopPropagation()}
                        />
                      </td>
                      <td className="px-3 py-2 whitespace-nowrap">{rule.detector}</td>
                      <td className="px-3 py-2 whitespace-nowrap">{rule.severity}</td>
                      <td className="px-3 py-2 whitespace-nowrap">{rule.action}</td>
                      <td className="px-3 py-2 truncate max-w-xs text-xs text-muted">{rule.description || '-'}</td>
                      <td className="px-3 py-2 whitespace-nowrap" onClick={(e) => e.stopPropagation()}>
                        <button className="btn ghost sm" onClick={() => { setEditing({ ...rule, enabled: rule.enabled ? 1 : 0 }); setIsNew(false); setTestResult(null); }}>编辑</button>
                        <button className="btn ghost sm text-danger" onClick={() => handleDelete(rule.id!)}>删除</button>
                      </td>
                    </tr>
                    {isExpanded && (
                      <tr key={`${rule.id}-detail`}>
                        <td colSpan={8} className="px-3 py-3 bg-white dark:bg-[var(--bg-card)]">
                          <div className="text-xs space-y-1">
                            <div><span className="text-muted inline-block w-24">规则名称：</span><span className="mono select-all">{rule.rule_name}</span></div>
                            <div><span className="text-muted inline-block w-24">正则表达式：</span><span className="mono break-all select-all">{rule.pattern}</span></div>
                            <div><span className="text-muted inline-block w-24">检测器：</span><span className="select-all">{rule.detector}</span></div>
                            <div><span className="text-muted inline-block w-24">严重度：</span><span className="select-all">{rule.severity}</span></div>
                            <div><span className="text-muted inline-block w-24">动作：</span><span className="select-all">{rule.action}</span></div>
                            <div><span className="text-muted inline-block w-24">说明：</span><span className="select-all">{rule.description || '-'}</span></div>
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

      {/* Edit modal */}
      {editing && (
        <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50" onClick={() => setEditing(null)}>
          <div className="card p-6 w-full max-w-2xl max-h-[80vh] overflow-y-auto space-y-3" onClick={(e) => e.stopPropagation()}>
            <div className="text-base font-semibold">{isNew ? '新增规则' : '编辑规则'}</div>

            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="text-xs text-muted">检测器</label>
                <select
                  className="input w-full"
                  value={editing.detector}
                  onChange={(e) => setEditing({ ...editing, detector: e.target.value })}
                >
                  <option value="tool_risk">tool_risk</option>
                  <option value="pii">pii</option>
                  <option value="safety">safety</option>
                </select>
              </div>
              <div>
                <label className="text-xs text-muted">规则名称</label>
                <input
                  className="input w-full"
                  value={editing.rule_name}
                  onChange={(e) => setEditing({ ...editing, rule_name: e.target.value })}
                />
              </div>
            </div>

            <div className="grid grid-cols-3 gap-3">
              <div>
                <label className="text-xs text-muted">严重度</label>
                <select
                  className="input w-full"
                  value={editing.severity}
                  onChange={(e) => setEditing({ ...editing, severity: e.target.value })}
                >
                  {SEVERITY_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                </select>
              </div>
              <div>
                <label className="text-xs text-muted">动作</label>
                <select
                  className="input w-full"
                  value={editing.action}
                  onChange={(e) => setEditing({ ...editing, action: e.target.value })}
                >
                  {ACTION_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                </select>
              </div>
              <div>
                <label className="text-xs text-muted">启用</label>
                <select
                  className="input w-full"
                  value={editing.enabled ? '1' : '0'}
                  onChange={(e) => setEditing({ ...editing, enabled: e.target.value === '1' ? 1 : 0 })}
                >
                  <option value="1">是</option>
                  <option value="0">否</option>
                </select>
              </div>
            </div>

            <div>
              <label className="text-xs text-muted">正则表达式</label>
              <textarea
                className="input w-full font-mono text-xs"
                rows={3}
                value={editing.pattern}
                onChange={(e) => setEditing({ ...editing, pattern: e.target.value })}
              />
            </div>

            <div>
              <label className="text-xs text-muted">说明</label>
              <input
                className="input w-full"
                value={editing.description}
                onChange={(e) => setEditing({ ...editing, description: e.target.value })}
              />
            </div>

            {/* Test area */}
            <div className="border-t pt-3 space-y-2">
              <div className="text-sm font-semibold">测试规则</div>
              <textarea
                className="input w-full text-xs"
                rows={2}
                placeholder="输入测试文本..."
                value={testText}
                onChange={(e) => setTestText(e.target.value)}
              />
              {testResult && (
                <div className={`text-xs ${testResult.matched ? 'text-green-600' : 'text-red-600'}`}>
                  {testResult.error
                    ? testResult.error
                    : testResult.matched
                      ? `命中: ${testResult.match}`
                      : '未命中'}
                </div>
              )}
            </div>

            <div className="flex justify-end gap-2 pt-2 border-t">
              <button className="btn ghost" onClick={() => setEditing(null)}>取消</button>
              <button className="btn primary" onClick={handleSave}>保存</button>
              <button className="btn ghost" onClick={handleTest}>测试</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
