/**
 * App 主组件
 *
 * 应用主布局，整合所有组件
 */

import { useState, useCallback, useEffect, useRef, Component, ReactNode, useMemo } from 'react';
import { ChatPanel } from './components/ChatPanel';
import { SessionSidebar } from './components/SessionSidebar';
import { SkillPanel } from './components/SkillPanel';
import { AgentPanel } from './components/AgentPanel/index';
import { TeamPanel } from './components/TeamPanel';
import { SessionsPanel } from './components/SessionsPanel';
import { HeartbeatPanel } from './components/HeartbeatPanel';
import CronPanel from './components/CronPanel';
import { ToolPanel } from './components/ToolPanel';
import { ConfigPanel } from './components/ConfigPanel';
import { ChannelsPanel } from './components/ChannelsPanel';
import { BrowserPanel } from './components/BrowserPanel';
import { UpdatePanel } from './components/UpdatePanel';
import { ExtensionsHubPanel } from './components/ExtensionsHubPanel';
import {
  ShareImageDocument,
  exportShareImageNode,
  type ShareImageSnapshot,
} from './features/shareImageExport';

import { FEATURE_APP_UPDATER_UI } from './featureFlags';
import { HeartbeatMessageModal } from './features/HeartbeatMessageModal';
import {
  beginHistoryRestore,
  fetchHistoryPage,
  HISTORY_GET_METHOD,
  type HistoryRestoreHandle,
  type HistoryHarnessReplayItem,
  type FetchHistoryPageResult,
} from './features/historyRestore';
import {
  normalizeToolCallPayload,
  normalizeToolResultPayload,
} from './features/tool-events/toolEventNormalizer';
import { useWebSocket } from './hooks';
import { webRequest } from './services/webClient';
import { useTeamPanelState } from './features/teamPanelState';
import { AgentMode, MediaItem, UserAnswer, ModelEntry, type Session } from './types';
import {
  ensureSessionRuntimes,
  useSessionStore,
  useChatStore,
  useTodoStore,
  useHarnessStore,
  useWorkspaceStore,
  useCronStore,
} from './stores';
import { useChatRoute } from './multi-session/routing/useChatRoute';
import { ConversationSidebar, type NewConversationOptions } from './multi-session/sidebar/ConversationSidebar';
import { DeleteDialog } from './multi-session/dialogs/Dialogs';
import {
  NEW_CONVERSATION_ID,
  createConversationTitle,
  forgetCreatedConversation,
  isConversationMissing,
  registerCreatedConversation,
  resetNewConversationRuntime,
} from './multi-session/state/newConversationLifecycle';
import { useTranslation } from 'react-i18next';
import {
  normalizeA2UIEnabled,
  setA2UIFeatureEnabled,
} from './features/a2ui/featureConfig';
import {
  buildA2UIClientEventContent,
  setA2UIActionHandler,
} from './features/a2ui/actionBridge';
import {
  isDesktopSaveCancelled,
  isDesktopSaveOk,
} from './utils/desktopSave';
import type { DesktopSaveApiResult } from './utils/desktopSave';
import './App.css';

type MainNavKey = 'chat' | 'skills' | 'agents' | 'teams' | 'sessions' | 'heartbeat' | 'cron' | 'channels' | 'extensions' | 'configpanel' | 'browserpanel' | 'updatepanel';

type LoadedHistoryPage = {
  pageIdx: number;
  totalPages: number;
  result: FetchHistoryPageResult | null;
};

type AgentsTeamsSavePayload = {
  agents: Record<string, {
    model: { provider: string; api_base: string; api_key: string; model: string };
    skills: string[];
  }>;
  team: Array<{
    team_name: string;
    lifecycle: string;
    teammate_mode: string;
    spawn_mode: string;
    enable_permissions: boolean;
    leader: { member_name: string; display_name: string; persona: string; agent_key: string };
    teammate: { agent_key: string };
    predefined_members: Array<{ member_name: string; display_name: string; persona: string; prompt_hint: string; agent_key: string }>;
  }>;
};

type ConfigSaveAllPayload = {
  config?: Record<string, string>;
  models?: ModelEntry[];
  agents?: AgentsTeamsSavePayload["agents"];
  team?: AgentsTeamsSavePayload["team"];
};

type WindowWithPyWebview = Window & {
  pywebview?: {
    api?: {
      save_data_url?: (
        dataUrl: string,
        filename: string,
      ) => DesktopSaveApiResult;
    };
  };
};

function getWorkContextForSession(sessionId: string): {
  project_id?: string;
  project_dir?: string;
} {
  const sessionState = useSessionStore.getState();
  const workspaceState = useWorkspaceStore.getState();
  const session =
    sessionState.currentSession?.session_id === sessionId
      ? sessionState.currentSession
      : sessionState.sessions.find((item) => item.session_id === sessionId);
  const selectedProject = workspaceState.selectedProject;

  return {
    project_id: session?.project_id || selectedProject?.project_id || undefined,
    project_dir: session?.project_dir || selectedProject?.project_dir || undefined,
  };
}

function clearTeamRuntimeState(sessionId: string): void {
  const sessionStore = useSessionStore.getState();
  sessionStore.setTeamMembers(sessionId, []);
  sessionStore.setTeamTaskEvents(sessionId, []);
  sessionStore.setTeamTasks(sessionId, []);
  sessionStore.setTeamMemberExecutionEvents(sessionId, []);
  sessionStore.clearAllTeamMemberContextCompressionStatus(sessionId);
  sessionStore.setTeamHistoryMessages(sessionId, []);
  sessionStore.setTeamHumanShareCommands(sessionId, []);
}

function waitForNextPaint(): Promise<void> {
  return new Promise((resolve) => {
    requestAnimationFrame(() => resolve());
  });
}

// 错误边界组件
interface ErrorBoundaryState {
  hasError: boolean;
  error: Error | null;
}

class ErrorBoundary extends Component<
  { children: ReactNode },
  ErrorBoundaryState
> {
  constructor(props: { children: ReactNode }) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, errorInfo: React.ErrorInfo) {
    console.error('React Error:', error, errorInfo);
  }

  render() {
    if (this.state.hasError) {
      return <ErrorFallback error={this.state.error} />;
    }
    return this.props.children;
  }
}

function ErrorFallback({ error }: { error: Error | null }) {
  const { t } = useTranslation();
  return (
    <div className="flex items-center justify-center h-screen bg-bg text-text p-8">
      <div className="max-w-2xl card">
        <h1 className="text-2xl font-bold text-danger mb-4">
          {t('app.errorTitle')}
        </h1>
        <p className="text-text-muted mb-4">
          {error?.message || t('app.unknownError')}
        </p>
        <pre className="bg-secondary p-4 rounded-lg text-sm overflow-auto max-h-64 font-mono">
          {error?.stack}
        </pre>
        <button
          onClick={() => window.location.reload()}
          className="btn primary mt-4"
        >
          {t('app.reload')}
        </button>
      </div>
    </div>
  );
}

function generateSessionId(): string {
  const ts = Date.now().toString(16);
  const rand = crypto.randomUUID().replaceAll('-', '').slice(0, 12);
  return `sess_${ts}_${rand}`;
}

function downloadDataUrl(dataUrl: string, filename: string): void {
  const link = document.createElement('a');
  link.href = dataUrl;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
}

// 判断 session_id 是否为可恢复/可展示的会话（web 渠道 sess_ 与 cron 触发的 cron_ 前缀均需支持）
function isRestorableSessionId(sessionId: string): boolean {
  return sessionId.startsWith('sess_') || sessionId.startsWith('cron_');
}

async function saveShareImage(dataUrl: string, filename: string): Promise<boolean> {
  const pywebviewApi = (window as WindowWithPyWebview).pywebview?.api;
  if (pywebviewApi?.save_data_url) {
    const result = await pywebviewApi.save_data_url(dataUrl, filename);
    if (isDesktopSaveCancelled(result)) {
      return false;
    }
    if (!isDesktopSaveOk(result)) {
      throw new Error('share_desktop_save_failed');
    }
    return true;
  }
  downloadDataUrl(dataUrl, filename);
  return true;
}

function AppContent() {
  const { t, i18n } = useTranslation();
  const { route, navigate } = useChatRoute();
  const tRef = useRef(t);
  // 优先使用存储的会话 ID，避免每次刷新创建新会话
  const [sessionId, setSessionId] = useState<string>(() => {
    if (route.kind === 'chat-session') return route.sessionId;
    return 'new';
  });

  const [activeNav, setActiveNav] = useState<MainNavKey>('chat');
  const [serverConfig, setServerConfig] = useState<Record<string, unknown> | null>(null);
  const [configError, setConfigError] = useState<string | null>(null);
  const [initialDataLoaded, setInitialDataLoaded] = useState(false);
  const [restartModalOpen, setRestartModalOpen] = useState(false);
  const [restartSuccess, setRestartSuccess] = useState(false);
  const [isExportingShare, setIsExportingShare] = useState(false);
  const [shareExportSnapshot, setShareExportSnapshot] = useState<ShareImageSnapshot | null>(null);
  const [restartSeenDisconnect, setRestartSeenDisconnect] = useState(false);
  const [appliedWithoutRestart, setAppliedWithoutRestart] = useState(false);
  const [a2uiRefreshPending, setA2uiRefreshPending] = useState(false);
  const [heartbeatToastVisible, setHeartbeatToastVisible] = useState(false);
  const [heartbeatToastMessage, setHeartbeatToastMessage] = useState('');
  const [saveToastVisible, setSaveToastVisible] = useState(false);
  const [configChangedConfirmOpen, setConfigChangedConfirmOpen] = useState(false);
  const [proactiveToastVisible, setProactiveToastVisible] = useState(false);
  const [proactiveToastMessage, setProactiveToastMessage] = useState('');
  const [heartbeatModalOpen, setHeartbeatModalOpen] = useState(false);
  const [securityAlertVisible, setSecurityAlertVisible] = useState(false);
  const [securityAlertContent, setSecurityAlertContent] = useState('');
  const [hasVisitedSkills, setHasVisitedSkills] = useState(false);
  const [hasVisitedChannels, setHasVisitedChannels] = useState(false);
  const [sidebarMorePanelOpen, setSidebarMorePanelOpen] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<Session | null>(null);
  const [dialogBusy, setDialogBusy] = useState(false);
  const [dialogError, setDialogError] = useState<string | null>(null);
  const [composerFocusNonce, setComposerFocusNonce] = useState(0);
  const [missingSessionId, setMissingSessionId] = useState<string | null>(null);
  const startupUpdateCheckRef = useRef(false);
  /** 从 SkillNet 等入口跳转配置页时，首次展开对应配置分组（如第三方服务） */
  const [configInitialExpandGroup, setConfigInitialExpandGroup] = useState<string | null>(null);

  useEffect(() => {
    tRef.current = t;
  }, [t]);

  useEffect(() => {
    if (activeNav !== 'configpanel') {
      setConfigInitialExpandGroup(null);
    }
    if (activeNav === 'chat') {
      const { availableModels, setSelectedModelName } = useSessionStore.getState();
      const defaultModel = availableModels[0];
      const runtime = useSessionStore.getState().getRuntime(sessionId);
      if (defaultModel && !runtime?.selectedModelName) {
        useSessionStore.getState().ensureRuntime(sessionId);
        setSelectedModelName(sessionId, defaultModel.alias || defaultModel.model_name);
      }
    }
  }, [activeNav, sessionId]);

  useEffect(() => {
    if (!FEATURE_APP_UPDATER_UI && activeNav === 'updatepanel') {
      setActiveNav('chat');
    }
  }, [activeNav]);

  useEffect(() => {
    const handler = (e: Event) => {
      const nav = (e as CustomEvent<MainNavKey>).detail;
      if (nav) setActiveNav(nav);
    };
    window.addEventListener('jiuwen:nav', handler);
    return () => window.removeEventListener('jiuwen:nav', handler);
  }, []);

  const restartAutoCloseTimerRef = useRef<number | null>(null);
  const heartbeatToastTimerRef = useRef<number | null>(null);
  const saveToastTimerRef = useRef<number | null>(null);
  const proactiveToastTimerRef = useRef<number | null>(null);
  const hasChangesRef = useRef(false);
  const lastHeartbeatToastKeyRef = useRef<string | null>(null);
  const [historyLoadingMore, setHistoryLoadingMore] = useState(false);
  const [historyPrepending, setHistoryPrepending] = useState(false);
  /** 仅用于强制重跑「首屏 history」effect：从会话列表恢复时若 sessionId 未变，也要重新拉 history 并恢复 historyPagerMeta */
  const [historyBootstrapKey, setHistoryBootstrapKey] = useState(0);
  const sessionIdRef = useRef(sessionId);
  const historyLoadingSessionsRef = useRef(new Set<string>());
  const historyRestoreHandlesRef = useRef(new Map<string, HistoryRestoreHandle>());
  const historyPageHandlesRef = useRef(new Map<string, HistoryRestoreHandle>());
  const historyPagePromisesRef = useRef(new Map<string, Promise<LoadedHistoryPage | null>>());
  const historyPageCancelRef = useRef(new Map<string, () => void>());
  const historyBackgroundPrefetchTokensRef = useRef(new Map<string, number>());
  const creatingSessionRef = useRef(false);
  const promotedFromNewSessionIdsRef = useRef(new Set<string>());
  const shareExportRef = useRef<HTMLDivElement>(null);
  const shareExportFilenameRef = useRef('jiuwenswarm-share.png');
  const shareExportTokenRef = useRef(0);
  const preserveSelectedProjectOnChatNewRef = useRef(false);
  const newConversationProjectRef = useRef<Pick<Session, 'project_id' | 'project_dir'> | null>(null);
  /** 为 true 表示刚从「会话列表」恢复；history 为空时在 useEffect 的 onEmpty 中提示一次 */
  const historyRestoreFromPanelHintRef = useRef(false);
  const { loadProjects, setSelectedProject } = useWorkspaceStore();


  useEffect(() => {
    sessionIdRef.current = sessionId;
    setHistoryLoadingMore(false);
    setHistoryPrepending(historyLoadingSessionsRef.current.has(sessionId));
  }, [sessionId]);

  const {
    teamAreaExpanded,
    teamAreaActiveTab,
    teamAreaActiveDetailTab,
    teamAreaSelectedMemberId,
    setTeamAreaExpanded,
    setTeamAreaActiveTab,
    setTeamAreaActiveDetailTab,
    setTeamAreaSelectedMemberId,
  } = useTeamPanelState();

  useEffect(() => {
    if (route.kind === 'chat-session') {
      sessionIdRef.current = route.sessionId;
      setSessionId(route.sessionId);
      setActiveNav('chat');
    } else if (route.kind === 'chat-new') {
      if (window.location.pathname !== '/chat/new') navigate({ kind: 'chat-new' }, { replace: true });
      if (preserveSelectedProjectOnChatNewRef.current) {
        preserveSelectedProjectOnChatNewRef.current = false;
      } else {
        useWorkspaceStore.getState().setSelectedProject(null);
      }
      sessionIdRef.current = 'new';
      setSessionId('new');
      setActiveNav('chat');
      setTeamAreaExpanded(false);
    }
  }, [navigate, route, setTeamAreaExpanded]);

  useEffect(() => {
    ensureSessionRuntimes(sessionId);
    useChatStore.getState().setActiveSessionId(sessionId);
  }, [sessionId]);

  useEffect(() => {
    if (!initialDataLoaded) {
      return;
    }
    void loadProjects();
  }, [initialDataLoaded, loadProjects]);

  const { setCurrentSession, setAvailableModels, setMode, heartbeatMessage, heartbeatUpdatedAt, setTeamLeaderMemberIds } = useSessionStore();
  const sessions = useSessionStore((s) => s.sessions);
  const currentSession = useSessionStore((s) => s.currentSession);
  const routeSessionId = route.kind === 'chat-session' ? route.sessionId : null;
  const projects = useWorkspaceStore((s) => s.projects);
  const sessionTitle = useMemo(() => {
    const session = currentSession?.session_id === sessionId
      ? currentSession
      : sessions.find((s) => s.session_id === sessionId);
    return session?.title?.trim() ?? '';
  }, [currentSession, sessions, sessionId]);
  const sessionProjectName = useMemo(() => {
    const session = sessions.find((s) => s.session_id === sessionId);
    if (!session?.project_dir) return '';
    const project = projects.find((item) => !item.is_default && item.project_dir === session.project_dir);
    return project?.name?.trim() ?? '';
  }, [projects, sessions, sessionId]);
  const mode = useSessionStore((s) => s.runtimes[sessionId]?.mode ?? 'agent');
  const teamTaskEvents = useSessionStore((s) => s.runtimes[sessionId]?.teamTaskEvents ?? []);
  const teamTasks = useSessionStore((s) => s.runtimes[sessionId]?.teamTasks ?? []);
  const teamMembers = useSessionStore((s) => s.runtimes[sessionId]?.teamMembers ?? []);
  const [chatPanelWidthPct, setChatPanelWidthPct] = useState(33.33);

  const handleToggleDetailPanel = useCallback((expanded: boolean) => {
    if (expanded && mode !== 'team' && teamAreaActiveTab === 'team') {
      setTeamAreaActiveTab('planning');
    }
    setTeamAreaExpanded(expanded);
  }, [mode, setTeamAreaActiveTab, setTeamAreaExpanded, teamAreaActiveTab]);

  const handleDividerMouseDown = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    const startX = e.clientX;
    const startPct = chatPanelWidthPct;
    const container = (e.currentTarget as HTMLElement).parentElement;
    if (!container) return;
    const containerWidth = container.getBoundingClientRect().width;

    const onMouseMove = (ev: MouseEvent) => {
      const dx = ev.clientX - startX;
      const newPct = Math.min(70, Math.max(20, startPct + (dx / containerWidth) * 100));
      setChatPanelWidthPct(newPct);
    };

    const onMouseUp = () => {
      document.removeEventListener('mousemove', onMouseMove);
      document.removeEventListener('mouseup', onMouseUp);
    };

    document.addEventListener('mousemove', onMouseMove);
    document.addEventListener('mouseup', onMouseUp);
  }, [chatPanelWidthPct]);

  const clearMessages = useChatStore((s) => s.clearMessages);
  const clearSubtasks = useChatStore((s) => s.clearSubtasks);
  const addMessage = useChatStore((s) => s.addMessage);
  const addToolCall = useChatStore((s) => s.addToolCall);
  const addToolResult = useChatStore((s) => s.addToolResult);
  const prependMessages = useChatStore((s) => s.prependMessages);
  const isProcessing = useChatStore((s) => s.runtimes[sessionId]?.isProcessing ?? false);
  const isPaused = useChatStore((s) => s.runtimes[sessionId]?.isPaused ?? false);
  const hasPendingQuestion = useChatStore((s) => Boolean(s.runtimes[sessionId]?.pendingQuestion));
  const setProcessing = useChatStore((s) => s.setProcessing);
  const setThinking = useChatStore((s) => s.setThinking);
  const setLoadingHistory = useChatStore((s) => s.setLoadingHistory);
  const setHistoryPagerMeta = useChatStore((s) => s.setHistoryPagerMeta);
  /** 自「恢复会话」加载 history 后的分页元数据；与消息一样按 session 隔离。 */
  const historyPagerMeta = useChatStore((s) => s.runtimes[sessionId]?.historyPagerMeta ?? null);
  const setPaused = useChatStore((s) => s.setPaused);
  const messages = useChatStore((s) => s.runtimes[sessionId]?.messages ?? []);
  const isLoadingHistory = useChatStore((s) => s.runtimes[sessionId]?.isLoadingHistory ?? false);
  const replaceHistoryMessages = useChatStore((s) => s.replaceHistoryMessages);
  const isRestoringHistorySession = isRestorableSessionId(sessionId) && isLoadingHistory && !historyPagerMeta && messages.length === 0;
  const isRestoringTeamHistory = mode === 'team' && isRestoringHistorySession;

  useEffect(() => {
    if (!serverConfig) {
      if (sessionId) setTeamLeaderMemberIds(sessionId, []);
      return;
    }
    const leaderIds = Object.entries(serverConfig)
      .filter(([key]) => /^team_leader_member_name_\d+$/.test(key) || /^team_\d+_leader_member_name$/.test(key))
      .map(([, value]) => (typeof value === 'string' ? value.trim() : ''))
      .filter(Boolean);
    if (sessionId) setTeamLeaderMemberIds(sessionId, leaderIds);
  }, [serverConfig, sessionId, setTeamLeaderMemberIds]);

  const disposeInFlightHistoryHandles = useCallback((sid?: string) => {
    const cancelSession = (targetSid: string) => {
      const prevToken = historyBackgroundPrefetchTokensRef.current.get(targetSid) ?? 0;
      historyBackgroundPrefetchTokensRef.current.set(targetSid, prevToken + 1);
      historyLoadingSessionsRef.current.delete(targetSid);
      if (targetSid === sessionIdRef.current) {
        setHistoryPrepending(false);
        setHistoryLoadingMore(false);
      }
      setLoadingHistory(targetSid, false);
      historyRestoreHandlesRef.current.get(targetSid)?.dispose();
      historyRestoreHandlesRef.current.delete(targetSid);
      for (const [key, handle] of Array.from(historyPageHandlesRef.current.entries())) {
        if (!key.startsWith(`${targetSid}:`)) continue;
        handle.dispose();
        historyPageHandlesRef.current.delete(key);
        historyPagePromisesRef.current.delete(key);
        historyPageCancelRef.current.get(key)?.();
        historyPageCancelRef.current.delete(key);
      }
    };

    if (sid) {
      cancelSession(sid);
      return;
    }

    for (const targetSid of new Set([
      ...historyRestoreHandlesRef.current.keys(),
      ...Array.from(historyPageHandlesRef.current.keys(), (key) => key.split(':', 1)[0]),
      ...historyLoadingSessionsRef.current,
    ])) {
      cancelSession(targetSid);
    }
  }, [setLoadingHistory]);

  useEffect(() => () => disposeInFlightHistoryHandles(), [disposeInFlightHistoryHandles]);
  const todos = useTodoStore((s) => s.runtimes[sessionId]?.todos ?? []);
  const clearTodos = useTodoStore((s) => s.clearTodos);
  const extensionReady = useHarnessStore((s) => s.runtimes[sessionId]?.extensionReady ?? null);
  const resetHarnessStore = useHarnessStore((s) => s.reset);
  const proactiveNotificationMessage = useHarnessStore((s) => s.proactiveNotificationMessage);
  const setProactiveNotification = useHarnessStore((s) => s.setProactiveNotification);

  const toolPanelHasContent = useMemo(() => {
    const hasMessages = messages.length > 0;
    switch (mode) {
      case 'auto_harness':
        return Boolean(extensionReady?.runtimePath) || hasMessages;
      case 'team':
        return isRestoringTeamHistory || teamTaskEvents.length > 0 || teamTasks.length > 0 || teamMembers.length > 0 || hasMessages;
      default:
        return todos.length > 0 || hasMessages;
    }
  }, [mode, todos.length, teamTaskEvents.length, teamTasks.length, teamMembers.length, extensionReady?.runtimePath, messages.length, isRestoringTeamHistory]);
  // 单 agent 模式同样复用集群模式的展开布局（百分比宽度 + 可拖拽分割线），
  // 避免右侧面板与聊天面板平分空间导致宽度与集群模式不一致；auto_harness 走收起态分支。
  const isTeamAreaExpanded = mode !== 'auto_harness' && teamAreaExpanded && toolPanelHasContent;

  // WebSocket 连接 - provider 由后端配置决定 - provider 由后端配置决定，前端默认不在 URL query 传递
  const {
    isConnected,
    request,
    persistMedia,
    sendMessage,
    sendStructuredChatContent,
    pause,
    cancel,
    supplement,
    sendUserAnswer,
  } = useWebSocket({
    activeSessionId: sessionId,
    onConnect: () => console.log('Connected'),
    onDisconnect: () => {
      console.log('Disconnected');
    },
    onError: (error) => {
      console.error('WebSocket error:', error);
    },
    onConfigChanged: () => {
      handleConfigChanged();
    },
  });

  const applyHistoryPageResult = useCallback((sid: string, result: FetchHistoryPageResult) => {
    prependMessages(sid, result.messages);
    for (const item of result.toolReplay) {
      if (item.kind === 'tool_call') {
        const n = normalizeToolCallPayload(item.payload);
        addToolCall(
          sid,
          {
            id: n.id,
            name: n.name,
            arguments: n.arguments,
            description: n.description,
            formatted_args: n.formatted_args,
            memberName: n.memberName,
          },
          { startedAt: item.at }
        );
      } else {
        const n = normalizeToolResultPayload(item.payload);
        addToolResult(
          sid,
          {
            toolName: n.toolName,
            result: n.result,
            success: n.success,
            toolCallId: n.toolCallId,
            summary: n.summary,
            skillTree: n.skillTree,
          },
          { updatedAt: item.at }
        );
      }
    }

    const harnessStore = useHarnessStore.getState();
    const harnessRuntime = harnessStore.getRuntime(sid);
    for (const item of result.harnessReplay) {
      if (item.kind === 'harness_message') {
        const content = typeof item.payload.content === 'string' ? item.payload.content : '';
        const stage = typeof item.payload.stage === 'string' ? item.payload.stage : undefined;
        if (content) {
          harnessStore.addHarnessMessage(sid, content, stage);
          if (stage) {
            const existingStage = harnessRuntime?.stageResults.find((s) => s.stage === stage);
            if (existingStage?.status !== 'running') {
              harnessStore.updateStageResult(sid, {
                stage,
                stageLabel: content,
                status: 'running',
                messages: [],
                metrics: {},
              });
            }
          }
        }
      } else if (item.kind === 'harness_stage_result') {
        const stage = typeof item.payload.stage === 'string' ? item.payload.stage : '';
        const status = typeof item.payload.status === 'string' ? item.payload.status : 'success';
        const error = typeof item.payload.error === 'string' ? item.payload.error : undefined;
        const messages = Array.isArray(item.payload.messages) ? item.payload.messages : [];
        const metrics = item.payload.metrics || {};
        if (stage) {
          harnessStore.updateStageResult(sid, {
            stage,
            status: status as 'success' | 'failed' | 'timeout',
            error,
            messages,
            metrics,
          });
        }
      }
    }
  }, [addToolCall, addToolResult, prependMessages]);

  const fetchHistoryPageResult = useCallback(async (
    sid: string,
    pageIdx: number,
    fallbackTotalPages: number
  ): Promise<LoadedHistoryPage | null> => {
    const pageKey = `${sid}:${pageIdx}`;
    const existingPromise = historyPagePromisesRef.current.get(pageKey);
    if (existingPromise) return existingPromise;

    const promise = new Promise<LoadedHistoryPage | null>((resolve) => {
      let settled = false;
      const settleCanceled = () => settle(null);
      const settle = (page: LoadedHistoryPage | null) => {
        if (settled) return;
        settled = true;
        if (historyPageCancelRef.current.get(pageKey) === settleCanceled) {
          historyPageCancelRef.current.delete(pageKey);
        }
        historyPageHandlesRef.current.delete(pageKey);
        historyPagePromisesRef.current.delete(pageKey);
        resolve(page);
      };
      historyPageCancelRef.current.set(pageKey, settleCanceled);

      const pageHandle = fetchHistoryPage({
        sessionId: sid,
        pageIdx,
        onReady: (result) => {
          const totalPages = result.totalPages ?? fallbackTotalPages;
          settle({ pageIdx, totalPages, result });
        },
        onEmpty: (emptyTotalPages) => {
          const totalPages = emptyTotalPages ?? fallbackTotalPages;
          settle({ pageIdx, totalPages, result: null });
        },
        onError: (message) => {
          console.warn('[history.page]', message);
        },
      });
      historyPageHandlesRef.current.set(pageKey, pageHandle);

      void request(HISTORY_GET_METHOD, {
        session_id: sid,
        page_idx: pageIdx,
      }).catch((error) => {
        pageHandle.dispose();
        if (historyPageHandlesRef.current.get(pageKey) === pageHandle) {
          historyPageHandlesRef.current.delete(pageKey);
        }
        console.error('Failed to load older history:', error);
        settle(null);
      });
    });
    historyPagePromisesRef.current.set(pageKey, promise);
    return promise;
  }, [request]);

  const applyLoadedHistoryPage = useCallback((sid: string, page: LoadedHistoryPage) => {
    if (page.result) {
      applyHistoryPageResult(sid, page.result);
    }
    setHistoryPagerMeta(sid, {
      loadedPages: page.pageIdx,
      totalPages: page.totalPages,
    });
  }, [applyHistoryPageResult, setHistoryPagerMeta]);

  const startBackgroundHistoryPrefetch = useCallback((sid: string, initialLoadedPages: number, initialTotalPages: number) => {
    if (initialLoadedPages >= initialTotalPages) return;
    const token = (historyBackgroundPrefetchTokensRef.current.get(sid) ?? 0) + 1;
    historyBackgroundPrefetchTokensRef.current.set(sid, token);

    void (async () => {
      let loadedPages = initialLoadedPages;
      let totalPages = initialTotalPages;
      while (
        token === historyBackgroundPrefetchTokensRef.current.get(sid) &&
        loadedPages < totalPages
      ) {
        if (historyLoadingSessionsRef.current.has(sid)) {
          return;
        }
        const nextPage = loadedPages + 1;
        historyLoadingSessionsRef.current.add(sid);
        if (sessionIdRef.current === sid) {
          setHistoryPrepending(true);
        }
        const page = await fetchHistoryPageResult(sid, nextPage, totalPages);
        historyLoadingSessionsRef.current.delete(sid);
        if (sessionIdRef.current === sid) {
          setHistoryPrepending(false);
        }
        if (
          page == null ||
          token !== historyBackgroundPrefetchTokensRef.current.get(sid)
        ) {
          return;
        }
        applyLoadedHistoryPage(sid, page);
        loadedPages = nextPage;
        totalPages = page.totalPages;
        await waitForNextPaint();
      }
    })();
  }, [applyLoadedHistoryPage, fetchHistoryPageResult]);

  const upsertSessionMetadata = useCallback((session: Session, options: { setCurrent?: boolean } = {}) => {
    const sessionStore = useSessionStore.getState();
    const exists = sessionStore.sessions.some((item) => item.session_id === session.session_id);
    if (exists) {
      sessionStore.updateSession(session.session_id, session);
    } else {
      sessionStore.addSession(session);
    }
    if (options.setCurrent) {
      sessionStore.setCurrentSession(session);
    }
  }, []);

  const loadSessionMetadata = useCallback(async (targetSessionId: string): Promise<Session | null> => {
    if (!isRestorableSessionId(targetSessionId)) return null;
    try {
      const session = await request<Session>('session.get_metadata', {
        session_id: targetSessionId,
      });
      upsertSessionMetadata(session, { setCurrent: sessionIdRef.current === targetSessionId });
      if (sessionIdRef.current === targetSessionId) {
        setMissingSessionId((current) => (current === targetSessionId ? null : current));
      }
      return session;
    } catch (error) {
      console.warn('Failed to fetch session metadata:', error);
      if (sessionIdRef.current === targetSessionId) {
        setMissingSessionId(targetSessionId);
      }
      return null;
    }
  }, [request, upsertSessionMetadata]);

  // 获取服务端配置（通过 WS 方法）
  const fetchConfig = useCallback(async () => {
    try {
      const config = await request<Record<string, unknown>>('config.get');
      setA2UIFeatureEnabled(normalizeA2UIEnabled(config.a2ui_enabled));
      setServerConfig(config);
      setConfigError(null);
    } catch (error) {
      console.error('Failed to fetch config:', error);
      setServerConfig(null);
      setConfigError(t('app.configError'));
    }
    // 同步获取多模型列表
    try {
      const resp = await request<{ models: ModelEntry[]; active_model: string }>('models.list');
      if (resp?.models) {
        setAvailableModels(resp.models, resp.active_model);
      }
    } catch (error) {
      console.warn('Failed to fetch models list:', error);
    }
  }, [request, t, setAvailableModels]);

  useEffect(() => {
    if (!FEATURE_APP_UPDATER_UI || !isConnected || startupUpdateCheckRef.current) {
      return;
    }
    startupUpdateCheckRef.current = true;
    const timeoutId = window.setTimeout(() => {
      void request('updater.check', { manual: false })
        .then((payload) => {
          window.dispatchEvent(new CustomEvent('jiuwenswarm:updater-status', { detail: payload }));
        })
        .catch((updateError) => {
          console.warn('Startup updater check failed:', updateError);
        });
    }, 5000);
    return () => {
      window.clearTimeout(timeoutId);
    };
  }, [isConnected, request]);

  const clearRestartAutoCloseTimer = useCallback(() => {
    if (restartAutoCloseTimerRef.current != null) {
      window.clearTimeout(restartAutoCloseTimerRef.current);
      restartAutoCloseTimerRef.current = null;
    }
  }, []);

  const closeRestartModal = useCallback(() => {
    clearRestartAutoCloseTimer();
    setRestartModalOpen(false);
    setRestartSuccess(false);
    setRestartSeenDisconnect(false);
    setAppliedWithoutRestart(false);
    setA2uiRefreshPending(false);
  }, [clearRestartAutoCloseTimer]);

  const clearHeartbeatToastTimer = useCallback(() => {
    if (heartbeatToastTimerRef.current != null) {
      window.clearTimeout(heartbeatToastTimerRef.current);
      heartbeatToastTimerRef.current = null;
    }
  }, []);

  const clearSaveToastTimer = useCallback(() => {
    if (saveToastTimerRef.current != null) {
      window.clearTimeout(saveToastTimerRef.current);
      saveToastTimerRef.current = null;
    }
  }, []);

  const clearProactiveToastTimer = useCallback(() => {
    if (proactiveToastTimerRef.current != null) {
      window.clearTimeout(proactiveToastTimerRef.current);
      proactiveToastTimerRef.current = null;
    }
  }, []);

  const showSaveToast = useCallback(() => {
    setSaveToastVisible(true);
    clearSaveToastTimer();
    saveToastTimerRef.current = window.setTimeout(() => {
      setSaveToastVisible(false);
      saveToastTimerRef.current = null;
    }, 3000);
  }, [clearSaveToastTimer]);

  const securityAlertTimerRef = useRef<number | null>(null);

  useEffect(() => {
    const handleSecurityAlert = (e: CustomEvent) => {
      setSecurityAlertContent(e.detail.message);
      setSecurityAlertVisible(true);
      if (securityAlertTimerRef.current) {
        clearTimeout(securityAlertTimerRef.current);
      }
      securityAlertTimerRef.current = window.setTimeout(() => {
        setSecurityAlertVisible(false);
        securityAlertTimerRef.current = null;
      }, 5000);
    };
    window.addEventListener('security-alert', handleSecurityAlert as EventListener);
    return () => {
      window.removeEventListener('security-alert', handleSecurityAlert as EventListener);
      if (securityAlertTimerRef.current) clearTimeout(securityAlertTimerRef.current);
    };
  }, []);

  const validateModelConfig = useCallback(
    async (fields: {
      api_base: string;
      api_key: string;
      model: string;
      model_provider: string;
      reasoning_level?: string;
    }) => {
      await request('config.validate_model', fields, { timeoutMs: 60000 });
    },
    [request],
  );

  const handleModelsReplaceAll = useCallback(async (models: ModelEntry[]) => {
    await request('models.replace_all', { models });
  }, [request]);

  const handleConfigChanged = useCallback(() => {
    if (hasChangesRef.current) {
      setConfigChangedConfirmOpen(true);
      return;
    }
    void fetchConfig();
  }, [fetchConfig]);

  const handleHasChangesChange = useCallback((hasChanges: boolean) => {
    hasChangesRef.current = hasChanges;
  }, []);

  const handleModelsRefresh = useCallback(async () => {
    try {
      const resp = await request<{ models: ModelEntry[]; active_model: string }>('models.list');
      if (resp?.models) {
        setAvailableModels(resp.models, resp.active_model);
      }
    } catch (error) {
      console.warn('Failed to refresh models list:', error);
    }
  }, [request, setAvailableModels]);

  const saveConfigAndRestart = useCallback(async (updates: Record<string, string>) => {
    const payload = await request<{ updated?: string[]; applied_without_restart?: boolean }>(
      'config.set',
      updates
    );
    setServerConfig((prev) => {
      if (!prev) return updates;
      const next: Record<string, unknown> = { ...prev, ...updates };
      // Keep the bilingual memory_forbidden_description dictionary structure.
      if (typeof prev?.memory_forbidden_description === 'object' && prev.memory_forbidden_description !== null
          && !Array.isArray(prev.memory_forbidden_description) && updates.memory_forbidden_description !== undefined) {
        const prevDict = prev.memory_forbidden_description as Record<string, string>;
        const lang = i18n.language || 'zh';
        next.memory_forbidden_description = { ...prevDict, [lang]: updates.memory_forbidden_description };
      }
      return next;
    });
    setConfigError(null);
    setRestartModalOpen(true);
    setRestartSuccess(false);
    setRestartSeenDisconnect(false);
    if ('a2ui_enabled' in updates) {
      setAppliedWithoutRestart(false);
      setA2uiRefreshPending(true);
      setRestartSuccess(true);
      clearRestartAutoCloseTimer();
      restartAutoCloseTimerRef.current = window.setTimeout(() => {
        closeRestartModal();
        window.location.reload();
      }, 5000);
    } else {
      setAppliedWithoutRestart(payload?.applied_without_restart === true);
      clearRestartAutoCloseTimer();
      if (payload?.applied_without_restart === true) {
        setRestartSuccess(true);
        restartAutoCloseTimerRef.current = window.setTimeout(() => {
          closeRestartModal();
        }, 5000);
      }
    }
  }, [clearRestartAutoCloseTimer, closeRestartModal, request]);

  const savePermissionSilent = useCallback(async (updates: Record<string, string>) => {
    try {
      await request<{ updated?: string[]; applied_without_restart?: boolean }>('config.set', updates);
      setServerConfig((prev) => {
        if (!prev) return updates;
        return { ...prev, ...updates };
      });
    } catch (error) {
      console.error('Failed to save permission:', error);
      setRestartModalOpen(true);
      setRestartSuccess(false);
      setRestartSeenDisconnect(false);
      setAppliedWithoutRestart(false);
    }
  }, [request]);

  const applyConfigSaveUiState = useCallback((appliedWithoutRestart: boolean) => {
    setConfigError(null);
    setRestartModalOpen(true);
    setRestartSuccess(false);
    setRestartSeenDisconnect(false);
    setAppliedWithoutRestart(appliedWithoutRestart);
    clearRestartAutoCloseTimer();
    if (appliedWithoutRestart) {
      setRestartSuccess(true);
      restartAutoCloseTimerRef.current = window.setTimeout(() => {
        closeRestartModal();
      }, 5000);
    }
  }, [clearRestartAutoCloseTimer, closeRestartModal]);

  const buildAgentsTeamsFlatConfig = useCallback((payload: AgentsTeamsSavePayload) => {
    const updates: Record<string, string> = {};
    const agentCount = Object.keys(payload.agents).length;
    Object.entries(payload.agents).forEach(([name, agent], idx) => {
      updates[`agent_name_${idx}`] = name;
      updates[`agent_model_${idx}`] = agent.model.model;
      updates[`agent_skills_${idx}`] = agent.skills.join(',');
    });
    for (let i = agentCount; i < 10; i++) {
      updates[`agent_name_${i}`] = "";
      updates[`agent_model_${i}`] = "";
      updates[`agent_skills_${i}`] = "";
    }
    payload.team.forEach((team, idx) => {
      // 使用与后端一致的键名格式：team_${idx}_name
      updates[`team_${idx}_name`] = team.team_name;
      updates[`team_${idx}_lifecycle`] = team.lifecycle;
      updates[`team_${idx}_teammate_mode`] = team.teammate_mode;
      updates[`team_${idx}_spawn_mode`] = team.spawn_mode;
      updates[`team_${idx}_enable_permissions`] = String(team.enable_permissions);
      updates[`team_${idx}_leader_member_name`] = team.leader.member_name;
      updates[`team_${idx}_leader_display_name`] = team.leader.display_name;
      updates[`team_${idx}_leader_persona`] = team.leader.persona;
      updates[`team_${idx}_leader_agent_key`] = team.leader.agent_key;
      updates[`team_${idx}_teammate_agent_key`] = team.teammate.agent_key;
      updates[`team_${idx}_predefined_members`] = team.predefined_members?.length
        ? JSON.stringify(team.predefined_members)
        : "";
    });
    for (let i = payload.team.length; i < 10; i++) {
      // 使用与后端一致的键名格式：team_${i}_name
      updates[`team_${i}_name`] = "";
      updates[`team_${i}_lifecycle`] = "";
      updates[`team_${i}_teammate_mode`] = "";
      updates[`team_${i}_spawn_mode`] = "";
      updates[`team_${i}_enable_permissions`] = "";
      updates[`team_${i}_leader_member_name`] = "";
      updates[`team_${i}_leader_display_name`] = "";
      updates[`team_${i}_leader_persona`] = "";
      updates[`team_${i}_leader_agent_key`] = "";
      updates[`team_${i}_teammate_agent_key`] = "";
      updates[`team_${i}_predefined_members`] = "";
    }
    return updates;
  }, []);

  const handleAgentsTeamsSave = useCallback(async (payload: AgentsTeamsSavePayload) => {
    const result = await request<{ updated?: string[]; applied_without_restart?: boolean }>(
      'config.set',
      payload as unknown as Record<string, string>
    );
    // 更新前端配置缓存
    const updates = buildAgentsTeamsFlatConfig(payload);
    setServerConfig((prev: Record<string, unknown> | null) => ({ ...prev, ...updates }));
    applyConfigSaveUiState(result?.applied_without_restart === true);
  }, [applyConfigSaveUiState, buildAgentsTeamsFlatConfig, request]);

  const saveAllConfigAndRestart = useCallback(async (payload: ConfigSaveAllPayload) => {
    const isA2UIChange = payload.config && 'a2ui_enabled' in payload.config;
    const result = await request<{ updated?: string[]; applied_without_restart?: boolean }>(
      'config.save_all',
      payload as unknown as Record<string, unknown>
    );
    setServerConfig((prev) => {
      const next: Record<string, unknown> = { ...(prev ?? {}) };
      if (payload.config) {
        Object.assign(next, payload.config);
        if (typeof prev?.memory_forbidden_description === 'object' && prev.memory_forbidden_description !== null
            && !Array.isArray(prev.memory_forbidden_description)
            && payload.config.memory_forbidden_description !== undefined) {
          const prevDict = prev.memory_forbidden_description as Record<string, string>;
          const lang = i18n.language || 'zh';
          next.memory_forbidden_description = {
            ...prevDict,
            [lang]: payload.config.memory_forbidden_description,
          };
        }
      }
      if (payload.agents !== undefined || payload.team !== undefined) {
        const agents = payload.agents || {};
        const team = payload.team || [];
        Object.assign(next, buildAgentsTeamsFlatConfig({
          agents,
          team,
        }));
      }
      return next;
    });
    if (isA2UIChange) {
      // Show modal then refresh page after 5 seconds
      setConfigError(null);
      setRestartModalOpen(true);
      setRestartSuccess(true);
      setRestartSeenDisconnect(false);
      setAppliedWithoutRestart(false);
      setA2uiRefreshPending(true);
      clearRestartAutoCloseTimer();
      restartAutoCloseTimerRef.current = window.setTimeout(() => {
        closeRestartModal();
        window.location.reload();
      }, 5000);
    } else {
      applyConfigSaveUiState(result?.applied_without_restart === true);
    }
  }, [applyConfigSaveUiState, buildAgentsTeamsFlatConfig, i18n.language, request]);

  useEffect(() => {
    if (!restartModalOpen || restartSuccess) {
      return;
    }
    if (!isConnected) {
      setRestartSeenDisconnect(true);
      return;
    }
    if (restartSeenDisconnect && isConnected) {
      setRestartSuccess(true);
      clearRestartAutoCloseTimer();
      restartAutoCloseTimerRef.current = window.setTimeout(() => {
        closeRestartModal();
      }, 5000);
    }
  }, [
    clearRestartAutoCloseTimer,
    closeRestartModal,
    isConnected,
    restartModalOpen,
    restartSeenDisconnect,
    restartSuccess,
  ]);

  useEffect(() => {
    return () => {
      clearRestartAutoCloseTimer();
      clearHeartbeatToastTimer();
      clearSaveToastTimer();
      clearProactiveToastTimer();
    };
  }, [clearHeartbeatToastTimer, clearProactiveToastTimer, clearRestartAutoCloseTimer, clearSaveToastTimer]);

  useEffect(() => {
    const normalized = heartbeatMessage?.trim();
    if (!normalized) {
      return;
    }
    if (normalized.toUpperCase() === 'HEARTBEAT_OK') {
      return;
    }
    const toastKey = `${heartbeatUpdatedAt ?? ''}::${normalized}`;
    if (lastHeartbeatToastKeyRef.current === toastKey) {
      return;
    }
    lastHeartbeatToastKeyRef.current = toastKey;
    setHeartbeatToastMessage(normalized);
    setHeartbeatToastVisible(true);
    clearHeartbeatToastTimer();
    heartbeatToastTimerRef.current = window.setTimeout(() => {
      setHeartbeatToastVisible(false);
      heartbeatToastTimerRef.current = null;
    }, 15000);
  }, [clearHeartbeatToastTimer, heartbeatMessage, heartbeatUpdatedAt]);

  useEffect(() => {
    const message = proactiveNotificationMessage?.trim();
    if (!message) return;
    setProactiveToastMessage(message);
    setProactiveToastVisible(true);
    clearProactiveToastTimer();
    proactiveToastTimerRef.current = window.setTimeout(() => {
      setProactiveToastVisible(false);
      setProactiveNotification(null);
      proactiveToastTimerRef.current = null;
    }, 8000);
  }, [clearProactiveToastTimer, proactiveNotificationMessage, setProactiveNotification]);

  useEffect(() => {
    if (!isConnected || initialDataLoaded) {
      return;
    }
    void (async () => {
      await fetchConfig();
      setInitialDataLoaded(true);
    })();
  }, [fetchConfig, initialDataLoaded, isConnected]);

  useEffect(() => {
    if (!isConnected || !routeSessionId) {
      setMissingSessionId(null);
      return;
    }
    void loadSessionMetadata(routeSessionId);
  }, [isConnected, loadSessionMetadata, routeSessionId]);

  // 聊天处理完成后更新本地会话元数据，以便拾取自动生成的标题等更新。
  const prevProcessingBySessionRef = useRef(new Map<string, boolean>());
  useEffect(() => {
    if (!sessionId || sessionId === NEW_CONVERSATION_ID) {
      return;
    }

    const prevProcessing = prevProcessingBySessionRef.current.get(sessionId) ?? false;
    if (prevProcessing && !isProcessing) {
      if (hasPendingQuestion) {
        return;
      }
      void (async () => {
        const session = await loadSessionMetadata(sessionId);
        if (session) {
          useWorkspaceStore.getState().upsertSession(session);
        }
      })();
    }
    prevProcessingBySessionRef.current.set(sessionId, isProcessing);
  }, [sessionId, isProcessing, hasPendingQuestion, loadSessionMetadata]);

  // 连接成功后从 config.yaml 同步 preferred_language 到前端显示
  useEffect(() => {
    if (!isConnected) return;
    void webRequest<{ preferred_language?: string }>('locale.get_conf')
      .then((payload) => {
        const lang = payload?.preferred_language;
        if (lang === 'zh' || lang === 'en') {
          i18n.changeLanguage(lang);
        }
      })
      .catch(() => {});
  }, [isConnected]);

  // 当会话 ID 变化或页面加载时，自动加载历史会话
  useEffect(() => {
    if (!isConnected || !sessionId || sessionId === NEW_CONVERSATION_ID) return;
    
    // 仅处理以 sess_ 开头的会话 ID
    if (!isRestorableSessionId(sessionId)) return;

    if (promotedFromNewSessionIdsRef.current.has(sessionId)) {
      setHistoryPagerMeta(sessionId, null);
      setHistoryLoadingMore(false);
      setLoadingHistory(sessionId, false);
      return;
    }

    // 新建会话时跳过历史加载
    const isNew = useChatStore.getState().runtimes[sessionId]?.isNewSession ?? false;
    if (isNew) {
      useChatStore.getState().setNewSession(sessionId, false);
      setHistoryPagerMeta(sessionId, null);  // 新会话无历史，不显示分页栏
      setLoadingHistory(sessionId, false);
      return;
    }

    // 已有完整历史恢复状态则跳过历史加载，直接使用内存数据。
    // historyPagerMeta 是唯一可靠的"history 是否完整加载过"标记。
    const existingRuntime = useChatStore.getState().getRuntime(sessionId);
    if (existingRuntime && existingRuntime.historyPagerMeta) {
      setLoadingHistory(sessionId, false);
      startBackgroundHistoryPrefetch(
        sessionId,
        existingRuntime.historyPagerMeta.loadedPages,
        existingRuntime.historyPagerMeta.totalPages
      );
      return;
    }

    // 清理之前的历史加载句柄
    disposeInFlightHistoryHandles(sessionId);
    setHistoryPagerMeta(sessionId, null);
    setHistoryLoadingMore(false);
    
    setLoadingHistory(sessionId, true);
    // 开始历史会话加载
    const restoreHandle = beginHistoryRestore({
      sessionId: sessionId,
      onReady: (messages, totalPages) => {
        historyRestoreFromPanelHintRef.current = false;
        replaceHistoryMessages(sessionId, messages);
        const restoredTotalPages = totalPages ?? 1;
        setHistoryPagerMeta(sessionId, {
          loadedPages: 1,
          totalPages: restoredTotalPages,
        });
        setLoadingHistory(sessionId, false);
        startBackgroundHistoryPrefetch(sessionId, 1, restoredTotalPages);
        queueMicrotask(() => {
          if (historyRestoreHandlesRef.current.get(sessionId) === restoreHandle) {
            historyRestoreHandlesRef.current.delete(sessionId);
          }
        });
      },
      onEmpty: (emptyTotalPages) => {
        replaceHistoryMessages(sessionId, []);
        const restoredTotalPages = emptyTotalPages ?? 1;
        setHistoryPagerMeta(sessionId, {
          loadedPages: 1,
          totalPages: restoredTotalPages,
        });
        if (historyRestoreFromPanelHintRef.current) {
          historyRestoreFromPanelHintRef.current = false;
          addMessage(sessionId, {
            id: `history-restore-empty-${Date.now()}`,
            role: 'system',
            content: tRef.current('sessions.restoreEmpty'),
            timestamp: new Date().toISOString(),
          });
        }
        setLoadingHistory(sessionId, false);
        startBackgroundHistoryPrefetch(sessionId, 1, restoredTotalPages);
        if (historyRestoreHandlesRef.current.get(sessionId) === restoreHandle) {
          historyRestoreHandlesRef.current.delete(sessionId);
        }
      },
      onToolReplay: (items) => {
        clearSubtasks(sessionId);
        for (const item of items) {
          if (item.kind === 'tool_call') {
            const n = normalizeToolCallPayload(item.payload);
            addToolCall(
              sessionId,
              {
                id: n.id,
                name: n.name,
                arguments: n.arguments,
                description: n.description,
                formatted_args: n.formatted_args,
                memberName: n.memberName,
              },
              { startedAt: item.at }
            );
          } else {
            const n = normalizeToolResultPayload(item.payload);
            addToolResult(
              sessionId,
              {
                toolName: n.toolName,
                result: n.result,
                success: n.success,
                toolCallId: n.toolCallId,
                summary: n.summary,
                skillTree: n.skillTree,
              },
              { updatedAt: item.at }
            );
          }
        }
      },
      onHarnessReplay: (items: HistoryHarnessReplayItem[]) => {
        const harnessStore = useHarnessStore.getState();
        const harnessRuntime = harnessStore.getRuntime(sessionId);
        for (const item of items) {
          if (item.kind === 'harness_message') {
            const content = typeof item.payload.content === 'string' ? item.payload.content : '';
            const stage = typeof item.payload.stage === 'string' ? item.payload.stage : undefined;
            if (content) {
              harnessStore.addHarnessMessage(sessionId, content, stage);
              // Update stage result with running status and label from message
              if (stage) {
                const existingStage = harnessRuntime?.stageResults.find((s) => s.stage === stage);
                if (existingStage?.status !== 'running') {
                  harnessStore.updateStageResult(sessionId, {
                    stage,
                    stageLabel: content,
                    status: 'running',
                    messages: [],
                    metrics: {},
                  });
                }
              }
            }
          } else if (item.kind === 'harness_stage_result') {
            const stage = typeof item.payload.stage === 'string' ? item.payload.stage : '';
            const status = typeof item.payload.status === 'string' ? item.payload.status : 'success';
            const error = typeof item.payload.error === 'string' ? item.payload.error : undefined;
            const messages = Array.isArray(item.payload.messages) ? item.payload.messages : [];
            const metrics = item.payload.metrics || {};
            if (stage) {
              harnessStore.updateStageResult(sessionId, {
                stage,
                status: status as 'success' | 'failed' | 'timeout',
                error,
                messages,
                metrics,
              });
            }
          }
        }
      },
      onError: (message) => {
        console.warn('[history.restore]', message);
        setLoadingHistory(sessionId, false);
      },
    });
    historyRestoreHandlesRef.current.set(sessionId, restoreHandle);

    // 调用历史会话接口
    void (async () => {
      try {
        await request(HISTORY_GET_METHOD, {
          session_id: sessionId,
          page_idx: 1,
        });
      } catch (error) {
        historyRestoreFromPanelHintRef.current = false;
        restoreHandle.dispose();
        if (historyRestoreHandlesRef.current.get(sessionId) === restoreHandle) {
          historyRestoreHandlesRef.current.delete(sessionId);
        }
        // 发生错误时，设置 historyPagerMeta 为 null，显示欢迎信息
        setHistoryPagerMeta(sessionId, null);
        console.error('Failed to load history:', error);
        setLoadingHistory(sessionId, false);
        // 忽略 "invalid page_idx or session history not found" 错误，因为这是新会话的正常情况
        const errorMessage = error instanceof Error ? error.message : String(error);
        if (sessionIdRef.current === sessionId && !errorMessage.includes('invalid page_idx or session history not found')) {
          clearMessages(sessionId);
          addMessage(sessionId, {
            id: `history-load-failed-${Date.now()}`,
            role: 'system',
            content: tRef.current('sessions.errors.restoreFailed', { sessionId }),
            timestamp: new Date().toISOString(),
          });
        }
      }
    })();
  }, [
    isConnected,
    sessionId,
    historyBootstrapKey,
    request,
    addMessage,
    addToolCall,
    addToolResult,
    clearMessages,
    clearSubtasks,
    disposeInFlightHistoryHandles,
    setLoadingHistory,
    setHistoryPagerMeta,
    replaceHistoryMessages,
    startBackgroundHistoryPrefetch,
  ]);

  const requestComposerFocus = useCallback(() => {
    setComposerFocusNonce((nonce) => nonce + 1);
  }, []);

  const enterNewConversation = useCallback((targetMode: AgentMode = mode, options: NewConversationOptions = {}) => {
    const currentSessionId = sessionIdRef.current;
    const currentRuntime = useSessionStore.getState().getRuntime(currentSessionId);
    const selectedModelName = currentRuntime?.selectedModelName ?? null;
    const selectedProject = options.project ?? useWorkspaceStore.getState().selectedProject;
    const projectDir = selectedProject?.project_dir ?? currentRuntime?.projectDirectory ?? null;
    disposeInFlightHistoryHandles(
      currentSessionId !== NEW_CONVERSATION_ID ? currentSessionId : undefined,
    );
    setHistoryLoadingMore(false);
    resetNewConversationRuntime({ mode: targetMode, selectedModelName, projectDir });
    if (options.initialInputValue) {
      useChatStore.getState().setInputValue(NEW_CONVERSATION_ID, options.initialInputValue);
    }
    if (options.preserveProject) {
      preserveSelectedProjectOnChatNewRef.current = true;
      newConversationProjectRef.current = selectedProject
        ? {
          project_id: selectedProject.project_id,
          project_dir: selectedProject.project_dir,
        }
        : null;
    } else {
      newConversationProjectRef.current = null;
      setSelectedProject(null);
    }
    sessionIdRef.current = NEW_CONVERSATION_ID;
    setSessionId(NEW_CONVERSATION_ID);
    setCurrentSession(null);
    setTeamAreaExpanded(false);
    navigate({ kind: 'chat-new' });
    setActiveNav('chat');
    requestComposerFocus();
  }, [disposeInFlightHistoryHandles, mode, navigate, requestComposerFocus, setCurrentSession, setSelectedProject, setTeamAreaExpanded]);

  const handleNewSession = useCallback(async (options?: NewConversationOptions) => {
    enterNewConversation(mode, options);
  }, [enterNewConversation, mode]);

  // 切换模式
  const handleSwitchMode = useCallback((targetMode: AgentMode) => {
    const currentId = sessionIdRef.current;
    if (useChatStore.getState().getRuntime(currentId)?.isProcessing) return;
    if (currentId === NEW_CONVERSATION_ID) {
      setMode(NEW_CONVERSATION_ID, targetMode);
      return;
    }
    enterNewConversation(targetMode);
  }, [enterNewConversation, setMode]);

  const handleSendMessage = useCallback(async (content: string, mediaItems?: MediaItem[]) => {
    const currentSessionId = sessionIdRef.current;
    if (!currentSessionId) return;
    if (currentSessionId === NEW_CONVERSATION_ID) {
      if (creatingSessionRef.current) return;
      creatingSessionRef.current = true;
      useChatStore.getState().setProcessing(NEW_CONVERSATION_ID, true);
      const newSid = generateSessionId();
      const newRuntime = useSessionStore.getState().getRuntime(NEW_CONVERSATION_ID);
      const runtimeSettings = {
        mode: newRuntime?.mode ?? mode,
        selectedModelName: newRuntime?.selectedModelName ?? null,
        projectDir: newRuntime?.projectDirectory ?? null,
      };
      const baseWorkContext = getWorkContextForSession(NEW_CONVERSATION_ID);
      const preservedProject = newConversationProjectRef.current;
      const workContext = {
        project_id: baseWorkContext.project_id || preservedProject?.project_id,
        project_dir: baseWorkContext.project_dir || preservedProject?.project_dir,
      };
      try {
        const createParams: Record<string, unknown> = {
          session_id: newSid,
          mode: runtimeSettings.mode,
          title: createConversationTitle(content).slice(0, 100),
        };
        if (runtimeSettings.selectedModelName) {
          createParams.model = runtimeSettings.selectedModelName;
        }
        if (workContext.project_id) {
          createParams.project_id = workContext.project_id;
        }
        if (workContext.project_dir) {
          createParams.project_dir = workContext.project_dir;
        }
        const payload = await request<{ session_id?: string; sessionId?: string }>('session.create', createParams);
        const createdSessionId = payload.session_id ?? payload.sessionId;
        if (createdSessionId !== newSid) throw new Error('session.create returned an unexpected session id');
        const createdSession = registerCreatedConversation(
          newSid,
          runtimeSettings,
          Date.now(),
          content,
          {
            project_id: workContext.project_id,
            project_dir: workContext.project_dir,
          },
        );
        // 迁移 'new' 会话的已选技能到新会话
        const pendingSkills = useSessionStore.getState().getRuntime(NEW_CONVERSATION_ID)?.selectedSkills ?? [];
        pendingSkills.forEach((skill) => useSessionStore.getState().addSelectedSkill(newSid, skill));
        useSessionStore.getState().clearSelectedSkills(NEW_CONVERSATION_ID);
        useWorkspaceStore.getState().upsertSession(createdSession, { isNew: true });
        promotedFromNewSessionIdsRef.current.add(newSid);
        useChatStore.getState().setProcessing(NEW_CONVERSATION_ID, false);
        sessionIdRef.current = newSid;
        setSessionId(newSid);
        navigate({ kind: 'chat-session', sessionId: newSid }, { replace: true });
        const sent = await sendMessage(content, newSid, mediaItems);
        newConversationProjectRef.current = null;
        if (!sent) {
          useChatStore.getState().setInputValue(newSid, content);
        }
      } catch (error) {
        useChatStore.getState().setProcessing(NEW_CONVERSATION_ID, false);
        useChatStore.getState().setThinking(NEW_CONVERSATION_ID, false);
        useChatStore.getState().setInputValue(NEW_CONVERSATION_ID, content);
        console.error('Failed to create conversation:', error);
        window.alert(t('multiSession.errors.create'));
      } finally {
        creatingSessionRef.current = false;
      }
      return;
    }
    disposeInFlightHistoryHandles(currentSessionId);
    const sent = await sendMessage(content, currentSessionId, mediaItems);
    if (sent) {
      const sessionState = useSessionStore.getState();
      const session =
        sessionState.currentSession?.session_id === currentSessionId
          ? sessionState.currentSession
          : sessionState.sessions.find((item) => item.session_id === currentSessionId);
      await useWorkspaceStore.getState().refreshSessionWorkspace(session);
    } else {
      useChatStore.getState().setInputValue(currentSessionId, content);
    }
  }, [disposeInFlightHistoryHandles, mode, navigate, request, sendMessage, t]);

  const handlePersistMedia = useCallback((content: string, mediaItems: MediaItem[]) => {
    const currentSessionId = sessionIdRef.current;
    if (!currentSessionId || currentSessionId === NEW_CONVERSATION_ID) {
      return Promise.reject(new Error('会话未就绪，请稍后重试'));
    }
    return persistMedia(content, currentSessionId, mediaItems);
  }, [persistMedia]);

  useEffect(() => {
    return setA2UIActionHandler((message) => {
      const currentSessionId = sessionIdRef.current;
      if (!currentSessionId || currentSessionId === NEW_CONVERSATION_ID) return;
      return sendStructuredChatContent(
        buildA2UIClientEventContent(message),
        currentSessionId,
      );
    });
  }, [sendStructuredChatContent]);

  const handleInterrupt = useCallback((newInput?: string) => {
    const currentSessionId = sessionIdRef.current;
    if (!currentSessionId || currentSessionId === NEW_CONVERSATION_ID) return;
    const trimmed = newInput?.trim();
    if (!trimmed) return;
    void supplement(currentSessionId, trimmed);
  }, [supplement]);

  const handleCancel = useCallback(() => {
    const currentSessionId = sessionIdRef.current;
    if (!currentSessionId || currentSessionId === NEW_CONVERSATION_ID) return;
    if (mode === 'team') {
      void pause(currentSessionId);
      return;
    }
    // agent 模式下有队列任务时，暂停队列自动发送
    if (mode === 'agent') {
      const runtime = useChatStore.getState().getRuntime(currentSessionId);
      if (runtime && runtime.taskQueue.length > 0) {
        useChatStore.getState().setQueuePaused(currentSessionId, true);
      }
    }
    void cancel(currentSessionId);
  }, [cancel, mode, pause]);

  const handleUserAnswer = useCallback((requestId: string, answers: UserAnswer[], source?: string) => {
    const currentSessionId = sessionIdRef.current;
    if (!currentSessionId || currentSessionId === NEW_CONVERSATION_ID) return;
    void sendUserAnswer(currentSessionId, requestId, answers, source);
  }, [sendUserAnswer]);

  const handleLoadMoreHistory = useCallback(async () => {
    if (!isRestorableSessionId(sessionId) || !historyPagerMeta) return;
    if (historyLoadingSessionsRef.current.has(sessionId) || historyPagerMeta.loadedPages >= historyPagerMeta.totalPages) return;

    const sid = sessionId;
    const nextPage = historyPagerMeta.loadedPages + 1;
    const fallbackTotal = historyPagerMeta.totalPages;
    const prevToken = historyBackgroundPrefetchTokensRef.current.get(sid) ?? 0;
    historyBackgroundPrefetchTokensRef.current.set(sid, prevToken + 1);
    historyLoadingSessionsRef.current.add(sid);
    setHistoryLoadingMore(true);
    setLoadingHistory(sid, true);
    const page = await fetchHistoryPageResult(sid, nextPage, fallbackTotal);
    if (page) {
      applyLoadedHistoryPage(sid, page);
      startBackgroundHistoryPrefetch(sid, page.pageIdx, page.totalPages);
    }
    historyLoadingSessionsRef.current.delete(sid);
    setHistoryLoadingMore(false);
    setLoadingHistory(sid, false);
  }, [
    applyLoadedHistoryPage,
    fetchHistoryPageResult,
    historyPagerMeta,
    sessionId,
    setLoadingHistory,
    startBackgroundHistoryPrefetch,
  ]);

  const chatHistoryPager = useMemo(() => {
    if (!historyPagerMeta) return null;
    return {
      loadedPages: historyPagerMeta.loadedPages,
      totalPages: historyPagerMeta.totalPages,
      loadingMore: historyLoadingMore,
      prepending: historyPrepending,
      onLoadMore: handleLoadMoreHistory,
    };
  }, [
    handleLoadMoreHistory,
    historyLoadingMore,
    historyPagerMeta,
    historyPrepending,
  ]);

  const handleRestoreSession = useCallback(
    async (targetSessionId: string, targetMode?: string, targetSession?: Session) => {
      if (!isRestorableSessionId(targetSessionId)) return;

      const resolvedMode = targetMode ?? targetSession?.mode ?? mode;
      disposeInFlightHistoryHandles(targetSessionId);
      setHistoryLoadingMore(false);
      const existingRuntime = useChatStore.getState().getRuntime(targetSessionId);
      if (!existingRuntime) {
        useChatStore.getState().ensureRuntime(targetSessionId);
        setProcessing(targetSessionId, false);
        setThinking(targetSessionId, false);
        setPaused(targetSessionId, false);
        clearTeamRuntimeState(targetSessionId);
        clearMessages(targetSessionId);
        clearTodos(targetSessionId);
        clearSubtasks(targetSessionId);
        resetHarnessStore(targetSessionId);
        historyRestoreFromPanelHintRef.current = true;
      }
      // 确保 session runtime 存在；否则 useSessionStore.setMode 会因找不到 runtime 而直接跳过，
      // 导致从会话页签恢复后前端 mode 不会切换到目标会话对应的 mode。
      ensureSessionRuntimes(targetSessionId);
      sessionIdRef.current = targetSessionId;
      setSessionId(targetSessionId);
      if (targetSession) {
        upsertSessionMetadata(targetSession, { setCurrent: true });
      } else {
        setCurrentSession(null);
      }
      if (resolvedMode) {
        setMode(targetSessionId, resolvedMode as AgentMode);
      }
      setActiveNav('chat');
      navigate({ kind: 'chat-session', sessionId: targetSessionId });
      setHistoryBootstrapKey((k) => k + 1);
      requestComposerFocus();
      if (!targetSession) {
        void loadSessionMetadata(targetSessionId);
      }
    },
    [
      clearMessages,
      clearSubtasks,
      clearTodos,
      disposeInFlightHistoryHandles,
      mode,
      navigate,
      loadSessionMetadata,
      requestComposerFocus,
      resetHarnessStore,
      setActiveNav,
      setCurrentSession,
      setHistoryLoadingMore,
      setMode,
      setPaused,
      setProcessing,
      setSessionId,
      setThinking,
      upsertSessionMetadata,
    ]
  );

  const requestSessionNavigation = useCallback((target: Session | 'new', options?: NewConversationOptions) => {
    if (target === 'new') { enterNewConversation(mode, options); return; }
    void handleRestoreSession(target.session_id, target.mode, target);
  }, [enterNewConversation, handleRestoreSession, mode]);

  const handleDeleteConversation = useCallback(async () => {
    if (!deleteTarget) return;
    const runtime = useChatStore.getState().getRuntime(deleteTarget.session_id);
    if (runtime?.isProcessing || runtime?.pendingQuestion) return;
    setDialogBusy(true); setDialogError(null);
    try {
      const deletedSession = deleteTarget;
      await request('session.delete', { session_id: deleteTarget.session_id });
      forgetCreatedConversation(deleteTarget.session_id);
      useSessionStore.getState().removeSession(deleteTarget.session_id);
      useSessionStore.getState().removeRuntime(deleteTarget.session_id);
      useChatStore.getState().removeRuntime(deleteTarget.session_id);
      useTodoStore.getState().removeRuntime(deleteTarget.session_id);
      useHarnessStore.getState().removeRuntime(deleteTarget.session_id);
      const deletingCurrent = sessionIdRef.current === deleteTarget.session_id;
      setDeleteTarget(null);
      await useWorkspaceStore.getState().refreshSessionWorkspace(deletedSession);
      // 删除 session 后刷新所属定时任务的触发会话列表
      const cronStore = useCronStore.getState();
      for (const [jobId, sessions] of Object.entries(cronStore.cronSessions)) {
        if (sessions.some((s) => s.session_id === deletedSession.session_id)) {
          const job = cronStore.jobs.find((j) => j.id === jobId);
          void cronStore.loadCronSessions(job?.project_id || 'default', jobId);
        }
      }
      if (deletingCurrent) {
        enterNewConversation();
      }
    } catch { setDialogError(t('multiSession.errors.delete')); }
    finally { setDialogBusy(false); }
  }, [deleteTarget, enterNewConversation, request, t]);

  const handleNavigate = useCallback((nav: MainNavKey) => {
    setActiveNav(nav);
    if (nav === 'skills') setHasVisitedSkills(true);
    if (nav === 'channels') setHasVisitedChannels(true);
  }, []);

  const handleExportShare = useCallback(async () => {
    const currentSessionId = sessionIdRef.current;
    if (!currentSessionId || currentSessionId === NEW_CONVERSATION_ID || (isProcessing && !isPaused) || isExportingShare) {
      return;
    }
    setIsExportingShare(true);
    try {
      const params = new URLSearchParams({
        session_id: currentSessionId,
      });
      const response = await fetch(`/share-api/snapshot?${params.toString()}`, {
        cache: 'no-store',
      });
      const contentType = response.headers.get('content-type') || '';
      if (!response.ok) {
        let detail = '';
        try {
          const payload = await response.json();
          detail = typeof payload?.error === 'string' ? payload.error : '';
        } catch {
          detail = await response.text().catch(() => '');
        }
        throw new Error(detail || `HTTP ${response.status}`);
      }
      if (!contentType.includes('application/json')) {
        throw new Error('share_snapshot_not_json');
      }
      const payload = await response.json() as {
        filename?: string;
        snapshot?: ShareImageSnapshot;
      };
      if (!payload.snapshot) {
        throw new Error('missing_snapshot');
      }
      shareExportFilenameRef.current = payload.filename || payload.snapshot.metadata?.filename || 'jiuwenswarm-share.png';
      setShareExportSnapshot(payload.snapshot);
    } catch (error) {
      console.error('Failed to export share image:', error);
      const detail = error instanceof Error && error.message ? `: ${error.message}` : '';
      window.alert(`${t('share.exportFailed')}${detail}`);
      setIsExportingShare(false);
      setShareExportSnapshot(null);
    }
  }, [isExportingShare, isPaused, isProcessing, t]);

  useEffect(() => {
    if (!shareExportSnapshot) {
      return;
    }
    const token = shareExportTokenRef.current + 1;
    shareExportTokenRef.current = token;

    void (async () => {
      try {
        const node = shareExportRef.current;
        if (!node) {
          throw new Error('share_image_node_missing');
        }
        const dataUrl = await exportShareImageNode(node);
        if (shareExportTokenRef.current !== token) {
          return;
        }
        const saved = await saveShareImage(dataUrl, shareExportFilenameRef.current);
        if (saved) {
          showSaveToast();
        }
      } catch (error) {
        console.error('Failed to render share image:', error);
        const detail = error instanceof Error && error.message ? `: ${error.message}` : '';
        window.alert(`${t('share.exportFailed')}${detail}`);
      } finally {
        if (shareExportTokenRef.current === token) {
          setIsExportingShare(false);
          setShareExportSnapshot(null);
        }
      }
    })();
  }, [shareExportSnapshot, showSaveToast, t]);

  const heartbeatToastPreviewRaw = heartbeatToastMessage.replace(/\s+/g, ' ').trim();
  const heartbeatToastPreview = heartbeatToastPreviewRaw.length > 120
    ? `${heartbeatToastPreviewRaw.slice(0, 120)}...`
    : heartbeatToastPreviewRaw;
  const routeSessionMissing = routeSessionId !== null
    && initialDataLoaded
    && missingSessionId === routeSessionId
    && isConversationMissing(routeSessionId, true, sessions);
  const showConversationNotFound = route.kind === 'not-found' || routeSessionMissing;
  const isNewSessionPromotion = Boolean(sessionId && promotedFromNewSessionIdsRef.current.has(sessionId));
  const composerFocusKey = showConversationNotFound ? null : `${sessionId}:${composerFocusNonce}`;

  return (
    <div
      className={`shell shell--icon-rail ${sidebarMorePanelOpen ? 'shell--more-panel-open' : ''}`}
      data-testid="app-shell"
      data-session-id={sessionId}
    >
      {/* Navigation Sidebar */}
      <SessionSidebar
        activeNav={activeNav}
        onNavigate={handleNavigate}
        appVersion={typeof serverConfig?.app_version === 'string' ? serverConfig.app_version : ''}
        isConnected={isConnected}
        onNewSession={handleNewSession}
        showNewSession={false}
        hiddenNavItems={['sessions']}
        onMorePanelOpenChange={setSidebarMorePanelOpen}
      />

      {/* Main Content */}
      <main className={`content ${activeNav === 'chat' ? 'content--chat' : ''} ${isTeamAreaExpanded ? 'content--team-expanded' : ''}`}>
        {configError && (
          <div className="card mb-4">
            <div className="text-sm text-text-muted">
              {configError}. {t('app.configErrorHint')}
              <span className="mono"> python -m tests.web_gateway_jiuwenclaw_integration </span>
              {t('app.configErrorDefault')}
              <span className="mono"> jiuwenswarm/channels/web/frontend/.env.local </span>
              {t('app.configErrorEnv')} <span className="mono">VITE_API_BASE</span> {t('common.and')} <span className="mono">VITE_WS_BASE</span>.
            </div>
          </div>
        )}

        {activeNav === 'chat' && (
          <>
            <div className="chat-layout flex-1 flex min-h-0 overflow-hidden">
              <ConversationSidebar
                activeSessionId={sessionId === NEW_CONVERSATION_ID ? null : sessionId}
                onNew={(options) => requestSessionNavigation('new', options)}
                onSelect={requestSessionNavigation}
                onDelete={(session) => { setDialogError(null); setDeleteTarget(session); }}
                onOpenCron={() => handleNavigate('cron')}
                isCronActive={false}
              />
              <div className="chat-workspace flex-1 flex min-h-0 overflow-hidden">
                {showConversationNotFound && (
                  <div className="flex-1 flex flex-col items-center justify-center gap-4">
                    <h1 className="text-lg font-semibold text-text">{t('multiSession.notFound.title')}</h1>
                    <div className="flex gap-2">
                      <button className="btn primary" onClick={() => enterNewConversation()}>
                        {t('multiSession.notFound.newConversation')}
                      </button>
                    </div>
                  </div>
                )}
                {/* Chat Panel - 在展开时可拖拽调整宽度 */}
                <div
                  className={`${showConversationNotFound ? 'hidden' : 'flex'} chat-layout__surface p-3 pt-0 flex-col min-w-0 min-h-0 ${isTeamAreaExpanded ? '' : 'flex-1'}`}
                  style={isTeamAreaExpanded ? { width: `${chatPanelWidthPct}%` } : undefined}
                >
                  <div className={`flex-1 min-h-0`}>
                    <ChatPanel
                      onSendMessage={handleSendMessage}
                      onPersistMedia={handlePersistMedia}
                      onInterrupt={handleInterrupt}
                      onCancel={handleCancel}
                      onSwitchMode={handleSwitchMode}
                      isProcessing={isProcessing}
                      onUserAnswer={handleUserAnswer}
                      onExportShare={handleExportShare}
                      isExportingShare={isExportingShare}
                      canExportShare={Boolean(sessionId && sessionId !== NEW_CONVERSATION_ID && (!isProcessing || isPaused))}
                      sessionTitle={sessionTitle}
                      sessionProjectName={sessionProjectName}
                      teamAreaExpanded={isTeamAreaExpanded}
                      autoFocusKey={composerFocusKey}
                      onNavigateToSkills={() => handleNavigate('skills')}
                      onToggleTeamArea={handleToggleDetailPanel}
                      permissionsEnabled={serverConfig?.permissions_enabled !== 'false'}
                      onSavePermission={savePermissionSilent}
                      historyPager={chatHistoryPager}
                      isHistoryRestoring={isRestoringHistorySession}
                    />
                  </div>
                </div>

                {/* 可拖拽分割线 */}
                {isTeamAreaExpanded && !showConversationNotFound && (
                  <div
                    className="resize-divider"
                    onMouseDown={handleDividerMouseDown}
                  />
                )}

                {/* Tool Panel / Expanded Team Panel */}
                {(toolPanelHasContent || isRestoringTeamHistory) && !showConversationNotFound && (
                  <ToolPanel
                    sessionId={sessionId}
                    isNewSessionPromotion={isNewSessionPromotion}
                    teamAreaExpanded={teamAreaExpanded}
                    teamAreaActiveTab={teamAreaActiveTab}
                    teamAreaActiveDetailTab={teamAreaActiveDetailTab}
                    teamAreaSelectedMemberId={teamAreaSelectedMemberId}
                    setTeamAreaExpanded={setTeamAreaExpanded}
                    setTeamAreaActiveTab={setTeamAreaActiveTab}
                    setTeamAreaActiveDetailTab={setTeamAreaActiveDetailTab}
                    setTeamAreaSelectedMemberId={setTeamAreaSelectedMemberId}
                  />
                )}
              </div>
            </div>
          </>
        )}
        {activeNav === 'agents' && (
          <div className="app-section">
            <AgentPanel sessionId={sessionId} />
          </div>
        )}
        {activeNav === 'teams' && (
          <div className="app-section">
            <TeamPanel />
          </div>
        )}
        {activeNav === 'sessions' && (
          <div className="app-section">
            <SessionsPanel
              currentSessionId={sessionId}
              isConnected={isConnected}
              isProcessing={isProcessing}
              onRestoreSession={handleRestoreSession}
            />
          </div>
        )}
        {activeNav === 'heartbeat' && (
          <div className="app-section">
            <HeartbeatPanel />
          </div>
        )}
        {activeNav === 'cron' && (
          <div className="chat-layout flex-1 flex min-h-0 overflow-hidden">
            <ConversationSidebar
              // 停留在定时任务时，项目/会话列表不应该还显示"选中"效果——定时任务和它们是同一级的
              // 互斥选中关系，传 null 让列表里的选中态清空（沿用"新建会话时传 null"的既有语义）
              activeSessionId={null}
              onNew={(options) => requestSessionNavigation('new', options)}
              onSelect={requestSessionNavigation}
              onDelete={(session) => { setDialogError(null); setDeleteTarget(session); }}
              onOpenCron={() => handleNavigate('cron')}
              isCronActive
            />
            <div className="chat-workspace flex-1 flex min-h-0 overflow-hidden">
              <CronPanel
                sessionId={sessionId}
                onCreateViaChat={(initialInputValue) => requestSessionNavigation('new', { initialInputValue })}
                onSelectSession={(session) => {
                  if (typeof session === 'string') { void handleRestoreSession(session); return; }
                  requestSessionNavigation(session);
                }}
              />
            </div>
          </div>
        )}
        {activeNav === 'configpanel' && (
          <div className="app-section">
            <ConfigPanel
              config={serverConfig}
              isConnected={isConnected}
              sessionId={sessionId}
              onSaveConfig={saveConfigAndRestart}
              onSaveAllConfig={saveAllConfigAndRestart}
              onValidateModel={validateModelConfig}
              initialExpandGroupTag={configInitialExpandGroup}
              onModelsReplaceAll={handleModelsReplaceAll}
              onModelValidate={validateModelConfig}
              onModelsRefresh={handleModelsRefresh}
              onAgentsTeamsSave={handleAgentsTeamsSave}
              onHasChangesChange={handleHasChangesChange}
            />
          </div>
        )}
        {activeNav === 'browserpanel' && (
          <div className="app-section">
            <BrowserPanel isConnected={isConnected} request={request} />
          </div>
        )}
        {FEATURE_APP_UPDATER_UI && activeNav === 'updatepanel' && (
          <div className="app-section">
            <UpdatePanel isConnected={isConnected} request={request} />
          </div>
        )}

        {hasVisitedSkills && (
          <div className={`app-section ${activeNav === 'skills' ? '' : 'is-hidden'}`}>
            <SkillPanel
              sessionId={sessionId}
              isActive={activeNav === 'skills'}
              onNavigateToConfig={() => {
                setConfigInitialExpandGroup('third_party_api');
                setActiveNav('configpanel');
              }}
            />
          </div>
        )}
        {hasVisitedChannels && (
          <div className={`app-section ${activeNav === 'channels' ? '' : 'is-hidden'}`}>
            <ChannelsPanel isConnected={isConnected} />
          </div>
        )}
        {activeNav === 'extensions' && (
          <div className="app-section">
            <ExtensionsHubPanel sessionId={sessionId} isConnected={isConnected} />
          </div>
        )}
      </main>

      {deleteTarget && (
        <DeleteDialog
          title={deleteTarget.title || t('multiSession.untitled')}
          deleting={dialogBusy}
          error={dialogError}
          onCancel={() => setDeleteTarget(null)}
          onDelete={() => { void handleDeleteConversation(); }}
        />
      )}

      {/* 连接状态提示 */}
      {!isConnected && (
        <div className="app-toast-wrapper app-toast-wrapper--top">
          <div className="app-connection-toast animate-rise">
            {serverConfig ? t('connection.connecting') : t('connection.loadingConfig')}
          </div>
        </div>
      )}

      {saveToastVisible && (
        <div className="app-toast-wrapper app-toast-wrapper--top-center">
          <div className="app-session-toast animate-rise">
            {t('common.saveSuccess')}
          </div>
        </div>
      )}

      {/* 全局心跳消息提示 */}
      {heartbeatToastVisible && (
        <div className="app-toast-wrapper app-toast-wrapper--top">
          <div className="app-heartbeat-toast animate-rise">
            <div className="app-heartbeat-toast__header">
              <div className="app-heartbeat-toast__title">
                <span className="app-heartbeat-toast__dot animate-pulse" />
                <span className="text-xs font-medium text-text">{t('app.heartbeatTitle')}</span>
              </div>
              <button
                type="button"
                onClick={() => {
                  setHeartbeatToastVisible(false);
                  clearHeartbeatToastTimer();
                }}
                className="app-heartbeat-toast__close"
                aria-label={t('app.heartbeatClose')}
              >
                <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>
            <button
              type="button"
              onClick={() => {
                setHeartbeatModalOpen(true);
                setHeartbeatToastVisible(false);
                clearHeartbeatToastTimer();
              }}
              className="app-heartbeat-toast__content text-sm"
              title={t('app.heartbeatViewFull')}
            >
              <span className="app-heartbeat-toast__preview">
                {heartbeatToastPreview}
              </span>
            </button>
          </div>
        </div>
      )}

      {proactiveToastVisible && proactiveToastMessage && (
        <div className="app-toast-wrapper app-toast-wrapper--top-center" data-testid="proactive-notification-toast">
          <div className="bg-warn-subtle text-warn px-4 py-2 rounded-lg shadow-lg animate-rise text-sm">
            {proactiveToastMessage}
          </div>
        </div>
      )}

      {/* 安全警告提示 */}
      {securityAlertVisible && (
        <div className="app-toast-wrapper app-toast-wrapper--top">
          <div className="app-heartbeat-toast animate-rise">
            <div className="app-heartbeat-toast__header">
              <div className="app-heartbeat-toast__title">
                <span>⚠️</span>
                <span className="text-xs font-medium text-text">{t('app.securityAlertTitle')}</span>
              </div>
              <button
                type="button"
                onClick={() => {
                  setSecurityAlertVisible(false);
                  if (securityAlertTimerRef.current) {
                    clearTimeout(securityAlertTimerRef.current);
                    securityAlertTimerRef.current = null;
                  }
                }}
                className="app-heartbeat-toast__close"
              >
                <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>
            <div className="app-heartbeat-toast__content text-sm">
              {securityAlertContent}
            </div>
          </div>
        </div>
      )}

      {/* 配置保存后重启状态弹窗 */}
      {restartModalOpen && (
        <div className="app-restart-modal">
          <div className="app-restart-modal__backdrop" />
          <div className="app-restart-modal__panel">
            <div className="flex flex-col items-center text-center">
              {!restartSuccess ? (
                <div className="w-12 h-12 rounded-full border-4 border-border border-t-accent animate-spin mb-4" />
              ) : (
                <div className="w-12 h-12 rounded-full bg-ok/15 text-ok flex items-center justify-center mb-4">
                  <svg className="w-7 h-7" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2">
                    <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                  </svg>
                </div>
              )}
              <h3 className="text-base font-semibold text-text mb-1">
                {!restartSuccess
                  ? t('app.restarting')
                  : a2uiRefreshPending
                    ? t('app.a2uiRefresh')
                    : appliedWithoutRestart
                      ? t('app.configApplied')
                      : t('app.restartSuccess')}
              </h3>
              <p className="text-sm text-text-muted mb-5">
                {!restartSuccess
                  ? t('app.restartWaiting')
                  : a2uiRefreshPending
                    ? t('app.a2uiRefreshDesc')
                    : appliedWithoutRestart
                      ? t('app.configAppliedDesc')
                      : t('app.restartSuccessDesc')}
              </p>
              {restartSuccess && (
                <button
                  type="button"
                  onClick={() => {
                    if (a2uiRefreshPending) {
                      window.location.reload();
                    } else {
                      closeRestartModal();
                    }
                  }}
                  className="btn primary !px-4 !py-2"
                >
                  {t('common.ok')}
                </button>
              )}
            </div>
          </div>
        </div>
      )}

      {configChangedConfirmOpen && (
        <div className="app-restart-modal">
          <div className="app-restart-modal__backdrop" />
          <div className="app-restart-modal__panel">
            <div className="flex flex-col items-center text-center">
              <div className="w-12 h-12 rounded-full bg-warn-subtle text-warn flex items-center justify-center mb-4">
                <svg className="w-7 h-7" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v4m0 4h.01M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z" />
                </svg>
              </div>
              <h3 className="text-base font-semibold text-text mb-1">{t('config.errors.configChangedTitle')}</h3>
              <p className="text-sm text-text-muted mb-5">{t('config.errors.configChangedDesc')}</p>
              <div className="flex gap-3">
                <button type="button" onClick={() => { setConfigChangedConfirmOpen(false); void fetchConfig(); }} className="btn primary !px-4 !py-2">
                  {t('config.errors.configChangedConfirm')}
                </button>
                <button type="button" onClick={() => setConfigChangedConfirmOpen(false)} className="btn !px-4 !py-2">
                  {t('config.errors.configChangedCancel')}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      <HeartbeatMessageModal
        open={heartbeatModalOpen}
        message={heartbeatToastMessage}
        onClose={() => setHeartbeatModalOpen(false)}
      />

      <div className="share-image-stage" aria-hidden="true">
        <ShareImageDocument ref={shareExportRef} snapshot={shareExportSnapshot} />
      </div>
    </div>
  );
}

function App() {
  return (
    <ErrorBoundary>
      <AppContent />
    </ErrorBoundary>
  );
}

export default App;
