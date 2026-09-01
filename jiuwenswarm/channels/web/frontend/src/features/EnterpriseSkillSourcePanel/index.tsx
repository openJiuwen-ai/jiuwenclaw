/**
 * 企业技能源面板：企业版专用，浏览/搜索/安装/更新第三方技能源（SwarmSkillHub 等）。
 *
 * 与个人版 TeamSkillsHubModal 走同一套 skills.source.* / skills.updates.check /
 * skills.update 通用接口，仅 UI 与展示字段更贴合企业形态：补齐所属空间、作者、
 * 评分、下载量、更新时间、标签，以及分页。
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { webRequest } from '../../services/webClient';
import { getSkillAvatar } from '../../utils/skillAvatar';
import { Pagination } from '../../components/common/Pagination';
import { ConfirmDialog } from '../../components/common/ConfirmDialog';
import { useToast } from '../../components/common/useToast';

const PAGE_SIZE = 20;

type LoadState = 'idle' | 'loading' | 'success' | 'error';

type SourceDescriptor = {
  source_id: string;
  display_name?: string;
  enabled: boolean;
  capabilities?: string[];
};

type SkillCandidate = {
  source_id: string;
  skill_id?: string;
  version_id: string;
  name: string;
  display_name?: string;
  summary?: string;
  version?: string;
  namespace?: string;
  owner_display_name?: string;
  rating_avg?: number;
  rating_count?: number;
  download_count?: number;
  updated_at?: string | number;
  labels?: Array<{ name?: string } | string>;
  downloadable?: boolean;
  accessible?: boolean;
};

type UpdateStatus = {
  source_id: string;
  name: string;
  skill_id?: string;
  current_version_id?: string;
  latest_version_id?: string;
  current_version?: string;
  latest_version?: string;
  has_update: boolean;
  remote_status?: string;
  accessible?: boolean;
  downloadable?: boolean;
};

interface EnterpriseSkillSourcePanelProps {
  sessionId: string;
  viewMode?: 'list' | 'grid';
  onInstalled?: (skillName: string) => void | Promise<void>;
}

function normalizeUpdatedAt(value: SkillCandidate['updated_at']): string {
  if (value == null || value === '') return '';
  if (typeof value === 'number') {
    const millis = value > 10_000_000_000 ? value : value * 1000;
    const date = new Date(millis);
    if (!Number.isNaN(date.getTime())) return date.toLocaleDateString();
    return '';
  }
  return String(value).slice(0, 10);
}

function labelText(label: { name?: string } | string | undefined): string {
  if (label == null) return '';
  if (typeof label === 'string') return label;
  return label.name || '';
}

export function EnterpriseSkillSourcePanel({ sessionId, viewMode = 'list', onInstalled }: EnterpriseSkillSourcePanelProps) {
  const { t } = useTranslation();
  const [sources, setSources] = useState<SourceDescriptor[]>([]);
  const [sourceId, setSourceId] = useState('');
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<SkillCandidate[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loadState, setLoadState] = useState<LoadState>('idle');
  const { toast, showToast, clearToast } = useToast();
  const [installingId, setInstallingId] = useState<string | null>(null);
  const [updateStatuses, setUpdateStatuses] = useState<Map<string, UpdateStatus>>(new Map());
  const [pendingConfirm, setPendingConfirm] = useState<SkillCandidate | null>(null);

  const totalPages = useMemo(() => Math.max(1, Math.ceil(total / PAGE_SIZE)), [total]);

  const withSession = useCallback((params?: Record<string, unknown>) => ({ ...(params || {}), session_id: sessionId }), [sessionId]);

  // 1) 拉取可用技能源，默认选中第一个可搜索的来源
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const data = await webRequest<{
          success: boolean;
          error_message?: string;
          providers?: SourceDescriptor[];
        }>('skills.source.providers', withSession());
        if (!data.success) throw new Error(data.error_message || t('skills.source.errors.loadFailed'));
        const enabled = (data.providers || []).filter(item => item.enabled !== false && (item.capabilities || []).includes('search'));
        if (cancelled) return;
        setSources(enabled);
        if (enabled.length > 0) setSourceId(enabled[0].source_id);
        else setLoadState('error');
      } catch (error) {
        if (cancelled) return;
        setLoadState('error');
        showToast('error', error instanceof Error ? error.message : t('skills.source.errors.loadFailed'));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [showToast, t, withSession]);

  // 2) 已安装技能的更新状态（按 source_id:name 作为已装判定）
  useEffect(() => {
    if (!sourceId) return;
    let cancelled = false;
    void (async () => {
      try {
        const data = await webRequest<{
          success: boolean;
          items?: UpdateStatus[];
        }>('skills.updates.check', withSession({ source_id: sourceId }));
        if (!cancelled && data.success) {
          setUpdateStatuses(new Map((data.items || []).map(item => [`${item.source_id}:${item.name}`, item])));
        }
      } catch {
        // 更新状态查询失败不阻断搜索展示
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [sourceId, withSession]);

  // 3) 搜索
  const runSearch = useCallback(
    async (targetPage: number) => {
      if (!sourceId) return;
      setLoadState('loading');
      clearToast();
      try {
        const data = await webRequest<{
          success: boolean;
          error_message?: string;
          items?: SkillCandidate[];
          total?: number;
        }>(
          'skills.source.search',
          withSession({
            source_id: sourceId,
            q: query.trim(),
            page: targetPage,
            page_size: PAGE_SIZE,
          }),
        );
        if (!data.success) throw new Error(data.error_message || t('skills.source.errors.searchFailed'));
        setResults(data.items || []);
        setTotal(data.total ?? (data.items || []).length);
        setPage(targetPage);
        setLoadState('success');
      } catch (error) {
        setResults([]);
        setTotal(0);
        setLoadState('error');
        showToast('error', error instanceof Error ? error.message : t('skills.source.errors.searchFailed'));
      }
    },
    [query, showToast, sourceId, t, withSession],
  );

  useEffect(() => {
    if (sourceId) {
      setPage(1);
      void runSearch(1);
    }
    // 仅 sourceId 变化时触发；query 变化由搜索按钮/回车触发
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sourceId]);

  const handleInstall = useCallback(
    async (item: SkillCandidate, force = false) => {
      if (installingId) return;
      setInstallingId(item.name);
      clearToast();
      try {
        const data = await webRequest<{
          success: boolean;
          error_code?: string;
          error_message?: string;
          skill?: { name: string };
        }>(
          'skills.source.install',
          withSession({
            source_id: item.source_id || sourceId,
            skill_id: item.skill_id,
            version_id: item.version_id,
            force,
          }),
        );
        if (!data.success) {
          if (!force && data.error_code === 'skill_already_installed') {
            setInstallingId(null);
            setPendingConfirm(item);
            return;
          }
          throw new Error(data.error_message || t('skills.source.errors.installFailed'));
        }
        const skillName = data.skill?.name || item.name;
        showToast('success', t('skills.source.messages.installed', { name: skillName }));
        await onInstalled?.(skillName);
        void runSearch(page);
      } catch (error) {
        showToast('error', error instanceof Error ? error.message : t('skills.source.errors.installFailed'));
      } finally {
        setInstallingId(null);
      }
    },
    [installingId, onInstalled, page, runSearch, showToast, sourceId, t, withSession],
  );

  const handleConfirmInstall = useCallback(() => {
    const item = pendingConfirm;
    setPendingConfirm(null);
    if (item) void handleInstall(item, true);
  }, [pendingConfirm, handleInstall]);

  const handleUpdate = useCallback(
    async (item: SkillCandidate, status: UpdateStatus) => {
      if (installingId) return;
      const targetVersionId = status.latest_version_id || item.version_id;
      setInstallingId(item.name);
      clearToast();
      try {
        const data = await webRequest<{
          success: boolean;
          error_message?: string;
          skill?: { name?: string };
        }>(
          'skills.update',
          withSession({
            source_id: item.source_id || sourceId,
            skill_id: item.skill_id,
            target_version_id: targetVersionId,
            expected_current_version_id: status.current_version_id,
          }),
        );
        if (!data.success) throw new Error(data.error_message || t('skills.source.errors.updateFailed'));
        setUpdateStatuses(previous => {
          const next = new Map(previous);
          next.set(`${item.source_id}:${item.name}`, {
            ...status,
            current_version_id: targetVersionId,
            latest_version_id: targetVersionId,
            has_update: false,
          });
          return next;
        });
        showToast('success', t('skills.source.messages.updated', { name: data.skill?.name || item.name }));
        await onInstalled?.(data.skill?.name || item.name);
      } catch (error) {
        showToast('error', error instanceof Error ? error.message : t('skills.source.errors.updateFailed'));
      } finally {
        setInstallingId(null);
      }
    },
    [installingId, onInstalled, showToast, sourceId, t, withSession],
  );

  const renderAction = (item: SkillCandidate) => {
    const status = updateStatuses.get(`${item.source_id}:${item.name}`);
    const isInstalled = Boolean(status);
    const isInstalling = installingId === item.name;
    if (isInstalled && status?.has_update) {
      return (
        <button
          type="button"
          onClick={() => void handleUpdate(item, status)}
          disabled={isInstalling}
          className="min-w-[76px] h-[28px] px-3 rounded-[24px] text-sm text-text border border-text hover:bg-secondary/50 whitespace-nowrap disabled:text-text-muted disabled:cursor-not-allowed"
        >
          {isInstalling ? t('common.loading') : t('common.update')}
        </button>
      );
    }
    if (isInstalled) {
      return (
        <span className="px-4 h-[28px] flex items-center rounded-2xl text-sm whitespace-nowrap border border-[color:var(--color-border-success)] bg-ok-subtle text-ok">
          {t('skills.status.installed')}
        </span>
      );
    }
    return (
      <button
        type="button"
        onClick={() => void handleInstall(item)}
        disabled={isInstalling}
        className={`min-w-[76px] h-[28px] px-3 rounded-[24px] text-sm text-text border border-text hover:bg-secondary/50 whitespace-nowrap ${
          isInstalling ? 'text-text-muted cursor-not-allowed' : 'text-text'
        }`}
      >
        {isInstalling ? t('common.loading') : t('skills.actions.install')}
      </button>
    );
  };

  const renderCard = (item: SkillCandidate) => {
    const avatar = getSkillAvatar(item.name);
    const displayName = item.display_name || item.name;
    const tags = (item.labels || []).map(labelText).filter(Boolean).slice(0, 4);
    const status = updateStatuses.get(`${item.source_id}:${item.name}`);
    return (
      <div
        key={`${item.source_id}:${item.name}:${item.version_id}`}
        className={`p-4 rounded-lg border border-border bg-panel ${viewMode === 'grid' ? 'flex flex-col' : 'flex items-start justify-between gap-4'}`}
        style={viewMode === 'grid' ? { width: '496px', height: '220px', flexShrink: 0 } : undefined}
      >
        <div className="flex items-start gap-3 min-w-0 flex-1">
          <div className={`w-10 h-10 rounded-lg ${avatar.color} flex items-center justify-center flex-shrink-0 text-text-inverse font-semibold`}>
            {avatar.firstChar}
          </div>
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <span className="text-base font-semibold text-text-strong truncate">{displayName}</span>
              {item.namespace && <span className="shrink-0 text-xs text-text-muted">{item.namespace}</span>}
            </div>
            <div className="text-sm text-text-muted mt-1 line-clamp-2">{item.summary || t('skills.noDescription')}</div>
            <div className="flex flex-wrap gap-x-3 gap-y-1 mt-2 text-xs text-text-muted">
              {item.owner_display_name && (
                <span>
                  {t('skills.source.author')}: {item.owner_display_name}
                </span>
              )}
              {item.rating_avg != null && (
                <span>
                  {t('skills.source.rating')}: {Number(item.rating_avg).toFixed(1)}
                  {item.rating_count != null ? ` (${item.rating_count})` : ''}
                </span>
              )}
              {item.download_count != null && (
                <span>
                  {t('skills.source.downloads')}: {item.download_count}
                </span>
              )}
              <span>
                {t('skills.versionLabel')}: {status?.latest_version || item.version || 'latest'}
              </span>
              {status?.current_version && (
                <span>
                  {t('skills.source.localVersion')}: {status.current_version}
                </span>
              )}
              {normalizeUpdatedAt(item.updated_at) && (
                <span>
                  {t('skills.source.updatedAt')}: {normalizeUpdatedAt(item.updated_at)}
                </span>
              )}
            </div>
            {tags.length > 0 && (
              <div className="flex flex-wrap gap-1.5 mt-2">
                {tags.map(tag => (
                  <span key={tag} className="px-2 py-0.5 rounded-full bg-secondary border border-border text-xs text-text-muted">
                    {tag}
                  </span>
                ))}
              </div>
            )}
          </div>
        </div>
        <div className="flex-shrink-0 self-end">{renderAction(item)}</div>
      </div>
    );
  };

  return (
    <div className="flex flex-col h-full">
      {toast && (
        <div
          className={`mb-3 px-3 py-2.5 rounded-lg text-sm leading-snug border ${
            toast.type === 'success' ? 'border-[color:var(--color-border-success)] bg-ok-subtle text-ok' : 'border-danger/40 bg-danger/10 text-danger'
          }`}
        >
          {toast.text.replace('√', '')}
        </div>
      )}

      <div className="flex items-center gap-2">
        {sources.length > 1 && (
          <select
            value={sourceId}
            onChange={e => setSourceId(e.target.value)}
            className="px-2 py-1.5 rounded-lg text-sm bg-secondary border border-border text-text"
          >
            {sources.map(s => (
              <option key={s.source_id} value={s.source_id}>
                {s.display_name || s.source_id}
              </option>
            ))}
          </select>
        )}
        <input
          value={query}
          onChange={e => setQuery(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && void runSearch(1)}
          placeholder={t('skills.source.searchPlaceholder')}
          className="flex-1 min-w-0 px-3 py-1.5 rounded-lg text-sm bg-secondary border border-border text-text placeholder:text-text-muted"
        />
        <button
          type="button"
          onClick={() => void runSearch(1)}
          disabled={loadState === 'loading' || !sourceId}
          className={`px-4 py-1.5 rounded-2xl text-sm border border-gray-400 hover:border-gray-600 hover:bg-secondary/50 ${
            loadState === 'loading' || !sourceId ? 'text-text-muted cursor-not-allowed' : 'text-text'
          }`}
        >
          {loadState === 'loading' ? t('common.loading') : t('skills.teamskillshub.search')}
        </button>
      </div>

      {loadState === 'loading' && <div className="flex items-center justify-center flex-1 text-text-muted">{t('common.loading')}</div>}
      {loadState === 'error' && <div className="mt-4 text-sm text-text-muted">{t('skills.source.errors.searchFailed')}</div>}
      {loadState === 'success' && (
        <div className={`mt-4 flex-1 min-h-0 overflow-y-auto ${viewMode === 'grid' ? 'flex flex-wrap gap-4 content-start' : 'space-y-3'}`}>
          {results.length === 0 ? <div className="text-sm text-text-muted">{t('skills.source.noResults')}</div> : results.map(renderCard)}
        </div>
      )}

      {loadState === 'success' && <Pagination page={page} totalPages={totalPages} total={total} onPageChange={p => void runSearch(p)} className="mt-3" />}
      {pendingConfirm && (
        <ConfirmDialog
          title={t('skills.source.replaceConfirmTitle')}
          message={t('skills.source.replaceConfirm', { name: pendingConfirm.name })}
          onConfirm={handleConfirmInstall}
          onCancel={() => setPendingConfirm(null)}
          loading={installingId === pendingConfirm.name}
        />
      )}
    </div>
  );
}
