import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { webClient } from '../services/webClient';
import { useExtSettingsStore } from '../stores/extSettingsStore';

interface CronJob {
  id: string;
  name: string;
  enabled: boolean;
  cron_expr: string;
  timezone: string;
  description?: string;
  user_id?: string;
  group_id?: string;
  bot_id?: string;
}

const TIMEZONES = ['Asia/Shanghai', 'Asia/Tokyo', 'UTC', 'America/New_York', 'Europe/London'];

/**
 * 定时任务面板（内嵌于 claw_manager 用户面的「定时任务」标签，view=schedule）。
 * 走 web_enterprise 的 webClient（WS-RPC cron.job.*）；归属 id 取自 extSettings(URL query 注入)，
 * 创建时带上、列表时按 user_id+bot_id 筛选 —— 只看自己在该 bot 下的任务。
 */
export function CronView() {
  const { t } = useTranslation();
  const { userId, groupId, botId } = useExtSettingsStore();
  const [jobs, setJobs] = useState<CronJob[]>([]);
  const [ready, setReady] = useState(false);
  const [error, setError] = useState('');
  const [showForm, setShowForm] = useState(false);

  const load = useCallback(async () => {
    try {
      const res = await webClient.request<{ jobs: CronJob[] }>('cron.job.list', {
        user_id: userId, group_id: groupId, bot_id: botId,
      });
      setJobs(res.jobs ?? []);
      setError('');
    } catch (e) {
      setError((e as Error)?.message || t('cron.loadFailed'));
    }
  }, [userId, groupId, botId, t]);

  useEffect(() => {
    let alive = true;
    webClient.connect({ userId, groupId, botId })
      .then(() => { if (alive) { setReady(true); void load(); } })
      .catch((e) => { if (alive) setError((e as Error)?.message || t('cron.loadFailed')); });
    return () => { alive = false; };
  }, [userId, groupId, botId, load, t]);

  async function toggle(job: CronJob) {
    try { await webClient.request('cron.job.toggle', { id: job.id, enabled: !job.enabled }); await load(); }
    catch (e) { setError((e as Error)?.message || ''); }
  }
  async function remove(job: CronJob) {
    if (!window.confirm(t('cron.confirmDelete', { name: job.name }))) return;
    try { await webClient.request('cron.job.delete', { id: job.id }); await load(); }
    catch (e) { setError((e as Error)?.message || ''); }
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', padding: 16, gap: 12, overflow: 'auto' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <h2 style={{ fontSize: 18, fontWeight: 600 }}>{t('cron.title')}</h2>
        <div style={{ display: 'flex', gap: 8 }}>
          <button className="px-3 py-1 rounded border" onClick={() => void load()} disabled={!ready}>{t('cron.refresh')}</button>
          <button className="px-3 py-1 rounded bg-blue-600 text-white" onClick={() => setShowForm((v) => !v)} disabled={!ready}>{t('cron.new')}</button>
        </div>
      </div>

      {!ready && <div style={{ color: '#888' }}>{t('cron.connecting')}</div>}
      {error && <div style={{ color: '#c00', fontSize: 13 }}>{error}</div>}

      {showForm && <CronForm onCreated={() => { setShowForm(false); void load(); }} onCancel={() => setShowForm(false)} ids={{ userId, groupId, botId }} />}

      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 14 }}>
        <thead>
          <tr style={{ textAlign: 'left', borderBottom: '1px solid #ddd' }}>
            <th style={{ padding: 6 }}>{t('cron.name')}</th>
            <th style={{ padding: 6 }}>{t('cron.cronExpr')}</th>
            <th style={{ padding: 6 }}>{t('cron.timezone')}</th>
            <th style={{ padding: 6 }}>{t('cron.enabled')}</th>
            <th style={{ padding: 6 }}>{t('cron.actions')}</th>
          </tr>
        </thead>
        <tbody>
          {jobs.map((j) => (
            <tr key={j.id} style={{ borderBottom: '1px solid #f0f0f0' }}>
              <td style={{ padding: 6 }}>{j.name}<div style={{ fontSize: 12, color: '#888' }}>{j.description}</div></td>
              <td style={{ padding: 6, fontFamily: 'monospace' }}>{j.cron_expr}</td>
              <td style={{ padding: 6 }}>{j.timezone}</td>
              <td style={{ padding: 6 }}>
                <button className="px-2 py-0.5 rounded border" onClick={() => void toggle(j)}>{j.enabled ? t('cron.on') : t('cron.off')}</button>
              </td>
              <td style={{ padding: 6 }}>
                <button className="px-2 py-0.5 rounded border text-red-600" onClick={() => void remove(j)}>{t('cron.delete')}</button>
              </td>
            </tr>
          ))}
          {ready && jobs.length === 0 && <tr><td colSpan={5} style={{ padding: 12, color: '#888' }}>{t('cron.empty')}</td></tr>}
        </tbody>
      </table>
    </div>
  );
}

function CronForm({ ids, onCreated, onCancel }: {
  ids: { userId: string; groupId: string; botId: string };
  onCreated: () => void; onCancel: () => void;
}) {
  const { t } = useTranslation();
  const [name, setName] = useState('');
  const [cronExpr, setCronExpr] = useState('0 9 * * *');
  const [timezone, setTimezone] = useState('Asia/Shanghai');
  const [description, setDescription] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');

  async function submit() {
    setBusy(true); setErr('');
    try {
      await webClient.request('cron.job.create', {
        name: name.trim(), cron_expr: cronExpr.trim(), timezone, targets: 'web',
        description: description.trim(),
        user_id: ids.userId, group_id: ids.groupId, bot_id: ids.botId,
      });
      onCreated();
    } catch (e) { setErr((e as Error)?.message || t('cron.saveFailed')); }
    finally { setBusy(false); }
  }

  const field = { display: 'block', width: '100%', padding: '6px 8px', border: '1px solid #ccc', borderRadius: 6, marginTop: 4 } as const;
  const label = { fontSize: 13, color: '#555', marginTop: 10, display: 'block' } as const;

  return (
    <div style={{ border: '1px solid #ddd', borderRadius: 8, padding: 12, background: 'rgba(0,0,0,0.02)' }}>
      <label style={label}>{t('cron.name')}</label>
      <input style={field} value={name} placeholder={t('cron.namePlaceholder')} onChange={(e) => setName(e.target.value)} />
      <label style={label}>{t('cron.description')}</label>
      <input style={field} value={description} placeholder={t('cron.descPlaceholder')} onChange={(e) => setDescription(e.target.value)} />
      <label style={label}>{t('cron.cronExpr')}</label>
      <input style={{ ...field, fontFamily: 'monospace' }} value={cronExpr} onChange={(e) => setCronExpr(e.target.value)} />
      <div style={{ fontSize: 12, color: '#888', marginTop: 4 }}>{t('cron.cronExprHint')}</div>
      <label style={label}>{t('cron.timezone')}</label>
      <select style={field} value={timezone} onChange={(e) => setTimezone(e.target.value)}>
        {TIMEZONES.map((tz) => <option key={tz} value={tz}>{tz}</option>)}
      </select>
      {err && <div style={{ color: '#c00', fontSize: 13, marginTop: 8 }}>{err}</div>}
      <div style={{ marginTop: 12, display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
        <button className="px-3 py-1 rounded border" onClick={onCancel}>{t('cron.cancel')}</button>
        <button className="px-3 py-1 rounded bg-blue-600 text-white" disabled={busy || !name.trim() || !cronExpr.trim() || !description.trim()} onClick={() => void submit()}>{t('cron.create')}</button>
      </div>
    </div>
  );
}
