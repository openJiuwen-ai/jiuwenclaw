/**
 * 企业技能面板：已装列表（Gateway 读库）+ URL 验签安装 / 卸载（转发 Agent）。
 */

import { useCallback, useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { webRequest } from '../../services/webClient';
import {
  useExtSettingsStore,
  extSettingsToRoutingParams,
} from '../../stores/extSettingsStore';

const INSTALL_TIMEOUT_MS = 120_000;

type SourceType = 'prebuilt' | 'user' | string;

interface EnterpriseSkillItem {
  skill_name: string;
  source_type: SourceType;
  skill_source?: string | null;
  skill_version?: string | null;
  skill_id?: string | null;
  user_id?: string | null;
  group_id?: string | null;
  bot_id?: string | null;
  installed_at?: string | null;
  updated_at?: string | null;
  removable?: boolean;
}

interface SkillPanelProps {
  sessionId: string;
}

function channelFromSkillSource(skillSource: string | null | undefined): string {
  const raw = String(skillSource || '').trim();
  if (!raw) return '';
  const idx = raw.indexOf(':');
  if (idx <= 0) return 'prebuilt';
  return raw.slice(0, idx);
}

function displaySourceBody(skillSource: string | null | undefined): string {
  const raw = String(skillSource || '').trim();
  if (!raw) return '—';
  const idx = raw.indexOf(':');
  if (idx <= 0) return raw;
  return raw.slice(idx + 1) || raw;
}

export default function SkillPanel({ sessionId }: SkillPanelProps) {
  const { t } = useTranslation();
  const userId = useExtSettingsStore((s) => s.userId);
  const groupId = useExtSettingsStore((s) => s.groupId);
  const botId = useExtSettingsStore((s) => s.botId);
  const routingParams = extSettingsToRoutingParams({ userId, groupId, botId });

  const [skills, setSkills] = useState<EnterpriseSkillItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [search, setSearch] = useState('');
  const [filter, setFilter] = useState<'all' | 'prebuilt' | 'user'>('all');
  const [installUrl, setInstallUrl] = useState('');
  const [installSignature, setInstallSignature] = useState('');
  const [installing, setInstalling] = useState(false);
  const [actionTarget, setActionTarget] = useState<string | null>(null);

  const loadSkills = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const payload = await webRequest<{
        skills?: EnterpriseSkillItem[];
        service_id?: string;
        agent_id?: string;
      }>('skills.enterprise.list', {
        ...routingParams,
        session_id: sessionId,
      });
      setSkills(Array.isArray(payload.skills) ? payload.skills : []);
    } catch (loadError) {
      const message =
        loadError instanceof Error ? loadError.message : t('skills.enterprise.errors.loadFailed');
      setError(message);
      setSkills([]);
    } finally {
      setLoading(false);
    }
  }, [t, sessionId, routingParams.user_id, routingParams.group_id, routingParams.bot_id]);

  useEffect(() => {
    void loadSkills();
  }, [loadSkills]);

  useEffect(() => {
    if (!success) return;
    const timer = window.setTimeout(() => setSuccess(null), 2500);
    return () => window.clearTimeout(timer);
  }, [success]);

  const visibleSkills = useMemo(() => {
    const keyword = search.trim().toLowerCase();
    return skills
      .filter((item) => {
        if (filter === 'prebuilt' && item.source_type !== 'prebuilt') return false;
        if (filter === 'user' && item.source_type !== 'user') return false;
        if (!keyword) return true;
        const haystack = [
          item.skill_name,
          item.source_type,
          item.skill_source || '',
          item.skill_version || '',
          item.user_id || '',
        ]
          .join(' ')
          .toLowerCase();
        return haystack.includes(keyword);
      })
      .sort((a, b) => {
        if (a.source_type !== b.source_type) {
          return a.source_type === 'prebuilt' ? -1 : 1;
        }
        return String(a.skill_name).localeCompare(String(b.skill_name));
      });
  }, [skills, search, filter]);

  const handleInstall = async () => {
    const url = installUrl.trim();
    const signature = installSignature.trim();
    if (!url) {
      setError(t('skills.enterprise.errors.urlRequired'));
      return;
    }
    if (!routingParams.user_id || !routingParams.group_id || !routingParams.bot_id) {
      setError(t('skills.enterprise.errors.routingRequired'));
      return;
    }

    setInstalling(true);
    setError(null);
    try {
      const params: Record<string, unknown> = {
        url,
        session_id: sessionId,
        ...routingParams,
      };
      if (signature) {
        params.signature = signature;
      }
      const payload = await webRequest<{
        success?: boolean;
        skill?: { name?: string };
        error_code?: string;
        error_message?: string;
        detail?: string;
      }>('skills.enterprise.install_by_url', params, { timeoutMs: INSTALL_TIMEOUT_MS });

      if (payload && payload.success === false) {
        throw new Error(
          payload.error_message ||
            payload.detail ||
            payload.error_code ||
            t('skills.enterprise.errors.installFailed'),
        );
      }
      setSuccess(
        t('skills.enterprise.success.installed', {
          name: payload?.skill?.name || '',
        }),
      );
      setInstallUrl('');
      setInstallSignature('');
      await loadSkills();
    } catch (installError) {
      const message =
        installError instanceof Error
          ? installError.message
          : t('skills.enterprise.errors.installFailed');
      setError(message);
    } finally {
      setInstalling(false);
    }
  };

  const handleUninstall = async (item: EnterpriseSkillItem) => {
    const name = String(item.skill_name || '').trim();
    if (!name) return;
    if (!item.removable && item.source_type !== 'user') {
      setError(t('skills.enterprise.errors.prebuiltNotRemovable'));
      return;
    }
    if (!window.confirm(t('skills.enterprise.deleteConfirm', { name }))) return;

    setActionTarget(name);
    setError(null);
    try {
      const payload = await webRequest<{
        success?: boolean;
        error_code?: string;
        error_message?: string;
        detail?: string;
      }>('skills.enterprise.uninstall', {
        name,
        session_id: sessionId,
        ...routingParams,
      }, { timeoutMs: 60_000 });

      if (payload && payload.success === false) {
        throw new Error(
          payload.error_message ||
            payload.detail ||
            payload.error_code ||
            t('skills.enterprise.errors.uninstallFailed'),
        );
      }
      setSuccess(t('skills.enterprise.success.uninstalled', { name }));
      await loadSkills();
    } catch (uninstallError) {
      const message =
        uninstallError instanceof Error
          ? uninstallError.message
          : t('skills.enterprise.errors.uninstallFailed');
      setError(message);
    } finally {
      setActionTarget(null);
    }
  };

  const sourceTypeLabel = (sourceType: SourceType) => {
    if (sourceType === 'prebuilt') return t('skills.enterprise.sourceType.prebuilt');
    if (sourceType === 'user') return t('skills.enterprise.sourceType.user');
    return sourceType || t('skills.source.unknown');
  };

  const channelLabel = (item: EnterpriseSkillItem) => {
    if (item.source_type === 'prebuilt') {
      return t('skills.enterprise.channel.prebuilt');
    }
    const ch = channelFromSkillSource(item.skill_source);
    if (ch === 'web') return t('skills.enterprise.channel.web');
    if (ch === 'skillnet') return t('skills.enterprise.channel.skillnet');
    if (ch === 'clawhub') return t('skills.enterprise.channel.clawhub');
    return ch || t('skills.source.unknown');
  };

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold text-text">{t('skills.enterprise.title')}</h2>
          <p className="text-sm text-text-muted mt-1">{t('skills.enterprise.subtitle')}</p>
        </div>
        <button
          type="button"
          className="btn"
          onClick={() => void loadSkills()}
          disabled={loading}
        >
          {loading ? t('common.refreshing') : t('common.refresh')}
        </button>
      </div>

      {error && (
        <div className="rounded-md border border-danger/40 bg-danger/10 px-3 py-2 text-sm text-danger">
          {error}
        </div>
      )}
      {success && (
        <div className="rounded-md border border-accent/40 bg-accent/10 px-3 py-2 text-sm text-accent">
          {success}
        </div>
      )}

      <div className="card space-y-3">
        <div className="text-sm font-medium text-text">{t('skills.enterprise.installTitle')}</div>
        <p className="text-xs text-text-muted">{t('skills.enterprise.installHint')}</p>
        <div className="grid gap-3 md:grid-cols-2">
          <label className="block space-y-1 md:col-span-2">
            <span className="text-xs text-text-muted">{t('skills.enterprise.fields.url')}</span>
            <input
              type="url"
              value={installUrl}
              onChange={(e) => setInstallUrl(e.target.value)}
              className="w-full rounded-md border border-border bg-bg px-3 py-2 text-[13px] text-text outline-none focus:border-accent"
              placeholder={t('skills.enterprise.placeholders.url')}
              disabled={installing}
            />
          </label>
          <label className="block space-y-1 md:col-span-2">
            <span className="text-xs text-text-muted">{t('skills.enterprise.fields.signature')}</span>
            <input
              type="text"
              value={installSignature}
              onChange={(e) => setInstallSignature(e.target.value)}
              className="w-full rounded-md border border-border bg-bg px-3 py-2 text-[13px] text-text outline-none focus:border-accent font-mono"
              placeholder={t('skills.enterprise.placeholders.signature')}
              disabled={installing}
            />
          </label>
          <div className="flex items-end md:col-span-2">
            <button
              type="button"
              className="btn primary w-full md:w-auto"
              onClick={() => void handleInstall()}
              disabled={installing}
            >
              {installing ? t('skills.enterprise.installing') : t('skills.actions.install')}
            </button>
          </div>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <input
          type="search"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="min-w-[220px] flex-1 rounded-md border border-border bg-bg px-3 py-2 text-[13px] text-text outline-none focus:border-accent"
          placeholder={t('skills.enterprise.searchPlaceholder')}
        />
        <div className="flex items-center gap-1 rounded-lg bg-secondary/60 p-1">
          {([
            ['all', t('skills.enterprise.filter.all')],
            ['prebuilt', t('skills.enterprise.filter.prebuilt')],
            ['user', t('skills.enterprise.filter.user')],
          ] as const).map(([key, label]) => (
            <button
              key={key}
              type="button"
              className={`text-xs px-2 py-1 rounded ${
                filter === key ? 'bg-accent text-white font-medium' : 'text-text-muted hover:text-text'
              }`}
              onClick={() => setFilter(key)}
            >
              {label}
            </button>
          ))}
        </div>
        <span className="text-xs text-text-muted">
          {t('skills.totalCount', { count: visibleSkills.length })}
        </span>
      </div>

      <div className="card overflow-x-auto p-0">
        <table className="w-full min-w-[720px]">
          <thead>
            <tr className="border-b border-border">
              <th className="px-4 py-3 text-left text-sm font-medium text-text-muted">
                {t('skills.enterprise.columns.name')}
              </th>
              <th className="px-4 py-3 text-left text-sm font-medium text-text-muted">
                {t('skills.enterprise.columns.type')}
              </th>
              <th className="px-4 py-3 text-left text-sm font-medium text-text-muted">
                {t('skills.enterprise.columns.channel')}
              </th>
              <th className="px-4 py-3 text-left text-sm font-medium text-text-muted">
                {t('skills.enterprise.columns.source')}
              </th>
              <th className="px-4 py-3 text-left text-sm font-medium text-text-muted">
                {t('skills.enterprise.columns.version')}
              </th>
              <th className="px-4 py-3 text-left text-sm font-medium text-text-muted">
                {t('skills.enterprise.columns.installer')}
              </th>
              <th className="px-4 py-3 text-left text-sm font-medium text-text-muted w-[120px]">
                {t('skills.enterprise.columns.actions')}
              </th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr>
                <td colSpan={7} className="px-4 py-8 text-center text-text-muted">
                  {t('common.loading')}
                </td>
              </tr>
            ) : visibleSkills.length === 0 ? (
              <tr>
                <td colSpan={7} className="px-4 py-8 text-center text-text-muted">
                  {t('skills.enterprise.empty')}
                </td>
              </tr>
            ) : (
              visibleSkills.map((item) => {
                const removable = Boolean(item.removable ?? item.source_type === 'user');
                const busy = actionTarget === item.skill_name;
                return (
                  <tr key={item.skill_name} className="border-b border-border last:border-0">
                    <td className="px-4 py-3 text-sm font-medium text-text">
                      {item.skill_name}
                    </td>
                    <td className="px-4 py-3 text-sm text-text-muted">
                      {sourceTypeLabel(item.source_type)}
                    </td>
                    <td className="px-4 py-3 text-sm text-text-muted">{channelLabel(item)}</td>
                    <td
                      className="px-4 py-3 text-xs text-text-muted font-mono max-w-[240px] truncate"
                      title={String(item.skill_source || '')}
                    >
                      {displaySourceBody(item.skill_source)}
                    </td>
                    <td className="px-4 py-3 text-sm text-text-muted">
                      {item.skill_version || '—'}
                    </td>
                    <td className="px-4 py-3 text-sm text-text-muted">
                      {item.user_id || '—'}
                    </td>
                    <td className="px-4 py-3">
                      {removable ? (
                        <button
                          type="button"
                          className="text-sm text-danger hover:underline disabled:opacity-50"
                          disabled={busy}
                          onClick={() => void handleUninstall(item)}
                        >
                          {busy ? t('common.loading') : t('skills.actions.uninstall')}
                        </button>
                      ) : (
                        <span className="text-xs text-text-muted">
                          {t('skills.enterprise.notRemovable')}
                        </span>
                      )}
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
