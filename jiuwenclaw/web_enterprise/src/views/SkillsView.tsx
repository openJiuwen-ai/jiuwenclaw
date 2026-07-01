import { useCallback, useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { webClient } from '../services/webClient';
import { useExtSettingsStore } from '../stores/extSettingsStore';

interface SkillItem {
  name: string;
  description?: string;
  version?: string;
  author?: string;
  is_builtin?: boolean;
  is_builtin_source?: boolean;
  source?: string;
}
interface PluginItem {
  plugin_name: string;
  version?: string;
  skills?: string[];
  marketplace?: string;
}

/**
 * 技能面板（claw_manager 用户面「技能」标签，view=skills）。
 * 走 web_enterprise 的 webClient（WS-RPC skills.*，需 session_id，来自 connection.ack）。
 * 复用现有 skills.list/install/uninstall —— 不是自己设计的技能逻辑。
 */
export function SkillsView() {
  const { t } = useTranslation();
  const { userId, groupId, botId } = useExtSettingsStore();
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [skills, setSkills] = useState<SkillItem[]>([]);
  const [plugins, setPlugins] = useState<PluginItem[]>([]);
  const [search, setSearch] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState('');

  // 连接 + 从 connection.ack 拿 session_id
  useEffect(() => {
    let alive = true;
    const off = webClient.on('connection.ack', ({ payload }: { payload?: { session_id?: string } }) => {
      if (alive && payload?.session_id) setSessionId(payload.session_id);
    });
    webClient.connect({ userId, groupId, botId }).catch((e) => {
      if (alive) setError((e as Error)?.message || t('skills.loadFailed'));
    });
    return () => { alive = false; off(); };
  }, [userId, groupId, botId, t]);

  const load = useCallback(async (sid: string) => {
    try {
      const res = await webClient.request<{ skills?: SkillItem[]; plugins?: PluginItem[] }>(
        'skills.list', { with_installed: true, session_id: sid },
      );
      setSkills(res.skills ?? []);
      setPlugins(res.plugins ?? []);
      setError('');
    } catch (e) {
      setError((e as Error)?.message || t('skills.loadFailed'));
    }
  }, [t]);

  useEffect(() => { if (sessionId) void load(sessionId); }, [sessionId, load]);

  async function install(s: SkillItem) {
    if (!sessionId) return;
    setBusy(s.name);
    try {
      const r = await webClient.request<{ success: boolean; detail?: string; message?: string }>(
        'skills.install', { spec: `${s.name}@builtin`, force: false, session_id: sessionId },
      );
      if (!r.success) throw new Error(r.detail || r.message || t('skills.installFailed'));
      await load(sessionId);
    } catch (e) { setError((e as Error)?.message || t('skills.installFailed')); }
    finally { setBusy(''); }
  }

  async function uninstall(pluginName: string) {
    if (!sessionId) return;
    if (!window.confirm(t('skills.confirmUninstall', { name: pluginName }))) return;
    setBusy(pluginName);
    try {
      const r = await webClient.request<{ success: boolean; detail?: string; message?: string }>(
        'skills.uninstall', { name: pluginName, session_id: sessionId },
      );
      if (!r.success) throw new Error(r.detail || r.message || t('skills.uninstallFailed'));
      await load(sessionId);
    } catch (e) { setError((e as Error)?.message || t('skills.uninstallFailed')); }
    finally { setBusy(''); }
  }

  const installedNames = useMemo(
    () => new Set(plugins.flatMap((p) => p.skills ?? []).concat(plugins.map((p) => p.plugin_name))),
    [plugins],
  );
  const filtered = skills.filter((s) => {
    const q = search.trim().toLowerCase();
    return !q || s.name.toLowerCase().includes(q) || (s.description ?? '').toLowerCase().includes(q);
  });

  const card = { border: '1px solid #e5e7eb', borderRadius: 8, padding: 10, marginBottom: 8 } as const;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', padding: 16, gap: 12, overflow: 'auto' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8 }}>
        <h2 style={{ fontSize: 18, fontWeight: 600 }}>{t('skills.title')}</h2>
        <div style={{ display: 'flex', gap: 8 }}>
          <input style={{ padding: '6px 8px', border: '1px solid #ccc', borderRadius: 6, width: 200 }}
            placeholder={t('skills.search')} value={search} onChange={(e) => setSearch(e.target.value)} />
          <button className="px-3 py-1 rounded border" disabled={!sessionId} onClick={() => sessionId && void load(sessionId)}>{t('skills.refresh')}</button>
        </div>
      </div>

      {!sessionId && <div style={{ color: '#888' }}>{t('skills.connecting')}</div>}
      {error && <div style={{ color: '#c00', fontSize: 13 }}>{error}</div>}

      {/* 已安装 */}
      <div>
        <div style={{ fontSize: 13, color: '#555', fontWeight: 600, margin: '4px 0' }}>{t('skills.installed')}</div>
        {plugins.length === 0 && <div style={{ color: '#888', fontSize: 13 }}>{t('skills.noInstalled')}</div>}
        {plugins.map((p) => (
          <div key={p.plugin_name} style={{ ...card, display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <div>
              <div style={{ fontWeight: 500 }}>{p.plugin_name} {p.version && <span style={{ fontSize: 12, color: '#888' }}>v{p.version}</span>}</div>
              {p.skills && p.skills.length > 0 && <div style={{ fontSize: 12, color: '#888' }}>{p.skills.join(', ')}</div>}
            </div>
            <button className="px-2 py-1 rounded border text-red-600" style={{ whiteSpace: 'nowrap', flexShrink: 0 }} disabled={busy === p.plugin_name} onClick={() => void uninstall(p.plugin_name)}>
              {busy === p.plugin_name ? t('skills.uninstalling') : t('skills.uninstall')}
            </button>
          </div>
        ))}
      </div>

      {/* 技能目录 */}
      <div>
        <div style={{ fontSize: 13, color: '#555', fontWeight: 600, margin: '4px 0' }}>{t('skills.catalog')}</div>
        {sessionId && filtered.length === 0 && <div style={{ color: '#888', fontSize: 13 }}>{t('skills.noSkills')}</div>}
        {filtered.map((s) => {
          const isInstalled = installedNames.has(s.name);
          const canInstall = !!s.is_builtin_source && !isInstalled;
          return (
            <div key={s.name} style={{ ...card, display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8 }}>
              <div style={{ minWidth: 0 }}>
                <div style={{ fontWeight: 500 }}>
                  {s.name}
                  {s.is_builtin && <span style={{ fontSize: 11, color: '#2563eb', marginLeft: 6 }}>{t('skills.builtin')}</span>}
                  {isInstalled && <span style={{ fontSize: 11, color: '#16a34a', marginLeft: 6 }}>✓ {t('skills.installed')}</span>}
                </div>
                {s.description && <div style={{ fontSize: 12, color: '#888' }}>{s.description}</div>}
              </div>
              {canInstall ? (
                <button className="px-2 py-1 rounded bg-blue-600 text-white" style={{ whiteSpace: 'nowrap', flexShrink: 0 }} disabled={busy === s.name} onClick={() => void install(s)}>
                  {busy === s.name ? t('skills.installing') : t('skills.install')}
                </button>
              ) : (!isInstalled && <span style={{ fontSize: 12, color: '#aaa', whiteSpace: 'nowrap' }}>{t('skills.manualOnly')}</span>)}
            </div>
          );
        })}
      </div>
    </div>
  );
}
