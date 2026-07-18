import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ChevronDown, Search, TrendingUp, Newspaper, Briefcase } from 'lucide-react';
import { webRequest, webClient } from '../../services/webClient';
import { useSessionStore } from '../../stores/sessionStore';
import { useCronStore } from '../../stores';
import { projectRegistryClient } from '../../features/workspace/projectRegistryClient';
import type { ProjectInfo } from '../../features/workspace/projectTypes';
import type { Session } from '../../types';
import type { CronJobDTO, CronTaskUI, CronTemplateUI } from '../../types/cron';
import { CRON_TEMPLATES } from './constants';
import { cronExprToSchedule, summarizeSchedule } from './scheduleConvert';
import StatusBadge, { BoldRingIcon, RunningIcon } from './StatusBadge';
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

// 执行历史目前只有"该功能即将上线"占位（等 backend-requests.md #1 的真实数据接口交付），
// 用户要求先不在界面上露出入口（tab + 行内"运行历史"菜单项），但保留代码，等后端接口交付后
// 把这个开关打开即可，不用再重写 UI
const CRON_HISTORY_UI_ENABLED = false;

interface CronPanelProps {
  sessionId: string;
  onCreateViaChat: (initialInputValue: string) => void;
  /**
   * 跳转到"触发的会话"，复用工作面板的会话导航逻辑（App.tsx 的 requestSessionNavigation）。
   * 传 Session 对象（如"触发的会话"列表里已有完整数据）或直接传 session_id 字符串
   * （如立即执行返回的 session_id，还没有完整 Session 数据）。
   */
  onSelectSession: (session: Session | string) => void;
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

// 任务总数统计行旁边的小标签（运行中/已暂停 各多少个），样式复刻自阶段1 demo 的 StatPill
function StatPill({ icon, label, count }: { icon: React.ReactNode; label: string; count: number }) {
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full border border-border bg-card px-2.5 py-1 text-xs text-text">
      {icon}
      {label} {count}
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

export default function CronPanel({ sessionId, onCreateViaChat, onSelectSession }: CronPanelProps) {
  const { t } = useTranslation();
  const mode = useSessionStore((s) => s.runtimes[sessionId]?.mode ?? 'agent');
  // 工作面板侧边栏的"按项目分组展示定时任务"用的是独立的 useCronStore（见
  // multi-session/sidebar/ConversationSidebar.tsx），跟这个面板自己的 jobs state 是两份数据；
  // 在这里创建/编辑/停止/删除任务后也要通知它刷新，否则侧边栏那边的任务文件夹会显示过期数据
  const reloadCronStore = useCronStore((s) => s.reload);
  const loadCronSessions = useCronStore((s) => s.loadCronSessions);

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

  // "触发的会话"弹层：跟"更多"菜单同一套开合逻辑，但单独维护 ref/开关，因为触发的会话
  // 弹层是从"更多"菜单里的一个按钮打开的，不共用同一个 ref（点击弹层内部会话行不应该被
  // useClickOutside 判定为"点了外面"）
  const [sessionsPopoverJobId, setSessionsPopoverJobId] = useState<string | null>(null);
  const sessionsPopoverRef = useRef<HTMLDivElement>(null);
  useClickOutside(sessionsPopoverRef, sessionsPopoverJobId !== null, () => setSessionsPopoverJobId(null));
  const [triggeredSessions, setTriggeredSessions] = useState<Record<string, Session[]>>({});
  const [triggeredSessionsLoading, setTriggeredSessionsLoading] = useState<Record<string, boolean>>({});

  // "预览"（接下来几次触发时间）弹层：功能在旧版 CronPanel 里有、阶段4重写时漏做了，
  // 后端 cron.job.preview 接口一直都在，这次顺手加回来，跟"触发的会话"同一套弹层模式
  const [previewPopoverJobId, setPreviewPopoverJobId] = useState<string | null>(null);
  const previewPopoverRef = useRef<HTMLDivElement>(null);
  useClickOutside(previewPopoverRef, previewPopoverJobId !== null, () => setPreviewPopoverJobId(null));
  const [previewRuns, setPreviewRuns] = useState<Record<string, { wake_at: string; push_at: string }[]>>({});
  const [previewLoading, setPreviewLoading] = useState<Record<string, boolean>>({});

  const [drawer, setDrawer] = useState<
    | { mode: 'create' | 'template'; initial?: CronTaskFormValue }
    | { mode: 'edit'; initial: CronTaskFormValue; jobId: string }
    | null
  >(null);

  const [confirmState, setConfirmState] = useState<{ type: 'delete' | 'stop' | 'runNow'; job: CronTaskUI } | null>(null);
  const [confirmBusy, setConfirmBusy] = useState(false);

  const channelLabel = useCallback(
    (targets: string) => (KNOWN_TARGET_KEYS.includes(targets) ? t(`cron.targets.${targets}`) : targets),
    [t],
  );

  // "计划于"列：能识别成 周期/按间隔/单次 六种模式之一的就转成人话摘要（"每天 09:30"），
  // 识别不了的（Agent工具/TUI建的、或手写 Cron表达式 tab 的任意表达式）原样展示 7 段式原文兜底
  const scheduleLabel = useCallback(
    (cronExpr: string) => {
      const parsed = cronExprToSchedule(cronExpr);
      return parsed ? summarizeSchedule(parsed, t) : <span className="mono">{cronExpr}</span>;
    },
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
  }, [loadChannels, loadJobs, loadProjects]);

  // 监听 Agent 工具调用结果：cron_ 前缀的工具（比如通过聊天创建/改动定时任务）执行完后
  // 自动刷新任务列表，不用用户手动刷新页面（复用的是 upstream 同款监听逻辑，见 progress.md）
  useEffect(() => {
    const CRON_TOOL_PREFIX = 'cron_';
    const unsubscribe = webClient.on('chat.tool_result', (event) => {
      const payload = event.payload as Record<string, unknown>;
      const inner = (payload?.tool_result as Record<string, unknown>) ?? payload;
      const toolName = String(inner?.tool_name ?? inner?.name ?? '');
      if (toolName === 'cron' || toolName.startsWith(CRON_TOOL_PREFIX)) {
        void loadJobs(projects);
        void reloadCronStore();
      }
    });
    return unsubscribe;
  }, [loadJobs, projects, reloadCronStore]);

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

  // 任务总数统计行旁边的分类计数：跟 StatusBadge 的判断逻辑保持一致（expired 优先于 enabled）。
  // 运行中/已暂停/过期是任务本身状态的完整三态，不依赖后端；"运行失败"是执行历史维度的概念
  // （某一次执行的结果），不属于这里，见 StatusBadge.tsx 顶部注释
  const runningCount = useMemo(() => jobs.filter((j) => !j.expired && j.enabled).length, [jobs]);
  const pausedCount = useMemo(() => jobs.filter((j) => !j.expired && !j.enabled).length, [jobs]);
  const expiredCount = useMemo(() => jobs.filter((j) => j.expired).length, [jobs]);

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
      void reloadCronStore();
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
      void reloadCronStore();
    } catch (updateError) {
      const message = updateError instanceof Error ? updateError.message : t('cron.errors.updateFailed');
      setError(message);
    }
  }

  async function handleStopConfirm() {
    if (!confirmState || confirmBusy) return;
    setConfirmBusy(true);
    try {
      await webRequest<{ job: CronJobDTO }>('cron.job.toggle', { id: confirmState.job.id, enabled: false });
      setSuccess(t('cron.success.statusUpdated'));
      await loadJobs(projects);
      void reloadCronStore();
    } catch (toggleError) {
      const message = toggleError instanceof Error ? toggleError.message : t('cron.errors.toggleFailed');
      setError(message);
    } finally {
      setConfirmBusy(false);
      setConfirmState(null);
    }
  }

  // "启动"（恢复已暂停任务）是低风险操作，不需要像"停止"那样二次确认弹窗
  async function handleStart(job: CronTaskUI) {
    try {
      await webRequest<{ job: CronJobDTO }>('cron.job.toggle', { id: job.id, enabled: true });
      setSuccess(t('cron.success.statusUpdated'));
      await loadJobs(projects);
      void reloadCronStore();
    } catch (toggleError) {
      const message = toggleError instanceof Error ? toggleError.message : t('cron.errors.toggleFailed');
      setError(message);
    }
  }

  async function handleRunNowConfirm() {
    if (!confirmState || confirmBusy) return;
    setConfirmBusy(true);
    try {
      const result = await webRequest<{ accepted: boolean; run_id: string; session_id: string }>('cron.job.run_now', { id: confirmState.job.id });
      setSuccess(t('cron.success.runNow'));
      // 刷新左侧栏该定时任务下展开的 session 列表（project.get_cron_sessions）
      const { id: cronId, projectId } = confirmState.job;
      if (cronId && projectId) {
        void loadCronSessions(projectId, cronId);
      }
      // 后端首次执行（job 还没有 last_session_id）时 session_id 会是空串，此时不跳转，
      // 用户仍可通过"触发的会话"列表在会话就绪后手动打开
      if (result.session_id) {
        onSelectSession(result.session_id);
      }
    } catch (runNowError) {
      const message = runNowError instanceof Error ? runNowError.message : t('cron.errors.runNowFailed');
      setError(message);
    } finally {
      setConfirmBusy(false);
      setConfirmState(null);
    }
  }

  async function handleDeleteConfirm() {
    if (!confirmState || confirmBusy) return;
    setConfirmBusy(true);
    try {
      await webRequest<{ deleted: boolean }>('cron.job.delete', { id: confirmState.job.id });
      setSuccess(t('cron.success.deleted'));
      await loadJobs(projects);
      void reloadCronStore();
    } catch (deleteError) {
      const message = deleteError instanceof Error ? deleteError.message : t('cron.errors.deleteFailed');
      setError(message);
    } finally {
      setConfirmBusy(false);
      setConfirmState(null);
    }
  }

  function openTemplateDrawer(tpl: CronTemplateUI) {
    setDrawer({ mode: 'template', initial: templateToForm(tpl, t(tpl.titleKey), t(tpl.descriptionKey)) });
  }

  // "触发的会话"：查这个定时任务名下有哪些会话（含手动/自动触发的执行），点了直接跳转过去。
  // 注意：定时任务真正执行时生成的会话 id 是 `cron_<ts>_<job.id>` 这种格式，不是正常聊天的
  // `sess_...`，工作面板目前只认 `sess_` 前缀的会话可以跳转（App.tsx 多处判断），这部分不是
  // 我们这次要修的范围——能跳的正常跳，跳不了的属于已知限制，等负责这块的同事处理。
  async function toggleSessionsPopover(job: CronTaskUI) {
    if (sessionsPopoverJobId === job.id) {
      setSessionsPopoverJobId(null);
      return;
    }
    setSessionsPopoverJobId(job.id);
    setTriggeredSessionsLoading((prev) => ({ ...prev, [job.id]: true }));
    try {
      const payload = await projectRegistryClient.getCronSessions(job.projectId || 'default', job.id);
      setTriggeredSessions((prev) => ({ ...prev, [job.id]: payload.sessions || [] }));
    } catch {
      setTriggeredSessions((prev) => ({ ...prev, [job.id]: [] }));
    } finally {
      setTriggeredSessionsLoading((prev) => ({ ...prev, [job.id]: false }));
    }
  }

  async function togglePreviewPopover(job: CronTaskUI) {
    if (previewPopoverJobId === job.id) {
      setPreviewPopoverJobId(null);
      return;
    }
    setPreviewPopoverJobId(job.id);
    setPreviewLoading((prev) => ({ ...prev, [job.id]: true }));
    try {
      const payload = await webRequest<{ next: { wake_at: string; push_at: string }[] }>('cron.job.preview', {
        id: job.id,
        count: 3,
        session_id: sessionId,
      });
      setPreviewRuns((prev) => ({ ...prev, [job.id]: payload.next || [] }));
    } catch (previewError) {
      const message = previewError instanceof Error ? previewError.message : t('cron.errors.previewFailed');
      setError(message);
      setPreviewRuns((prev) => ({ ...prev, [job.id]: [] }));
    } finally {
      setPreviewLoading((prev) => ({ ...prev, [job.id]: false }));
    }
  }

  function formatPreviewTime(value: string): string {
    const parsed = new Date(value);
    return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString();
  }

  return (
    <div className="flex-1 min-h-0 relative overflow-y-auto" data-testid="cron-panel" data-session-id={sessionId}>
      {success && (
        <div className="pointer-events-none absolute top-3 left-1/2 -translate-x-1/2 z-20" data-testid="cron-success">
          <div className="bg-ok px-4 py-2 text-sm text-text-inverse rounded-lg shadow-lg animate-rise">{success}</div>
        </div>
      )}
      {error && (
        <div className="pointer-events-none absolute top-3 left-1/2 -translate-x-1/2 z-20" data-testid="cron-error">
          <div className="bg-danger px-4 py-2 text-sm text-text-inverse rounded-lg shadow-lg animate-rise">{error}</div>
        </div>
      )}

      {/* 宽度跟随主窗口自适应（w-[90%]），但用 max-w 封顶避免超宽屏上被拉得过宽，
          两侧留白也不会随窗口变宽而无限增大 */}
      <div className="mx-auto w-[90%] max-w-[1600px] py-8">
        {/* 页头 */}
        <div className="mb-5 flex items-start justify-between">
          <div>
            <h1 className="text-xl font-semibold text-text-strong">{t('cron.pageTitle')}</h1>
            <p className="mt-1 text-sm text-text-muted">{t('cron.pageSubtitle')}</p>
          </div>
          <div className="relative" ref={createMenuRef}>
            <button
              onClick={() => setCreateMenuOpen((v) => !v)}
              className="flex items-center gap-2 rounded-full bg-cron-action px-6 py-1.5 text-sm font-bold text-cron-action-foreground hover:bg-cron-action-hover"
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
                    onCreateViaChat(t('cron.createMenu.viaChatPrompt'));
                  }}
                  className="block w-full px-3 py-2 text-left text-sm font-semibold text-text hover:bg-bg-hover"
                >
                  {t('cron.createMenu.viaChat')}
                </button>
              </div>
            )}
          </div>
        </div>

        {/* Tab 导航 */}
        <div className="mb-4 flex items-center gap-6 border-b border-border">
          {([
            ['list', t('cron.tabs.list')],
            ['template', t('cron.tabs.template')],
            ...(CRON_HISTORY_UI_ENABLED ? [['history', t('cron.tabs.history')]] : []),
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

        {/* 任务总数统计行；任务列表/执行历史都显示，"任务总数"字号跟任务列表表格正文一致（text-sm），
            不用 text-lg 那么突出。任务列表额外带运行中/已暂停/过期三个分类计数（StatPill，复刻自
            阶段1 demo）——这是任务本身状态的完整三态，不依赖后端。"运行失败"属于执行历史维度
            （某一次执行的结果），不属于任务列表，不会出现在这里，见 backend-requests.md #1。
            执行历史目前没有真实执行记录数据（tab 本身也被 CRON_HISTORY_UI_ENABLED 隐藏），先只保留
            总数展示，不编造假的分类计数。空状态页面不显示这一行 */}
        {(activeTab === 'list' || activeTab === 'history') && jobs.length > 0 && (
          <div className="mb-4 flex items-center gap-3">
            <span className="text-sm font-bold text-text-strong">{t('cron.stats.total', { count: jobs.length })}</span>
            {activeTab === 'list' && (
              <>
                <StatPill icon={<span className="text-cron-running"><RunningIcon size={15} /></span>} label={t('cron.status.running')} count={runningCount} />
                <StatPill icon={<span className="text-text-muted"><BoldRingIcon /></span>} label={t('cron.status.paused')} count={pausedCount} />
                <StatPill icon={<span className="text-warn"><BoldRingIcon /></span>} label={t('cron.status.expired')} count={expiredCount} />
              </>
            )}
          </div>
        )}

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
        {activeTab === 'list' && loading && (
          <div className="rounded-lg border border-border bg-secondary/30 px-3 py-4 flex items-center justify-center">
            {t('cron.loading')}
          </div>
        )}
        {activeTab === 'list' && !loading && jobs.length === 0 && (
          <div className="flex min-h-[70vh] flex-col items-center">
            {/* 创建定时任务模块保持在可视区域垂直居中 */}
            <div className="flex flex-1 flex-col items-center justify-center gap-4">
              <img src={emptyIllustration} alt="" className="h-20 w-20" />
              <button onClick={() => setDrawer({ mode: 'create' })} className="btn !px-4 !py-2">
                {t('cron.empty.createButton')}
              </button>
            </div>
            {/* 任务模板模块沉到页面下方，不紧跟在创建按钮下面 */}
            <div className="w-full pb-4">
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
        )}
        {activeTab === 'list' && !loading && jobs.length > 0 && (
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
                              className="inline-flex shrink-0 items-center rounded-full bg-cron-auto-managed-surface px-1.5 py-0.5 text-[10px] font-medium text-cron-auto-managed-text"
                              title={t('cron.autoManagedHint') ?? undefined}
                            >
                              {t('cron.autoManaged')}
                            </span>
                          )}
                        </div>
                      </td>
                      <td className="px-4 py-3 text-text">{job.projectName ?? t('cron.table.noProject')}</td>
                      <td className="px-4 py-3 text-text">{scheduleLabel(job.cronExpr)}</td>
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
                          {/* proactive job 没有真正的"停止"态（enabled 由 config 驱动，不是用户可切的
                              开关，同 StatusBadge 的 enabled 判断），立即执行的禁用条件不看它的 enabled */}
                          {job.expired || (!isProactive && !job.enabled) ? (
                            <span
                              className="text-sm text-text-muted/50 cursor-not-allowed select-none"
                              title={t(job.expired ? 'cron.errors.expiredCannotRunNow' : 'cron.errors.disabledCannotRunNow') ?? undefined}
                            >
                              {t('cron.table.runNow')}
                            </span>
                          ) : (
                            <button
                              onClick={() => setConfirmState({ type: 'runNow', job })}
                              className="text-sm text-cron-action-link hover:opacity-80"
                            >
                              {t('cron.table.runNow')}
                            </button>
                          )}
                          <button
                            onClick={() => setDrawer({ mode: 'edit', initial: jobToForm(job), jobId: job.id })}
                            className="text-sm text-cron-action-link hover:opacity-80"
                          >
                            {t('cron.table.edit')}
                          </button>
                          {isProactive ? (
                            <span className="text-sm text-text-muted/50 cursor-not-allowed select-none" title={t('cron.autoManagedToggleDisabled') ?? undefined}>
                              {t('cron.table.stop')}
                            </span>
                          ) : job.expired ? (
                            <span className="text-sm text-text-muted/50 cursor-not-allowed select-none" title={t('cron.errors.expiredCannotEnable') ?? undefined}>
                              {t('cron.table.start')}
                            </span>
                          ) : job.enabled ? (
                            <button
                              onClick={() => setConfirmState({ type: 'stop', job })}
                              className="text-sm text-cron-action-link hover:opacity-80"
                            >
                              {t('cron.table.stop')}
                            </button>
                          ) : (
                            <button
                              onClick={() => void handleStart(job)}
                              className="text-sm text-cron-action-link hover:opacity-80"
                            >
                              {t('cron.table.start')}
                            </button>
                          )}
                          <div className="relative" ref={rowMenuJobId === job.id ? rowMenuRef : undefined}>
                            <button
                              onClick={() => setRowMenuJobId(rowMenuJobId === job.id ? null : job.id)}
                              className="flex items-center gap-0.5 text-sm text-cron-action-link hover:opacity-80"
                            >
                              {t('cron.table.more')} <ChevronDown size={13} />
                            </button>
                            {rowMenuJobId === job.id && (
                              <div className="absolute left-0 top-[calc(100%+4px)] z-20 w-28 rounded-lg border border-border bg-card py-1.5 shadow-lg">
                                <button
                                  onClick={() => {
                                    setRowMenuJobId(null);
                                    void toggleSessionsPopover(job);
                                  }}
                                  className="block w-full px-3 py-2 text-left text-sm text-cron-action-link hover:bg-bg-hover"
                                >
                                  {t('cron.table.triggeredSessions')}
                                </button>
                                <button
                                  onClick={() => {
                                    setRowMenuJobId(null);
                                    void togglePreviewPopover(job);
                                  }}
                                  className="block w-full px-3 py-2 text-left text-sm text-cron-action-link hover:bg-bg-hover"
                                >
                                  {t('cron.previewAction')}
                                </button>
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
                                    className="block w-full px-3 py-2 text-left text-sm text-danger hover:bg-bg-hover"
                                  >
                                    {t('cron.delete')}
                                  </button>
                                )}
                                {CRON_HISTORY_UI_ENABLED && (
                                  <button
                                    onClick={() => {
                                      setRowMenuJobId(null);
                                      setSuccess(t('cron.history.comingSoon'));
                                    }}
                                    className="block w-full px-3 py-2 text-left text-sm text-cron-action-link hover:bg-bg-hover"
                                  >
                                    {t('cron.table.history')}
                                  </button>
                                )}
                              </div>
                            )}
                          </div>
                        </div>
                        {sessionsPopoverJobId === job.id && (
                          <div
                            ref={sessionsPopoverRef}
                            className="absolute right-4 top-[calc(100%+4px)] z-20 w-64 rounded-lg border border-border bg-card py-1.5 shadow-lg"
                          >
                            <div className="px-3 py-1.5 text-xs font-bold text-text-muted">{t('cron.table.triggeredSessions')}</div>
                            {triggeredSessionsLoading[job.id] && (
                              <div className="px-3 py-2 text-sm text-text-muted">{t('common.loading')}</div>
                            )}
                            {!triggeredSessionsLoading[job.id] && (triggeredSessions[job.id]?.length ?? 0) > 0 && (
                              <div className="max-h-64 overflow-y-auto">
                                {triggeredSessions[job.id].map((s) => (
                                  <button
                                    key={s.session_id}
                                    onClick={() => {
                                      setSessionsPopoverJobId(null);
                                      onSelectSession(s);
                                    }}
                                    className="block w-full truncate px-3 py-2 text-left text-sm text-text hover:bg-bg-hover"
                                    title={s.title}
                                  >
                                    {s.title || s.session_id}
                                  </button>
                                ))}
                              </div>
                            )}
                            {!triggeredSessionsLoading[job.id] && (triggeredSessions[job.id]?.length ?? 0) === 0 && (
                              <div className="px-3 py-2 text-sm text-text-muted">{t('cron.table.noTriggeredSessions')}</div>
                            )}
                          </div>
                        )}
                        {previewPopoverJobId === job.id && (
                          <div
                            ref={previewPopoverRef}
                            className="absolute right-4 top-[calc(100%+4px)] z-20 w-64 rounded-lg border border-border bg-card py-1.5 shadow-lg"
                          >
                            <div className="truncate px-3 py-1.5 text-xs font-bold text-text-muted" title={job.name}>{job.name}</div>
                            {previewLoading[job.id] && (
                              <div className="px-3 py-2 text-sm text-text-muted">{t('cron.preview.loading')}</div>
                            )}
                            {!previewLoading[job.id] && (previewRuns[job.id]?.length ?? 0) > 0 && (
                              <div className="px-3 py-2 text-xs text-text">
                                {previewRuns[job.id].map((item, index) => (
                                  <div key={`${job.id}-${index}`} className="py-0.5">
                                    {t('cron.preview.label', { index: index + 1 })}：{formatPreviewTime(item.push_at)}
                                  </div>
                                ))}
                              </div>
                            )}
                            {!previewLoading[job.id] && (previewRuns[job.id]?.length ?? 0) === 0 && (
                              <div className="px-3 py-2 text-sm text-text-muted">{t('cron.preview.empty')}</div>
                            )}
                          </div>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}

        {/* tab: 任务模板 */}
        {activeTab === 'template' && (
          filteredTemplates.length > 0 ? (
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
          ) : (
            <div className="flex min-h-[30vh] flex-col items-center justify-center gap-2 text-text-muted">
              <p className="text-sm">{t('cron.search.noResults')}</p>
            </div>
          )
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
            loading={confirmBusy}
          />
        )}

        {/* 停止确认弹窗 */}
        {confirmState?.type === 'stop' && (
          <ConfirmDialog
            title={t('cron.confirm.stopTitle')}
            message={t('cron.confirm.stopMessage', { name: confirmState.job.name })}
            onConfirm={() => void handleStopConfirm()}
            onCancel={() => setConfirmState(null)}
            loading={confirmBusy}
          />
        )}

        {/* 立即执行确认弹窗 */}
        {confirmState?.type === 'runNow' && (
          <ConfirmDialog
            title={t('cron.confirm.runNowTitle')}
            message={t('cron.confirm.runNowMessage', { name: confirmState.job.name })}
            onConfirm={() => void handleRunNowConfirm()}
            onCancel={() => setConfirmState(null)}
            loading={confirmBusy}
          />
        )}
      </div>
    </div>
  );
}
