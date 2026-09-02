import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ChevronDown } from 'lucide-react';
import type { WebError } from '../../types/websocket';
import { Switch } from '../Switch';
import {
  normalizeA2AOutboundAgent,
  normalizeA2AOutboundDiscovery,
  normalizeA2AOutboundList,
  normalizeA2AOutboundSettings,
  shouldAcceptA2AOutboundResponse,
  type A2AOutboundAgent,
  type A2AOutboundAvailability,
  type A2AOutboundDiscovery,
} from './a2aOutboundPanelState';

interface Props {
  isConnected: boolean;
  request: <T = unknown>(method: string, params?: Record<string, unknown>) => Promise<T>;
}

const BADGE: Record<A2AOutboundAvailability, string> = {
  available: 'bg-ok/15 text-ok', unreachable: 'bg-danger/15 text-danger',
  incompatible: 'bg-danger/15 text-danger', review_required: 'bg-warn/15 text-warn',
};

const errorMessage = (error: unknown): string => (error as WebError)?.message || String(error);

export function A2AOutboundPanel({ isConnected, request }: Props) {
  const { t, i18n } = useTranslation();
  const [url, setUrl] = useState('');
  const [discovery, setDiscovery] = useState<A2AOutboundDiscovery | null>(null);
  const [agents, setAgents] = useState<A2AOutboundAgent[]>([]);
  const [allowLoopbackHttp, setAllowLoopbackHttp] = useState(false);
  const [savedAllowLoopbackHttp, setSavedAllowLoopbackHttp] = useState(false);
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [displayName, setDisplayName] = useState('');
  const [credential, setCredential] = useState('');
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [editing, setEditing] = useState<{ agentId: string; displayName: string; connectTimeout: string; syncWait: string; credential: string; clearCredential: boolean } | null>(null);
  const generationRef = useRef(0);

  const refresh = useCallback(async () => {
    if (!isConnected) return;
    const generation = ++generationRef.current;
    try {
      const [payload, rawSettings] = await Promise.all([
        request('a2a.outbound.list'),
        request('a2a.outbound.settings.get'),
      ]);
      if (!shouldAcceptA2AOutboundResponse(generation, generationRef.current)) return;
      const next = normalizeA2AOutboundList(payload);
      const settings = normalizeA2AOutboundSettings(rawSettings);
      if (!next || !settings) throw new Error(t('a2aIngress.outbound.errors.invalidResponse'));
      setAgents(next);
      setAllowLoopbackHttp(settings.allow_loopback_http);
      setSavedAllowLoopbackHttp(settings.allow_loopback_http);
      setError(null);
    } catch (nextError) {
      if (shouldAcceptA2AOutboundResponse(generation, generationRef.current)) setError(errorMessage(nextError));
    }
  }, [isConnected, request, t]);

  useEffect(() => { void refresh(); }, [refresh]);

  const discover = async () => {
    if (!url.trim() || busy || !isConnected) return;
    setBusy('discover'); setError(null); setNotice(null); setDiscovery(null);
    try {
      const next = normalizeA2AOutboundDiscovery(await request('a2a.outbound.discover', { url: url.trim() }));
      if (!next) throw new Error(t('a2aIngress.outbound.errors.invalidResponse'));
      setDiscovery(next); setDisplayName(next.agent.name); setCredential('');
      setNotice(t('a2aIngress.outbound.discovery.previewReady'));
    } catch (nextError) { setError(errorMessage(nextError)); }
    finally { setBusy(null); }
  };

  const updateAllowLoopbackHttp = async (nextEnabled: boolean) => {
    if (busy || !isConnected) return;
    const generation = ++generationRef.current;
    const previous = savedAllowLoopbackHttp;
    setAllowLoopbackHttp(nextEnabled);
    setBusy('settings'); setError(null); setNotice(null);
    try {
      const settings = normalizeA2AOutboundSettings(await request('a2a.outbound.settings.update', {
        allow_loopback_http: nextEnabled,
      }));
      if (!settings) throw new Error(t('a2aIngress.outbound.errors.invalidResponse'));
      if (!shouldAcceptA2AOutboundResponse(generation, generationRef.current)) return;
      setAllowLoopbackHttp(settings.allow_loopback_http);
      setSavedAllowLoopbackHttp(settings.allow_loopback_http);
      setNotice(t('a2aIngress.outbound.localDebug.saved'));
    } catch (nextError) {
      if (!shouldAcceptA2AOutboundResponse(generation, generationRef.current)) return;
      setAllowLoopbackHttp(previous);
      setError(errorMessage(nextError));
    }
    finally { setBusy(null); }
  };

  const register = async () => {
    if (!discovery || busy || !isConnected) return;
    setBusy('register'); setError(null);
    try {
      const payload = await request('a2a.outbound.register', {
        discovery_id: discovery.discovery_id, display_name: displayName.trim() || discovery.agent.name,
        enabled: true, ...(credential ? { credential } : {}),
      });
      const created = normalizeA2AOutboundAgent(payload);
      if (!created) throw new Error(t('a2aIngress.outbound.errors.invalidResponse'));
      generationRef.current += 1; setAgents(current => [created, ...current]); setDiscovery(null); setCredential('');
      setNotice(t('a2aIngress.outbound.registered'));
    } catch (nextError) { setError(errorMessage(nextError)); }
    finally { setBusy(null); }
  };

  const operate = async (agent: A2AOutboundAgent, operation: 'toggle' | 'refresh' | 'confirm' | 'reject' | 'delete') => {
    if (busy || !isConnected) return;
    if (operation === 'delete' && !window.confirm(t('a2aIngress.outbound.deleteConfirm'))) return;
    setBusy(`${operation}:${agent.agent_id}`); setError(null); setNotice(null); generationRef.current += 1;
    try {
      let payload: unknown;
      if (operation === 'toggle') payload = await request('a2a.outbound.update', { agent_id: agent.agent_id, enabled: !agent.enabled });
      else if (operation === 'refresh') payload = await request('a2a.outbound.refresh', { agent_id: agent.agent_id });
      else if (operation === 'confirm' || operation === 'reject') payload = await request('a2a.outbound.confirm_revision', { agent_id: agent.agent_id, accept: operation === 'confirm' });
      else payload = await request('a2a.outbound.delete', { agent_id: agent.agent_id });
      if (operation === 'delete') setAgents(current => current.filter(item => item.agent_id !== agent.agent_id));
      else {
        const updated = normalizeA2AOutboundAgent(payload);
        if (!updated) throw new Error(t('a2aIngress.outbound.errors.invalidResponse'));
        setAgents(current => current.map(item => item.agent_id === updated.agent_id ? updated : item));
      }
    } catch (nextError) { setError(errorMessage(nextError)); void refresh(); }
    finally { setBusy(null); }
  };

  const saveSettings = async (agent: A2AOutboundAgent) => {
    if (!editing || editing.agentId !== agent.agent_id || busy || !isConnected) return;
    const connectTimeout = Number(editing.connectTimeout);
    const syncWait = Number(editing.syncWait);
    if (!editing.displayName.trim() || !Number.isFinite(connectTimeout) || connectTimeout <= 0 || !Number.isFinite(syncWait) || syncWait <= 0) {
      setError(t('a2aIngress.outbound.errors.invalidSettings')); return;
    }
    setBusy(`save:${agent.agent_id}`); setError(null); generationRef.current += 1;
    try {
      const payload = await request('a2a.outbound.update', {
        agent_id: agent.agent_id, display_name: editing.displayName.trim(),
        connect_timeout_seconds: connectTimeout, sync_wait_seconds: syncWait,
        ...(editing.credential ? { credential: editing.credential } : {}),
        ...(editing.clearCredential ? { clear_credential: true } : {}),
      });
      const updated = normalizeA2AOutboundAgent(payload);
      if (!updated) throw new Error(t('a2aIngress.outbound.errors.invalidResponse'));
      setAgents(current => current.map(item => item.agent_id === updated.agent_id ? updated : item));
      setEditing(null); setNotice(t('a2aIngress.outbound.saved'));
    } catch (nextError) { setError(errorMessage(nextError)); void refresh(); }
    finally { setBusy(null); }
  };

  return <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-auto pr-1 pt-1">
    {!isConnected && <Notice tone="warn">{t('a2aIngress.disconnected')}</Notice>}
    {error && <Notice tone="danger">{error}</Notice>}
    {notice && <Notice tone="ok">{notice}</Notice>}
    <section className="rounded-xl border border-border bg-panel-strong/60 p-4">
      <h3 className="text-sm font-semibold text-text">{t('a2aIngress.outbound.discovery.title')}</h3>
      <p className="mt-1 text-xs text-text-muted">{t('a2aIngress.outbound.discovery.description')}</p>
      <div className="mt-4 flex gap-2">
        <input className="min-w-0 flex-1 rounded-md border border-border bg-bg px-3 py-2 text-sm text-text outline-none focus:border-accent" value={url} onChange={event => setUrl(event.target.value)} placeholder="https://agent.example.com" disabled={!!busy} />
        <button type="button" className="btn primary" onClick={() => void discover()} disabled={!isConnected || !!busy || !url.trim()}>{busy === 'discover' ? t('a2aIngress.outbound.discovery.discovering') : t('a2aIngress.outbound.discovery.action')}</button>
      </div>
      <div className="mt-4 border-t border-border pt-3">
        <button
          type="button"
          className="flex w-full items-center justify-between gap-3 text-left text-sm font-medium text-text-muted hover:text-text"
          aria-expanded={advancedOpen}
          onClick={() => setAdvancedOpen(open => !open)}
        >
          <span>{t('a2aIngress.outbound.discovery.advanced')}</span>
          <ChevronDown size={16} className={`shrink-0 transition-transform ${advancedOpen ? 'rotate-180' : ''}`} />
        </button>
        {advancedOpen && <div className="mt-3 rounded-lg border border-border bg-bg/60 p-3">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="min-w-0 flex-1">
              <div className="text-sm font-medium text-text">{t('a2aIngress.outbound.localDebug.allow')}</div>
              <p className="mt-1 text-xs text-text-muted">{t('a2aIngress.outbound.localDebug.description')}</p>
            </div>
            <Switch
              checked={allowLoopbackHttp}
              onChange={nextEnabled => void updateAllowLoopbackHttp(nextEnabled)}
              disabled={!isConnected || !!busy}
              title={t('a2aIngress.outbound.localDebug.allow')}
            />
          </div>
          {allowLoopbackHttp && <p className="mt-3 border-t border-warn/20 pt-3 text-xs text-warn">{t('a2aIngress.outbound.localDebug.warning')}</p>}
        </div>}
      </div>
    </section>
    {discovery && <section className="rounded-xl border border-accent/30 bg-accent/5 p-4">
      <div className="flex flex-wrap items-start justify-between gap-3"><div><h3 className="font-semibold text-text">{discovery.agent.name}</h3><p className="mt-1 text-sm text-text-muted">{discovery.agent.description || '-'}</p></div><span className="rounded-full bg-secondary px-2.5 py-1 text-xs text-text-muted">{t('a2aIngress.outbound.discovery.previewOnly')}</span></div>
      <div className="mt-4 grid gap-3 text-xs md:grid-cols-2"><Value label={t('a2aIngress.outbound.fields.version')} value={discovery.agent.version || '-'} /><Value label={t('a2aIngress.outbound.fields.endpoint')} value={discovery.agent.compatible_interfaces[0].url} /><Value label={t('a2aIngress.outbound.fields.protocol')} value={`${discovery.agent.compatible_interfaces[0].protocol_binding} ${discovery.agent.compatible_interfaces[0].protocol_version}`} /><Value label={t('a2aIngress.outbound.fields.expiresAt')} value={formatTime(discovery.expires_at, i18n.language)} /></div>
      <div className="mt-4 grid gap-3 md:grid-cols-2"><Input label={t('a2aIngress.outbound.fields.displayName')} value={displayName} onChange={setDisplayName} /><Input label={t('a2aIngress.outbound.fields.credential')} value={credential} onChange={setCredential} type="password" /></div>
      <div className="mt-4 flex justify-end gap-2"><button type="button" className="btn secondary" onClick={() => setDiscovery(null)} disabled={!!busy}>{t('common.cancel')}</button><button type="button" className="btn primary" onClick={() => void register()} disabled={!!busy}>{busy === 'register' ? t('a2aIngress.outbound.registering') : t('a2aIngress.outbound.register')}</button></div>
    </section>}
    <section className="rounded-xl border border-border bg-panel-strong/60">
      <div className="flex items-center justify-between border-b border-border px-4 py-3"><div><h3 className="text-sm font-semibold text-text">{t('a2aIngress.outbound.list.title')}</h3><p className="mt-1 text-xs text-text-muted">{t('a2aIngress.outbound.list.total', { count: agents.length })}</p></div><button type="button" className="btn secondary" onClick={() => void refresh()} disabled={!isConnected || !!busy}>{t('common.refresh')}</button></div>
      {agents.length === 0 ? <div className="px-4 py-12 text-center text-sm text-text-muted">{t('a2aIngress.outbound.list.empty')}</div> : <div className="divide-y divide-border">{agents.map(agent => <div key={agent.agent_id} className="p-4">
        <div className="flex flex-wrap items-start justify-between gap-3"><div><div className="flex items-center gap-2"><span className="font-medium text-text">{agent.display_name}</span><span className={`rounded-full px-2 py-0.5 text-xs ${BADGE[agent.availability]}`}>{t(`a2aIngress.outbound.availability.${agent.availability}`)}</span><span className="rounded-full bg-secondary px-2 py-0.5 text-xs text-text-muted">{agent.enabled ? t('a2aIngress.outbound.enabled') : t('a2aIngress.outbound.disabled')}</span></div><div className="mt-2 break-all font-mono text-xs text-text-muted">{agent.selected_interface.url}</div></div><div className="flex flex-wrap gap-2"><button type="button" className="btn secondary" onClick={() => setEditing({ agentId: agent.agent_id, displayName: agent.display_name, connectTimeout: String(agent.connect_timeout_seconds), syncWait: String(agent.sync_wait_seconds), credential: '', clearCredential: false })} disabled={!!busy}>{t('a2aIngress.outbound.settings')}</button><button type="button" className="btn secondary" onClick={() => void operate(agent, 'toggle')} disabled={!!busy || agent.availability === 'review_required'}>{agent.enabled ? t('a2aIngress.outbound.disable') : t('a2aIngress.outbound.enable')}</button><button type="button" className="btn secondary" onClick={() => void operate(agent, 'refresh')} disabled={!!busy}>{t('a2aIngress.outbound.refresh')}</button><button type="button" className="btn secondary text-danger" onClick={() => void operate(agent, 'delete')} disabled={!!busy}>{t('common.delete')}</button></div></div>
        <div className="mt-3 flex flex-wrap gap-x-5 gap-y-1 text-xs text-text-muted"><span>{t('a2aIngress.outbound.fields.revision')}: {agent.card_revision}</span><span>{t('a2aIngress.outbound.fields.credentialState')}: {agent.has_credential ? t('a2aIngress.outbound.configured') : t('a2aIngress.outbound.notConfigured')}</span><span>{t('a2aIngress.outbound.fields.lastChecked')}: {agent.last_checked_at ? formatTime(agent.last_checked_at, i18n.language) : '-'}</span></div>
        {agent.last_error_summary && <p className="mt-2 text-xs text-danger">{agent.last_error_summary}</p>}
        {editing?.agentId === agent.agent_id && <div className="mt-3 rounded-lg border border-border bg-bg p-3"><div className="grid gap-3 md:grid-cols-2"><Input label={t('a2aIngress.outbound.fields.displayName')} value={editing.displayName} onChange={value => setEditing(current => current ? { ...current, displayName: value } : current)} /><Input label={t('a2aIngress.outbound.fields.connectTimeout')} value={editing.connectTimeout} onChange={value => setEditing(current => current ? { ...current, connectTimeout: value } : current)} /><Input label={t('a2aIngress.outbound.fields.syncWait')} value={editing.syncWait} onChange={value => setEditing(current => current ? { ...current, syncWait: value } : current)} /><Input label={t('a2aIngress.outbound.fields.replaceCredential')} value={editing.credential} onChange={value => setEditing(current => current ? { ...current, credential: value, clearCredential: false } : current)} type="password" /></div><label className="mt-3 flex items-center gap-2 text-sm text-text"><input type="checkbox" checked={editing.clearCredential} onChange={event => setEditing(current => current ? { ...current, clearCredential: event.target.checked, credential: event.target.checked ? '' : current.credential } : current)} />{t('a2aIngress.outbound.clearCredential')}</label><div className="mt-3 flex justify-end gap-2"><button type="button" className="btn secondary" onClick={() => setEditing(null)}>{t('common.cancel')}</button><button type="button" className="btn primary" onClick={() => void saveSettings(agent)} disabled={!!busy}>{t('common.save')}</button></div></div>}
        {agent.availability === 'review_required' && <div className="mt-3 rounded-lg border border-warn/30 bg-warn/10 p-3 text-sm"><p>{t('a2aIngress.outbound.reviewRequired')}</p><PendingDiff agent={agent} label={t('a2aIngress.outbound.endpointChange')} /><div className="mt-3 flex gap-2"><button type="button" className="btn primary" onClick={() => void operate(agent, 'confirm')} disabled={!!busy}>{t('a2aIngress.outbound.confirm')}</button><button type="button" className="btn secondary" onClick={() => void operate(agent, 'reject')} disabled={!!busy}>{t('a2aIngress.outbound.reject')}</button></div></div>}
      </div>)}</div>}
    </section>
  </div>;
}

function formatTime(value: string, language: string): string { return new Date(value).toLocaleString(language.startsWith('zh') ? 'zh-CN' : 'en-US', { hour12: false }); }
function Notice({ tone, children }: { tone: 'warn' | 'danger' | 'ok'; children: React.ReactNode }) { const style = tone === 'danger' ? 'border-danger/30 bg-danger/10' : tone === 'warn' ? 'border-warn/30 bg-warn/10' : 'border-ok/30 bg-ok/10'; return <div className={`rounded-xl border px-4 py-3 text-sm text-text ${style}`}>{children}</div>; }
function Value({ label, value }: { label: string; value: string }) { return <div><div className="text-text-muted">{label}</div><div className="mt-1 break-all text-text">{value}</div></div>; }
function Input({ label, value, onChange, type = 'text' }: { label: string; value: string; onChange: (value: string) => void; type?: 'text' | 'password' }) { return <label><span className="text-xs font-medium text-text-muted">{label}</span><input type={type} className="mt-2 w-full rounded-md border border-border bg-bg px-3 py-2 text-sm text-text outline-none focus:border-accent" value={value} onChange={event => onChange(event.target.value)} /></label>; }
function PendingDiff({ agent, label }: { agent: A2AOutboundAgent; label: string }) { const pending = agent.pending_revision?.selected_interface as Record<string, unknown> | undefined; const nextUrl = typeof pending?.url === 'string' ? pending.url : ''; if (!nextUrl || nextUrl === agent.selected_interface.url) return null; return <div className="mt-2 grid gap-1 rounded-md bg-bg/70 p-2 text-xs"><span className="font-medium text-text">{label}</span><span className="break-all text-text-muted">{agent.selected_interface.url}</span><span className="break-all text-warn">→ {nextUrl}</span></div>; }
