import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import type { WebError } from '../../types/websocket';
import { A2AOutboundPanel } from './A2AOutboundPanel';
import {
  canOperateA2AIngress,
  draftFromA2AIngressSnapshot,
  isA2AIngressTransitioning,
  normalizeA2AIngressHistory,
  normalizeA2AIngressSnapshot,
  shouldAcceptA2AIngressResponse,
  toA2AIngressPatch,
  validateA2AIngressDraft,
  type A2AIngressDraft,
  type A2AIngressRequestRecord,
  type A2AIngressSnapshot,
} from './a2aIngressPanelState';

interface A2AIngressPanelProps {
  isConnected: boolean;
  request: <T = unknown>(method: string, params?: Record<string, unknown>) => Promise<T>;
}

const STATE_BADGE_CLASS: Record<A2AIngressSnapshot['state'], string> = {
  disabled: 'bg-secondary text-text-muted',
  starting: 'bg-accent/15 text-accent',
  running: 'bg-ok/15 text-ok',
  stopping: 'bg-warn/15 text-warn',
  error: 'bg-danger/15 text-danger',
};

const REQUEST_BADGE_CLASS: Record<A2AIngressRequestRecord['status'], string> = {
  processing: 'bg-accent/15 text-accent',
  completed: 'bg-ok/15 text-ok',
  failed: 'bg-danger/15 text-danger',
  canceled: 'bg-secondary text-text-muted',
};

function errorMessage(error: unknown): string {
  return (error as WebError)?.message || String(error);
}

export function A2AIngressPanel({ isConnected, request }: A2AIngressPanelProps) {
  const { t, i18n } = useTranslation();
  const [activeTab, setActiveTab] = useState<'config' | 'outbound' | 'history'>('config');
  const [snapshot, setSnapshot] = useState<A2AIngressSnapshot | null>(null);
  const [draft, setDraft] = useState<A2AIngressDraft | null>(null);
  const [loading, setLoading] = useState(false);
  const [operation, setOperation] = useState<'save' | 'apply' | 'enable' | 'disable' | 'reload' | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [isDirty, setIsDirty] = useState(false);
  const [history, setHistory] = useState<A2AIngressRequestRecord[]>([]);
  const [historyTotal, setHistoryTotal] = useState(0);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const responseGenerationRef = useRef(0);
  const historyResponseGenerationRef = useRef(0);

  const acceptSnapshot = useCallback(
    (payload: unknown) => {
      const next = normalizeA2AIngressSnapshot(payload);
      if (!next) throw new Error(t('a2aIngress.errors.invalidResponse'));
      setSnapshot(next);
      setDraft(draftFromA2AIngressSnapshot(next));
      setIsDirty(false);
      return next;
    },
    [t],
  );

  const refresh = useCallback(async (showLoading = true) => {
    if (!isConnected) return;
    const responseGeneration = ++responseGenerationRef.current;
    if (showLoading) setLoading(true);
    try {
      const payload = await request('a2a.ingress.get');
      if (!shouldAcceptA2AIngressResponse(responseGeneration, responseGenerationRef.current)) return;
      acceptSnapshot(payload);
      setError(null);
    } catch (refreshError) {
      if (!shouldAcceptA2AIngressResponse(responseGeneration, responseGenerationRef.current)) return;
      setError(errorMessage(refreshError));
    } finally {
      if (showLoading && shouldAcceptA2AIngressResponse(responseGeneration, responseGenerationRef.current)) setLoading(false);
    }
  }, [acceptSnapshot, isConnected, request]);

  useEffect(() => {
    void refresh();
  }, [refresh]);
  useEffect(() => {
    if (!isA2AIngressTransitioning(snapshot?.state)) return;
    const timer = window.setInterval(() => void refresh(false), 1200);
    return () => window.clearInterval(timer);
  }, [refresh, snapshot?.state]);

  const refreshHistory = useCallback(async (showLoading = true) => {
    if (!isConnected) return;
    const responseGeneration = ++historyResponseGenerationRef.current;
    if (showLoading) setHistoryLoading(true);
    try {
      const payload = await request('a2a.ingress.history', { limit: 200 });
      if (!shouldAcceptA2AIngressResponse(responseGeneration, historyResponseGenerationRef.current)) return;
      const next = normalizeA2AIngressHistory(payload);
      if (!next) throw new Error(t('a2aIngress.errors.invalidHistoryResponse'));
      setHistory(next.items);
      setHistoryTotal(next.total);
      setHistoryError(null);
    } catch (historyRequestError) {
      if (!shouldAcceptA2AIngressResponse(responseGeneration, historyResponseGenerationRef.current)) return;
      setHistoryError(errorMessage(historyRequestError));
    } finally {
      if (showLoading && shouldAcceptA2AIngressResponse(responseGeneration, historyResponseGenerationRef.current)) setHistoryLoading(false);
    }
  }, [isConnected, request, t]);

  useEffect(() => {
    if (activeTab !== 'history' || !isConnected) return;
    void refreshHistory();
    const timer = window.setInterval(() => void refreshHistory(false), 2000);
    return () => window.clearInterval(timer);
  }, [activeTab, isConnected, refreshHistory]);

  const updateDraft = <K extends keyof A2AIngressDraft>(field: K, value: A2AIngressDraft[K]) => {
    setDraft(current => (current ? { ...current, [field]: value } : current));
    setIsDirty(true);
    setNotice(null);
  };

  const save = async (apply: boolean) => {
    if (!draft || operation || !isConnected) return;
    const invalidField = validateA2AIngressDraft(draft);
    if (invalidField) {
      setError(t('a2aIngress.errors.invalidField', { field: t(`a2aIngress.fields.${invalidField}`) }));
      return;
    }
    setOperation(apply ? 'apply' : 'save');
    responseGenerationRef.current += 1;
    setLoading(false);
    setError(null);
    try {
      acceptSnapshot(await request('a2a.ingress.update', { config: toA2AIngressPatch(draft), apply }));
      setNotice(t(apply ? 'a2aIngress.savedAndApplied' : 'a2aIngress.saved'));
    } catch (saveError) {
      const failedSnapshot = normalizeA2AIngressSnapshot((saveError as WebError).payload);
      if (failedSnapshot) acceptSnapshot(failedSnapshot);
      setError(errorMessage(saveError));
      if (!failedSnapshot) void refresh();
    } finally {
      setOperation(null);
    }
  };

  const runOperation = async (nextOperation: 'enable' | 'disable' | 'reload') => {
    if (operation || !isConnected) return;
    setOperation(nextOperation);
    responseGenerationRef.current += 1;
    setLoading(false);
    setError(null);
    setNotice(null);
    try {
      acceptSnapshot(await request(`a2a.ingress.${nextOperation}`));
    } catch (operationError) {
      const failedSnapshot = normalizeA2AIngressSnapshot((operationError as WebError).payload);
      if (failedSnapshot) acceptSnapshot(failedSnapshot);
      setError(errorMessage(operationError));
      if (!failedSnapshot) void refresh();
    } finally {
      setOperation(null);
    }
  };

  const busy = operation !== null || isA2AIngressTransitioning(snapshot?.state);
  const canEnable = canOperateA2AIngress(snapshot, isConnected, busy, isDirty, 'enable');
  const canDisable = canOperateA2AIngress(snapshot, isConnected, busy, isDirty, 'disable');
  const canReload = canOperateA2AIngress(snapshot, isConnected, busy, isDirty, 'reload');

  return (
    <div className="flex-1 min-h-0">
      <div className="card main-panel-card w-full h-full flex flex-col overflow-hidden">
        <div className="mb-1 flex shrink-0 flex-wrap items-start justify-between gap-4">
          <div>
            <h2 className="text-lg font-semibold">{t('a2aIngress.title')}</h2>
            <p className="text-sm text-text-muted mt-1">{t('a2aIngress.subtitle')}</p>
          </div>
          {activeTab === 'config' ? <div className="flex flex-wrap gap-2">
            <button type="button" className="btn secondary" onClick={() => void refresh()} disabled={!isConnected || loading || busy}>
              {loading ? t('common.refreshing') : t('common.refresh')}
            </button>
            <button type="button" className="btn primary" onClick={() => void runOperation('enable')} disabled={!canEnable}>
              {operation === 'enable' ? t('a2aIngress.enabling') : t('a2aIngress.enable')}
            </button>
            <button type="button" className="btn secondary" onClick={() => void runOperation('disable')} disabled={!canDisable}>
              {operation === 'disable' ? t('a2aIngress.disabling') : t('a2aIngress.disable')}
            </button>
            <button type="button" className="btn secondary" onClick={() => void runOperation('reload')} disabled={!canReload}>
              {operation === 'reload' ? t('a2aIngress.reloading') : t('a2aIngress.reload')}
            </button>
          </div> : activeTab === 'history' ? <button type="button" className="btn secondary" onClick={() => void refreshHistory()} disabled={!isConnected || historyLoading}>
            {historyLoading ? t('common.refreshing') : t('common.refresh')}
          </button> : null}
        </div>
        <div className="app-subtabs shrink-0" role="tablist" aria-label={t('a2aIngress.tabs.ariaLabel')}>
          {(['config', 'outbound', 'history'] as const).map(tab => (
            <button
              key={tab}
              type="button"
              role="tab"
              id={`a2a-ingress-tab-${tab}`}
              aria-controls={`a2a-ingress-panel-${tab}`}
              aria-selected={activeTab === tab}
              tabIndex={activeTab === tab ? 0 : -1}
              className={`app-subtabs__tab${activeTab === tab ? ' app-subtabs__tab--active' : ''}`}
              onClick={() => setActiveTab(tab)}
            >
              {t(`a2aIngress.tabs.${tab}`)}
            </button>
          ))}
        </div>
        {activeTab === 'config' ? <div id="a2a-ingress-panel-config" role="tabpanel" aria-labelledby="a2a-ingress-tab-config" className="flex min-h-0 flex-1 flex-col gap-5 overflow-auto pr-1 pt-1">
        {!isConnected && <Alert tone="warn">{t('a2aIngress.disconnected')}</Alert>}
        {error && <Alert tone="danger">{error}</Alert>}
        {notice && <Alert tone="ok">{notice}</Alert>}
        {isDirty && <Alert tone="warn">{t('a2aIngress.saveBeforeLifecycleAction')}</Alert>}
        <section className="rounded-xl border border-border bg-panel-strong/60 p-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <h3 className="text-sm font-semibold text-text">{t('a2aIngress.status.title')}</h3>
              <p className="mt-1 text-xs text-text-muted">{t('a2aIngress.status.configRevision', { revision: snapshot?.config_revision ?? '-' })}</p>
            </div>
            {snapshot && (
              <span className={`rounded-full px-3 py-1 text-sm font-medium ${STATE_BADGE_CLASS[snapshot.state]}`}>
                {t(`a2aIngress.states.${snapshot.state}`)}
              </span>
            )}
          </div>
          <div className="mt-4 grid gap-4 md:grid-cols-2">
            <AddressCard label={t('a2aIngress.status.effectiveRpcUrl')} value={snapshot?.effective_rpc_url} emptyText={t('a2aIngress.status.notListening')} />
            <AddressCard label={t('a2aIngress.status.effectiveCardUrl')} value={snapshot?.effective_card_url} emptyText={t('a2aIngress.status.notListening')} />
          </div>
          {snapshot?.last_error && (
            <Alert tone="danger" className="mt-4">
              {snapshot.last_error}
            </Alert>
          )}
          {snapshot?.exposure_warning && (
            <Alert tone="warn" className="mt-4">
              {snapshot.exposure_warning}
            </Alert>
          )}
        </section>
        {draft && (
          <section className="rounded-xl border border-border bg-panel-strong/60 p-4">
            <div className="flex flex-wrap items-center justify-between gap-4">
              <div>
                <h3 className="text-sm font-semibold text-text">{t('a2aIngress.config.title')}</h3>
                <p className="mt-1 text-xs text-text-muted">{t('a2aIngress.config.description')}</p>
              </div>
              <div className="flex gap-2">
                <button type="button" className="btn secondary" onClick={() => void save(false)} disabled={!isConnected || busy}>
                  {operation === 'save' ? t('common.saving') : t('a2aIngress.save')}
                </button>
                <button type="button" className="btn primary" onClick={() => void save(true)} disabled={!isConnected || busy}>
                  {operation === 'apply' ? t('a2aIngress.applying') : t('a2aIngress.saveAndApply')}
                </button>
              </div>
            </div>
            <div className="mt-4 grid gap-4 md:grid-cols-2">
              <TextField label={t('a2aIngress.fields.host')} value={draft.host} onChange={value => updateDraft('host', value)} disabled={busy} />
              <TextField
                label={t('a2aIngress.fields.port')}
                value={draft.port}
                onChange={value => updateDraft('port', value)}
                disabled={busy}
                type="number"
                min={1}
                max={65535}
              />
              <TextField label={t('a2aIngress.fields.rpc_path')} value={draft.rpc_path} onChange={value => updateDraft('rpc_path', value)} disabled={busy} />
              <TextField label={t('a2aIngress.fields.card_path')} value={draft.card_path} onChange={value => updateDraft('card_path', value)} disabled={busy} />
              <TextField
                label={t('a2aIngress.fields.extended_card_path')}
                value={draft.extended_card_path}
                onChange={value => updateDraft('extended_card_path', value)}
                disabled={busy}
              />
              <TextField
                label={t('a2aIngress.fields.protocol_version')}
                value={draft.protocol_version}
                onChange={value => updateDraft('protocol_version', value)}
                disabled={busy}
              />
              <TextField label={t('a2aIngress.fields.app_name')} value={draft.app_name} onChange={value => updateDraft('app_name', value)} disabled={busy} />
              <TextField
                label={t('a2aIngress.fields.app_version')}
                value={draft.app_version}
                onChange={value => updateDraft('app_version', value)}
                disabled={busy}
              />
              <label className="md:col-span-2">
                <span className="text-xs font-medium text-text-muted">{t('a2aIngress.fields.app_description')}</span>
                <textarea
                  className="mt-2 min-h-20 w-full resize-y rounded-md border border-border bg-bg px-3 py-2 text-sm text-text outline-none focus:border-accent"
                  value={draft.app_description}
                  onChange={event => updateDraft('app_description', event.target.value)}
                  disabled={busy}
                />
              </label>
              <label className="flex items-center gap-3 rounded-lg border border-border bg-bg px-3 py-3 md:col-span-2">
                <input
                  type="checkbox"
                  checked={draft.expose_reasoning}
                  onChange={event => updateDraft('expose_reasoning', event.target.checked)}
                  disabled={busy}
                  className="h-4 w-4 accent-[var(--color-accent)]"
                />
                <span className="text-sm text-text">{t('a2aIngress.fields.expose_reasoning')}</span>
              </label>
            </div>
          </section>
        )}
        </div> : activeTab === 'outbound' ? <div id="a2a-ingress-panel-outbound" role="tabpanel" aria-labelledby="a2a-ingress-tab-outbound" className="flex min-h-0 flex-1 flex-col pt-1"><A2AOutboundPanel isConnected={isConnected} request={request} /></div> : <div id="a2a-ingress-panel-history" role="tabpanel" aria-labelledby="a2a-ingress-tab-history" className="flex min-h-0 flex-1 flex-col gap-3 pt-1">
          {!isConnected && <Alert tone="warn">{t('a2aIngress.disconnected')}</Alert>}
          {historyError && <Alert tone="danger">{historyError}</Alert>}
          <section className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-xl border border-border bg-panel-strong/60">
            <div className="flex shrink-0 flex-wrap items-center justify-between gap-3 border-b border-border px-4 py-3">
              <div>
                <h3 className="text-sm font-semibold text-text">{t('a2aIngress.history.title')}</h3>
                <p className="mt-1 text-xs text-text-muted">{t('a2aIngress.history.description')}</p>
              </div>
              <span className="text-xs text-text-muted">{t('a2aIngress.history.total', { count: historyTotal })}</span>
            </div>
            {historyLoading && history.length === 0 ? (
              <div className="grid min-h-0 flex-1 place-items-center px-4 py-12 text-center text-sm text-text-muted">{t('common.loading')}</div>
            ) : history.length === 0 ? (
              <div className="grid min-h-0 flex-1 place-items-center px-4 py-12 text-center">
                <div>
                  <div className="text-sm font-medium text-text">{t('a2aIngress.history.empty')}</div>
                  <div className="mt-1 text-xs text-text-muted">{t('a2aIngress.history.emptyDescription')}</div>
                </div>
              </div>
            ) : (
              <div className="min-h-0 flex-1 overflow-auto">
                <table className="w-full min-w-[920px] text-left text-sm">
                  <thead className="sticky top-0 z-10 bg-bg text-xs text-text-muted">
                    <tr>
                      <th className="px-4 py-3 font-medium">{t('a2aIngress.history.columns.status')}</th>
                      <th className="px-4 py-3 font-medium">{t('a2aIngress.history.columns.operation')}</th>
                      <th className="px-4 py-3 font-medium">{t('a2aIngress.history.columns.requestId')}</th>
                      <th className="px-4 py-3 font-medium">{t('a2aIngress.history.columns.contextId')}</th>
                      <th className="px-4 py-3 font-medium">{t('a2aIngress.history.columns.startedAt')}</th>
                      <th className="px-4 py-3 font-medium">{t('a2aIngress.history.columns.duration')}</th>
                      <th className="px-4 py-3 font-medium">{t('a2aIngress.history.columns.error')}</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-border">
                    {history.map(item => (
                      <tr key={item.request_id} className="text-text">
                        <td className="px-4 py-3"><span className={`rounded-full px-2.5 py-1 text-xs font-medium ${REQUEST_BADGE_CLASS[item.status]}`}>{t(`a2aIngress.history.statuses.${item.status}`)}</span></td>
                        <td className="px-4 py-3">{t(`a2aIngress.history.operations.${item.operation}`, { defaultValue: item.operation })}</td>
                        <td className="max-w-48 truncate px-4 py-3 font-mono text-xs" title={item.request_id}>{item.request_id}</td>
                        <td className="max-w-48 truncate px-4 py-3 font-mono text-xs" title={item.context_id || undefined}>{item.context_id || '-'}</td>
                        <td className="whitespace-nowrap px-4 py-3">{formatTimestamp(item.started_at, i18n.language)}</td>
                        <td className="whitespace-nowrap px-4 py-3">{formatDuration(item.duration_ms, t('a2aIngress.history.processing'))}</td>
                        <td className="max-w-64 px-4 py-3 text-xs text-danger"><span className="line-clamp-2" title={item.error || undefined}>{item.error || '-'}</span></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>
        </div>}
      </div>
    </div>
  );
}

function formatTimestamp(timestamp: number, language: string): string {
  return new Date(timestamp * 1000).toLocaleString(language.startsWith('zh') ? 'zh-CN' : 'en-US', { hour12: false });
}

function formatDuration(durationMs: number | null, processingText: string): string {
  if (durationMs === null) return processingText;
  if (durationMs < 1000) return `${durationMs} ms`;
  return `${(durationMs / 1000).toFixed(2)} s`;
}

function Alert({ tone, className = '', children }: { tone: 'warn' | 'danger' | 'ok'; className?: string; children: ReactNode }) {
  const toneClass = tone === 'danger' ? 'border-danger/30 bg-danger/10' : tone === 'warn' ? 'border-warn/30 bg-warn/10' : 'border-ok/30 bg-ok/10';
  return <div className={`rounded-xl border px-4 py-3 text-sm text-text ${toneClass} ${className}`}>{children}</div>;
}

function AddressCard({ label, value, emptyText }: { label: string; value: string | null | undefined; emptyText: string }) {
  return (
    <div className="rounded-lg border border-border bg-bg px-3 py-3">
      <div className="text-xs text-text-muted">{label}</div>
      <div className="mt-2 break-all font-mono text-sm text-text">{value || emptyText}</div>
    </div>
  );
}
function TextField({
  label,
  value,
  onChange,
  disabled,
  type = 'text',
  min,
  max,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  disabled: boolean;
  type?: 'text' | 'number';
  min?: number;
  max?: number;
}) {
  return (
    <label>
      <span className="text-xs font-medium text-text-muted">{label}</span>
      <input
        className="mt-2 w-full rounded-md border border-border bg-bg px-3 py-2 text-sm text-text outline-none focus:border-accent"
        type={type}
        value={value}
        onChange={event => onChange(event.target.value)}
        disabled={disabled}
        min={min}
        max={max}
      />
    </label>
  );
}
