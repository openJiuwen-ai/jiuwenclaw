import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ChevronDown, Search, TrendingUp, Newspaper, Briefcase } from 'lucide-react';
import { webRequest } from '../../services/webClient';
import { useSessionStore } from '../../stores/sessionStore';
import { projectRegistryClient } from '../../features/workspace/projectRegistryClient';
import type { ProjectInfo } from '../../features/workspace/projectTypes';
import type { CronJobDTO, CronTaskUI, CronTemplateUI } from '../../types/cron';
import { CRON_TEMPLATES } from './constants';
import StatusBadge from './StatusBadge';
import ConfirmDialog from './ConfirmDialog';
import CronTaskDrawer, { jobToForm, templateToForm, type CronTaskFormValue } from './CronTaskDrawer';
import { useClickOutside } from './useClickOutside';
import emptyIllustration from '../../assets/cron-empty.svg';

// 主动推荐自动维护的 job id（与后端 proactive_cron_sync.PROACTIVE_JOB_ID 一致）。
// 该 job 的整体开关由 config 的 proactive_recommendation.enabled 驱动（关则删除，不在列表里）；
// 面板上禁用停止/删除，编辑时仅 cron 表达式与时区可改，其余字段只读（沿用旧面板约束，见
// upstream 提交 59cf6de7）。
const PROACTIVE_AUTO_JOB_ID = 'proactive-tick-auto';

// 用于展示已有任务的推送频道（含历史数据可能存在的 wecom/wechat）
const KNOWN_TARGET_KEYS = ['web', 'tui', 'xiaoyi', 'feishu', 'dingtalk', 'whatsapp', 'wecom', 'wechat'];
// 创建/编辑时可选的推送频道：wecom/wechat 已被 upstream 下架（见提交 e12d1952、d57567e4），
// 不在下拉里出现，但已有数据仍按上面 KNOWN_TARGET_KEYS 正常展示
const SELECTABLE_TARGET_KEYS = ['web', 'tui', 'xiaoyi', 'feishu', 'dingtalk', 'whatsapp'];

interface CronPanelProps {
  sessionId: string;
}

type TabKey = 'list' | 'template' | 'history';

function TemplateIcon({ icon }: { icon: CronTemplateUI['icon'] }) {
  const Icon = icon === 'trend' ? TrendingUp : icon === 'newspaper' ? Newspaper : Briefcase;
  return (
    <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-accent-subtle text-accent">
      <Icon size={18} />
    </span>
  );
}

function Th({ children, first }: { children: React.ReactNode; first?: boolean }) {
  return (
    <th className="py-3 font-medium">
      <span className={`inline-block ${first ? 'px-4' : 'border-l border-border pl-4 pr-4'}`}>{children}</span>
    </th>
  );
}

function cronJobToUI(job: CronJobDTO, projects: ProjectInfo[]): CronTaskUI {
  const project = job.project_id ? projects.find((p) => p.project_id === job.project_id) ?? null : null;
  return {
    id: job.id,
    name: job.name,
    projectId: job.project_id,
    projectName: project ? project.name : null,
    description: job.description,
    modelName: job.model_name ?? null,
    cronExpr: job.cron_expr,
    timezone: job.timezone,
    enabled: job.enabled,
    expired: job.expired,
    deliveryChannel: job.targets,
  };
}

export default function CronPanel({ sessionId }: CronPanelProps) {
  const { t } = useTranslation();
  const mode = useSessionStore((s) => s.runtimes[sessionId]?.mode ?? 'agent.plan');

  const [jobs, setJobs] = useState<CronTaskUI[]>([]);
  const [projects, setProjects] = useState<ProjectInfo[]>([]);
  const [enabledChannels, setEnabledChannels] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  const [activeTab, setActiveTab] = useState<TabKey>('list');
  const [search, setSearch] = useState('');

  const [createMenuOpen, setCreateMenuOpen] = useState(false);
  const createMenuRef = useRef<HTMLDivElement>(null);
  useClickOutside(createMenuRef, createMenuOpen, () => setCreateMenuOpen(false));

  const [rowMenuJobId, setRowMenuJobId] = useState<string | null>(null);
  const rowMenuRef = useRef<HTMLDivElement>(null);
  useClickOutside(rowMenuRef, rowMenuJobId !== null, () => setRowMenuJobId(null));

  const [drawer, setDrawer] = useState<
    | { mode: 'create' | 'template'; initial?: CronTaskFormValue }
    | { mode: 'edit'; initial: CronTaskFormValue; jobId: string }
    | null
  >(null);

  const [confirmState, setConfirmState] = useState<{ type: 'delete' | 'stop'; job: CronTaskUI } | null>(null);

  const channelLabel = useCallback(
    (targets: string) => (KNOWN_TARGET_KEYS.includes(targets) ? t(`cron.targets.${targets}`) : targets),
    [t],
  );

  const loadJobs = useCallback(async (projectList: ProjectInfo[]) => {
    setLoading(true);
    setError(null);
    try {
      const payload = await webRequest<{ jobs: CronJobDTO[] }>('cron.job.list');
      setJobs((payload.jobs || []).map((j) => cronJobToUI(j, projectList)));
    } catch (loadError) {
      const message = loadError instanceof Error ? loadError.message : t('cron.errors.loadJobs');
      setError(message);
      setJobs([]);
    } finally {
      setLoading(false);
    }
  }, [t]);

  const loadProjects = useCallback(async () => {
    try {
      const payload = await projectRegistryClient.list('all');
      const visible = (payload.projects || []).filter((p) => !p.hidden);
      setProjects(visible);
      return visible;
    } catch {
      return [];
    }
  }, []);

  // 沿用旧 CronPanel 的做法：按已启用的推送频道决定"推送频道"下拉里哪些选项可选
  const loadChannels = useCallback(async () => {
    try {
      const payload = await webRequest<{ channels?: unknown[] }>('channel.get');
      const channels = payload?.channels || [];
      const enabled = new Set<string>();
      for (const item of channels) {
        if (item && typeof item === 'object' && 'channel_id' in item) {
          const channelId = (item as { channel_id: unknown }).channel_id;
          if (typeof channelId === 'string' && channelId.trim()) {
            enabled.add(channelId.trim().toLowerCase());
          }
        }
      }
      setEnabledChannels(enabled);
    } catch {
      // 忽略错误，保持空集合（下拉里全部选项禁用，用户仍可看到但选不了，不阻塞其他功能）
    }
  }, []);

  const targetOptions = useMemo(
    () => SELECTABLE_TARGET_KEYS.map((id) => ({ value: id, label: t(`cron.targets.${id}`), disabled: !enabledChannels.has(id) })),
    [enabledChannels, t],
  );

  useEffect(() => {
    void (async () => {
      const projectList = await loadProjects();
      await loadJobs(projectList);
      await loadChannels();
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!success) return;
    const timer = window.setTimeout(() => setSuccess(null), 2000);
    return () => window.clearTimeout(timer);
  }, [success]);

  useEffect(() => {
    if (!error) return;
    const timer = window.setTimeout(() => setError(null), 2000);
    return () => window.clearTimeout(timer);
  }, [error]);

  const filteredJobs = useMemo(
    () => jobs.filter((j) => j.name.toLowerCase().includes(search.trim().toLowerCase())),
    [jobs, search],
  );
  const filteredTemplates = useMemo(
    () => CRON_TEMPLATES.filter((tpl) => t(tpl.titleKey).toLowerCase().includes(search.trim().toLowerCase())),
    [search, t],
  );

  async function handleCreateSubmit(value: CronTaskFormValue) {
    try {
      await webRequest<{ job: CronJobDTO }>('cron.job.create', {
        name: value.name.trim(),
        description: value.description.trim(),
        cron_expr: value.cronExpr.trim(),
        timezone: value.timezone,
        targets: value.targets.trim() || 'web',
        enabled: value.enabled,
        ...(value.projectDir ? { project_dir: value.projectDir } : {}),
        ...(value.modelName ? { model_name: value.modelName } : {}),
        mode,
        session_id: sessionId,
      });
      setSuccess(t('cron.success.created'));
      setDrawer(null);
      setActiveTab('list');
      await loadJobs(projects);
    } catch (createError) {
      const message = createError instanceof Error ? createError.message : t('cron.errors.createFailed');
      setError(message);
    }
  }

  async function handleEditSubmit(jobId: string, value: CronTaskFormValue) {
    try {
      const isProactive = jobId === PROACTIVE_AUTO_JOB_ID;
      // proactive 自动维护 job 只允许改 cron_expr 和 timezone；enabled/mode/name/description/
      // targets/model_name 由 ConfigPanel/cron_sync 管理，不能带，否则会跟 proactive.tick 的
      // 调度逻辑冲突（沿用 upstream 提交 e64dcf51/59cf6de7 的约束）。
      const patch = isProactive
        ? { cron_expr: value.cronExpr.trim(), timezone: value.timezone }
        : {
            name: value.name.trim(),
            description: value.description.trim(),
            cron_expr: value.cronExpr.trim(),
            timezone: value.timezone,
            targets: value.targets.trim() || 'web',
            enabled: value.enabled,
            ...(value.modelName ? { model_name: value.modelName } : {}),
            mode,
          };
      await webRequest<{ job: CronJobDTO }>('cron.job.update', {
        id: jobId,
        patch,
        session_id: sessionId,
      });
      setSuccess(t('cron.success.updated'));
      setDrawer(null);
      await loadJobs(projects);
    } catch (updateError) {
      const message = updateError instanceof Error ? updateError.message : t('cron.errors.updateFailed');
      setError(message);
    }
  }

  async function handleStopConfirm() {
    if (!confirmState) return;
    try {
      await webRequest<{ job: CronJobDTO }>('cron.job.toggle', { id: confirmState.job.id, enabled: false });
      setSuccess(t('cron.success.statusUpdated'));
      await loadJobs(projects);
    } catch (toggleError) {
      const message = toggleError instanceof Error ? toggleError.message : t('cron.errors.toggleFailed');
      setError(message);
    } finally {
      setConfirmState(null);
    }
  }

  async function handleDeleteConfirm() {
    if (!confirmState) return;
    try {
      await webRequest<{ deleted: boolean }>('cron.job.delete', { id: confirmState.job.id });
      setSuccess(t('cron.success.deleted'));
      await loadJobs(projects);
    } catch (deleteError) {
      const message = deleteError instanceof Error ? deleteError.message : t('cron.errors.deleteFailed');
      setError(message);
    } finally {
      setConfirmState(null);
    }
  }

  function openTemplateDrawer(tpl: CronTemplateUI) {
    setDrawer({ mode: 'template', initial: templateToForm(tpl, t(tpl.titleKey), t(tpl.descriptionKey)) });
  }

  return (
    <div className="flex-1 min-h-0 relative overflow-y-auto" data-testid="cron-panel" data-session-id={sessionId}>
      {success && (
        <div className="pointer-events-none absolute top-3 left-1/2 -translate-x-1/2 z-20" data-testid="cron-success">
          <div className="bg-ok text-white px-4 py-2 rounded-lg shadow-lg animate-rise text-sm">{success}</div>
        </div>
      )}
      {error && (
        <div className="pointer-events-none absolute top-3 left-1/2 -translate-x-1/2 z-20" data-testid="cron-error">
          <div className="bg-danger text-white px-4 py-2 rounded-lg shadow-lg animate-rise text-sm">{error}</div>
        </div>
      )}

      <div className="mx-auto max-w-6xl px-8 py-8">
        {/* 页头 */}
        <div className="mb-5 flex items-start justify-between">
          <div>
            <h1 className="text-xl font-semibold text-text-strong">{t('cron.pageTitle')}</h1>
            <p className="mt-1 text-sm text-text-muted">{t('cron.pageSubtitle')}</p>
          </div>
          <div className="relative" ref={createMenuRef}>
            <button
              onClick={() => setCreateMenuOpen((v) => !v)}
              className="flex items-center gap-2 rounded-full bg-[#141414] px-6 py-1.5 text-sm font-bold text-white hover:bg-black"
              data-testid="cron-create-toggle"
            >
              {t('cron.createMenu.trigger')} <ChevronDown size={14} />
            </button>
            {createMenuOpen && (
              <div className="absolute right-0 top-[calc(100%+6px)] z-20 w-44 rounded-lg border border-border bg-card py-1.5 shadow-lg">
                <button
                  onClick={() => {
                    setCreateMenuOpen(false);
                    setDrawer({ mode: 'create' });
                  }}
                  className="block w-full px-3 py-2 text-left text-sm font-semibold text-text hover:bg-bg-hover"
                >
                  {t('cron.createMenu.manual')}
                </button>
                <button
                  onClick={() => {
                    setCreateMenuOpen(false);
                    window.alert(t('cron.createMenu.viaChatPlaceholder'));
                  }}
                  className="block w-full px-3 py-2 text-left text-sm font-semibold text-text hover:bg-bg-hover"
                >
                  {t('cron.createMenu.viaChat')}
                </button>
              </div>
            )}
          </div>
        </div>

        {/* 任务总数统计行（三态运行状态徽标等后端 last_run_status 交付后再上线，见 backend-requests.md #1） */}
        {activeTab === 'list' && (
          <div className="mb-4 flex items-center gap-3">
            <span className="text-lg font-bold text-text-strong">{t('cron.stats.total', { count: jobs.length })}</span>
          </div>
        )}

        {/* Tab 导航 */}
        <div className="mb-4 flex items-center gap-6 border-b border-border">
          {([
            ['list', t('cron.tabs.list')],
            ['template', t('cron.tabs.template')],
            ['history', t('cron.tabs.history')],
          ] as [TabKey, string][]).map(([key, label]) => (
            <button
              key={key}
              onClick={() => setActiveTab(key)}
              className={`-mb-px border-b-2 px-1 py-2.5 text-sm font-bold transition-colors ${
                activeTab === key ? 'border-text-strong text-text-strong' : 'border-transparent text-text-muted hover:text-text'
              }`}
            >
              {label}
            </button>
          ))}
        </div>

        {/* 搜索框 */}
        {!(activeTab === 'list' && jobs.length === 0) && activeTab !== 'history' && (
          <div className="mb-4">
            <div className="relative w-full">
              <Search size={14} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-text-muted" />
              <input
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder={t('cron.search.placeholder') ?? undefined}
                className="w-full rounded-md border border-border bg-card py-1.5 pl-9 pr-3 text-sm text-text outline-none focus:border-accent"
              />
            </div>
          </div>
        )}

        {/* tab: 任务列表 */}
        {activeTab === 'list' && (
          loading ? (
            <div className="rounded-lg border border-border bg-secondary/30 px-3 py-4 flex items-center justify-center">
              {t('cron.loading')}
            </div>
          ) : jobs.length === 0 ? (
            <div className="flex flex-col items-center gap-4 py-16">
              <img src={emptyIllustration} alt="" className="h-20 w-20" />
              <button onClick={() => setDrawer({ mode: 'create' })} className="btn !px-4 !py-2">
                {t('cron.empty.createButton')}
              </button>
              <div className="mt-8 w-full">
                <div className="mb-3 flex items-center justify-between">
                  <span className="text-sm font-bold text-text-strong">{t('cron.empty.templateSectionTitle')}</span>
                  <button onClick={() => setActiveTab('template')} className="text-xs text-accent hover:text-accent-hover">
                    {t('cron.empty.templateMore')}
                  </button>
                </div>
                <div className="grid grid-cols-3 gap-4">
                  {CRON_TEMPLATES.map((tpl) => (
                    <button
                      key={tpl.id}
                      onClick={() => openTemplateDrawer(tpl)}
                      className="rounded-lg border border-border bg-card p-4 text-left transition-colors hover:border-accent"
                    >
                      <div className="mb-2 flex items-center gap-2">
                        <TemplateIcon icon={tpl.icon} />
                        <span className="text-sm font-bold text-text-strong">{t(tpl.titleKey)}</span>
                      </div>
                      <p className="line-clamp-3 text-xs leading-relaxed text-text-muted">{t(tpl.descriptionKey)}</p>
                    </button>
                  ))}
                </div>
              </div>
            </div>
          ) : (
            <div className="overflow-visible rounded-lg border border-border">
              <table className="w-full border-collapse text-sm">
                <thead>
                  <tr className="border-b border-border bg-bg-muted text-left text-text">
                    <Th first>{t('cron.table.name')}</Th>
                    <Th>{t('cron.table.project')}</Th>
                    <Th>{t('cron.table.schedule')}</Th>
                    <Th>{t('cron.table.status')}</Th>
                    <Th>{t('cron.table.timezone')}</Th>
                    <Th>{t('cron.table.channel')}</Th>
                    <Th>{t('cron.table.actions')}</Th>
                  </tr>
                </thead>
                <tbody>
                  {filteredJobs.map((job) => {
                    const isProactive = job.id === PROACTIVE_AUTO_JOB_ID;
                    return (
                      <tr key={job.id} className="border-b border-border last:border-0">
                        <td className="px-4 py-3 text-text">
                          <div className="flex items-center gap-1">
                            {job.name}
                            {isProactive && (
                              <span
                                className="shrink-0 inline-flex items-center px-1.5 py-0.5 rounded-full text-[10px] font-medium bg-sky-100 text-sky-700"
                                title={t('cron.autoManagedHint') ?? undefined}
                              >
                                {t('cron.autoManaged')}
                              </span>
                            )}
                          </div>
                        </td>
                        <td className="px-4 py-3 text-text">{job.projectName ?? t('cron.table.noProject')}</td>
                        <td className="px-4 py-3 text-text mono">{job.cronExpr}</td>
                        {/* proactive 自动维护 job 的整体开关由 config 控制（关了就删除，不在列表里），
                            因此这里只有两态：过期 → 过期；否则 → 启用，不显示"禁用"中间态
                            （沿用 upstream 提交 59cf6de7 的约束） */}
                        <td className="px-4 py-3">
                          <StatusBadge enabled={isProactive ? !job.expired : job.enabled} expired={job.expired} />
                        </td>
                        <td className="px-4 py-3 text-text">{job.timezone}</td>
                        <td className="px-4 py-3 text-text">{channelLabel(job.deliveryChannel)}</td>
                        <td className="relative px-4 py-3">
                          <div className="flex items-center gap-3">
                            {isProactive ? (
                              <span className="text-sm text-text-muted/50 cursor-not-allowed select-none" title={t('cron.autoManagedToggleDisabled') ?? undefined}>
                                {t('cron.table.stop')}
                              </span>
                            ) : (
                              <button
                                onClick={() => setConfirmState({ type: 'stop', job })}
                                className="text-sm text-[#1476FF] hover:opacity-80"
                              >
                                {t('cron.table.stop')}
                              </button>
                            )}
                            <button
                              onClick={() => setDrawer({ mode: 'edit', initial: jobToForm(job), jobId: job.id })}
                              className="text-sm text-[#1476FF] hover:opacity-80"
                            >
                              {t('cron.table.edit')}
                            </button>
                            <div className="relative" ref={rowMenuJobId === job.id ? rowMenuRef : undefined}>
                              <button
                                onClick={() => setRowMenuJobId(rowMenuJobId === job.id ? null : job.id)}
                                className="flex items-center gap-0.5 text-sm text-[#1476FF] hover:opacity-80"
                              >
                                {t('cron.table.more')} <ChevronDown size={13} />
                              </button>
                              {rowMenuJobId === job.id && (
                                <div className="absolute left-0 top-[calc(100%+4px)] z-20 w-28 rounded-lg border border-border bg-card py-1.5 shadow-lg">
                                  {isProactive ? (
                                    <span
                                      className="block w-full px-3 py-2 text-left text-sm text-text-muted/50 cursor-not-allowed"
                                      title={t('cron.autoManagedToggleDisabled') ?? undefined}
                                    >
                                      {t('cron.delete')}
                                    </span>
                                  ) : (
                                    <button
                                      onClick={() => {
                                        setRowMenuJobId(null);
                                        setConfirmState({ type: 'delete', job });
                                      }}
                                      className="block w-full px-3 py-2 text-left text-sm text-[#1476FF] hover:bg-bg-hover"
                                    >
                                      {t('cron.delete')}
                                    </button>
                                  )}
                                  <button
                                    onClick={() => {
                                      setRowMenuJobId(null);
                                      setSuccess(t('cron.history.comingSoon'));
                                    }}
                                    className="block w-full px-3 py-2 text-left text-sm text-[#1476FF] hover:bg-bg-hover"
                                  >
                                    {t('cron.table.history')}
                                  </button>
                                </div>
                              )}
                            </div>
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )
        )}

        {/* tab: 任务模板 */}
        {activeTab === 'template' && (
          <div className="grid grid-cols-3 gap-4">
            {filteredTemplates.map((tpl) => (
              <button
                key={tpl.id}
                onClick={() => openTemplateDrawer(tpl)}
                className="rounded-lg border border-border bg-card p-4 text-left transition-colors hover:border-accent"
              >
                <div className="mb-2 flex items-center gap-2">
                  <TemplateIcon icon={tpl.icon} />
                  <span className="text-sm font-bold text-text-strong">{t(tpl.titleKey)}</span>
                </div>
                <p className="text-xs leading-relaxed text-text-muted">{t(tpl.descriptionKey)}</p>
              </button>
            ))}
          </div>
        )}

        {/* tab: 执行历史（等 backend-requests.md #1 交付后接入真实数据，见 plan.md §5） */}
        {activeTab === 'history' && (
          <div className="flex flex-col items-center gap-2 rounded-lg border border-border py-16 text-text-muted">
            <p className="text-sm">{t('cron.history.comingSoon')}</p>
          </div>
        )}

        {/* 创建/编辑/模板抽屉 */}
        {drawer && (
          <CronTaskDrawer
            mode={drawer.mode}
            initial={drawer.initial}
            projects={projects}
            targetOptions={targetOptions}
            proactiveLocked={drawer.mode === 'edit' && drawer.jobId === PROACTIVE_AUTO_JOB_ID}
            onClose={() => setDrawer(null)}
            onSwitchToManual={drawer.mode === 'template' ? () => setDrawer({ mode: 'create', initial: drawer.initial }) : undefined}
            onSwitchToTemplate={drawer.mode === 'create' ? () => { setDrawer(null); setActiveTab('template'); } : undefined}
            onSubmit={(value) => {
              if (drawer.mode === 'edit') void handleEditSubmit(drawer.jobId, value);
              else void handleCreateSubmit(value);
            }}
          />
        )}

        {/* 删除确认弹窗 */}
        {confirmState?.type === 'delete' && (
          <ConfirmDialog
            title={t('cron.confirm.deleteTitle')}
            message={t('cron.confirm.deleteMessage', { name: confirmState.job.name })}
            onConfirm={() => void handleDeleteConfirm()}
            onCancel={() => setConfirmState(null)}
          />
        )}

        {/* 停止确认弹窗 */}
        {confirmState?.type === 'stop' && (
          <ConfirmDialog
            title={t('cron.confirm.stopTitle')}
            message={t('cron.confirm.stopMessage', { name: confirmState.job.name })}
            onConfirm={() => void handleStopConfirm()}
            onCancel={() => setConfirmState(null)}
          />
        )}
      </div>
    </div>
  );
}
