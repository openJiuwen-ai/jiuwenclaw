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
import { LogsPanel } from './components/LogsPanel';
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
} from './features/historyRestore';
import {
  normalizeToolCallPayload,
  normalizeToolResultPayload,
} from './features/tool-events/toolEventNormalizer';
import { useWebSocket } from './hooks';
import { webRequest } from './services/webClient';
import { useTeamPanelState } from './features/teamPanelState';
import { AgentMode, MediaItem, UserAnswer, ModelEntry } from './types';
import { useSessionStore, useChatStore, useTodoStore, useHarnessStore } from './stores';
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

type MainNavKey = 'chat' | 'skills' | 'agents' | 'teams' | 'sessions' | 'heartbeat' | 'cron' | 'channels' | 'extensions' | 'configpanel' | 'logspanel' | 'browserpanel' | 'updatepanel';

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

function clearTeamRuntimeState(): void {
  const sessionStore = useSessionStore.getState();
  sessionStore.setTeamMembers([]);
  sessionStore.setTeamTaskEvents([]);
  sessionStore.setTeamTasks([]);
  sessionStore.setTeamMemberExecutionEvents([]);
  sessionStore.clearAllTeamMemberContextCompressionStatus();
  sessionStore.setTeamHistoryMessages([]);
  sessionStore.setTeamHumanShareCommands([]);
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



// 会话 ID 持久化（使用 sessionStorage：同标签页刷新保留，多标签页隔离）
const SESSION_STORAGE_KEY = 'openjiuwen_current_session';

function generateSessionId(): string {
  const ts = Date.now().toString(16);
  const rand = Math.random().toString(16).slice(2, 8);
  return `sess_${ts}_${rand}`;
}

function getStoredSessionId(): string | null {
  try {
    return sessionStorage.getItem(SESSION_STORAGE_KEY);
  } catch {
    return null;
  }
}

function storeSessionId(sessionId: string | null) {
  try {
    if (sessionId && sessionId !== 'new') {
      sessionStorage.setItem(SESSION_STORAGE_KEY, sessionId);
    } else {
      sessionStorage.removeItem(SESSION_STORAGE_KEY);
    }
  } catch {
    // ignore
  }
}

function downloadDataUrl(dataUrl: string, filename: string): void {
  const link = document.createElement('a');
  link.href = dataUrl;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
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
  const tRef = useRef(t);
  // 优先使用存储的会话 ID，避免每次刷新创建新会话
  const [sessionId, setSessionId] = useState<string>(() => {
    const stored = getStoredSessionId();
    return stored || 'new';
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
  const [newSessionToastVisible, setNewSessionToastVisible] = useState(false);
  const [heartbeatToastVisible, setHeartbeatToastVisible] = useState(false);
  const [heartbeatToastMessage, setHeartbeatToastMessage] = useState('');
  const [saveToastVisible, setSaveToastVisible] = useState(false);
  const [heartbeatModalOpen, setHeartbeatModalOpen] = useState(false);
  const [securityAlertVisible, setSecurityAlertVisible] = useState(false);
  const [securityAlertContent, setSecurityAlertContent] = useState('');
  const [hasVisitedSkills, setHasVisitedSkills] = useState(false);
  const [hasVisitedChannels, setHasVisitedChannels] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
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
      const defaultModel = availableModels[0]?.model_name;
      if (defaultModel) {
        setSelectedModelName(defaultModel);
      }
    }
  }, [activeNav]);

  useEffect(() => {
    if (!FEATURE_APP_UPDATER_UI && activeNav === 'updatepanel') {
      setActiveNav('chat');
    }
  }, [activeNav]);
  const restartAutoCloseTimerRef = useRef<number | null>(null);
  const newSessionToastTimerRef = useRef<number | null>(null);
  const heartbeatToastTimerRef = useRef<number | null>(null);
  const saveToastTimerRef = useRef<number | null>(null);
  const lastHeartbeatToastKeyRef = useRef<string | null>(null);
  /** 自「恢复会话」加载 history 后的分页元数据；用于聊天区顶部加载更早消息 */
  const [historyPagerMeta, setHistoryPagerMeta] = useState<{
    loadedPages: number;
    totalPages: number;
  } | null>(null);
  const [historyLoadingMore, setHistoryLoadingMore] = useState(false);
  /** 仅用于强制重跑「首屏 history」effect：从会话列表恢复时若 sessionId 未变，也要重新拉 history 并恢复 historyPagerMeta */
  const [historyBootstrapKey, setHistoryBootstrapKey] = useState(0);
  const sessionIdRef = useRef(sessionId);
  const historyLoadingMoreRef = useRef(false);
  const historyRestoreHandleRef = useRef<HistoryRestoreHandle | null>(null);
  const historyPageHandleRef = useRef<HistoryRestoreHandle | null>(null);
  const shareExportRef = useRef<HTMLDivElement>(null);
  const shareExportFilenameRef = useRef('jiuwenswarm-share.png');
  const shareExportTokenRef = useRef(0);
  /** 为 true 表示刚从「会话列表」恢复；history 为空时在 useEffect 的 onEmpty 中提示一次 */
  const historyRestoreFromPanelHintRef = useRef(false);



  const { setCurrentSession, setSessions, setAvailableModels, setMode, mode, heartbeatMessage,
    heartbeatUpdatedAt, teamTaskEvents, teamTasks, teamMembers, setTeamLeaderMemberIds,
    updateSession } = useSessionStore();
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
  const [chatPanelWidthPct, setChatPanelWidthPct] = useState(33.33);

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

  const {
    clearMessages,
    clearSubtasks,
    addMessage,
    addToolCall,
    addToolResult,
    prependMessages,
    isProcessing,
    isPaused,
    setProcessing,
    setThinking,
    setLoadingHistory,
    setPaused,
    messages,
  } = useChatStore();

  useEffect(() => {
    if (!serverConfig) {
      setTeamLeaderMemberIds([]);
      return;
    }
    const leaderIds = Object.entries(serverConfig)
      .filter(([key]) => /^team_leader_member_name_\d+$/.test(key) || /^team_\d+_leader_member_name$/.test(key))
      .map(([, value]) => (typeof value === 'string' ? value.trim() : ''))
      .filter(Boolean);
    setTeamLeaderMemberIds(leaderIds);
  }, [serverConfig, setTeamLeaderMemberIds]);

  const disposeInFlightHistoryHandles = useCallback(() => {
    historyLoadingMoreRef.current = false;
    setLoadingHistory(false);
    historyRestoreHandleRef.current?.dispose();
    historyRestoreHandleRef.current = null;
    historyPageHandleRef.current?.dispose();
    historyPageHandleRef.current = null;
  }, [setLoadingHistory]);

  useEffect(() => () => disposeInFlightHistoryHandles(), [disposeInFlightHistoryHandles]);
  const { todos, clearTodos } = useTodoStore();
  const { extensionReady, reset: resetHarnessStore } = useHarnessStore();

  const toolPanelHasContent = useMemo(() => {
    const hasMessages = messages.length > 0;
    const hasHeartbeat = Boolean(heartbeatMessage);
    switch (mode) {
      case 'auto_harness':
        return Boolean(extensionReady?.runtimePath) || hasMessages || hasHeartbeat;
      case 'team':
        return teamTaskEvents.length > 0 || teamTasks.length > 0 || teamMembers.length > 0 || hasMessages || hasHeartbeat;
      default:
        return todos.length > 0 || hasMessages || hasHeartbeat;
    }
  }, [mode, todos.length, teamTaskEvents.length, teamTasks.length, teamMembers.length, extensionReady?.runtimePath, messages.length, heartbeatMessage]);
  const isTeamAreaExpanded = mode === 'team' && teamAreaExpanded && toolPanelHasContent;

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
    switchMode,
    sendUserAnswer,
  } = useWebSocket({
    activeSessionId: sessionId,
    onConnect: (payload) => {
      const currentStored = getStoredSessionId();
      if (payload.session_id) {
        // 仅在尚无有效 session 时采纳后端分配的 session_id；
        // 重连时保持已有会话，防止被覆盖
        if (!currentStored) {
          console.log('Adopting backend session:', payload.session_id);
          sessionIdRef.current = payload.session_id;
          setSessionId(payload.session_id);
          storeSessionId(payload.session_id);
        } else {
          console.log('Keeping existing session:', currentStored);
        }
      } else if (!currentStored) {
        // 后端未提供 session_id 且本地也无有效 session：兜底生成
        const fallbackSid = generateSessionId();
        console.log('Generated fallback session:', fallbackSid);
        sessionIdRef.current = fallbackSid;
        setSessionId(fallbackSid);
        storeSessionId(fallbackSid);
      }
    },
    onDisconnect: () => {
      console.log('Disconnected');
    },
    onError: (error) => {
      console.error('WebSocket error:', error);
    },
  });

  // 获取会话列表
  const fetchSessions = useCallback(async () => {
    try {
      const payload = await request<{ sessions?: unknown[] }>('session.list', {
        limit: 20,
      });
      if (payload?.sessions && Array.isArray(payload.sessions)) {
        // 兼容新格式(对象数组)和旧格式(字符串数组)
        const normalized = payload.sessions.map((item) => {
          if (typeof item === 'string') {
            return { session_id: item } as Parameters<typeof setSessions>[0][number];
          }
          if (item && typeof item === 'object') {
            return item as Parameters<typeof setSessions>[0][number];
          }
          return null;
        }).filter(Boolean) as Parameters<typeof setSessions>[0];
        setSessions(normalized);
      }
    } catch (error) {
      console.error('Failed to fetch sessions:', error);
    }
  }, [request, setSessions]);

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

  const clearNewSessionToastTimer = useCallback(() => {
    if (newSessionToastTimerRef.current != null) {
      window.clearTimeout(newSessionToastTimerRef.current);
      newSessionToastTimerRef.current = null;
    }
  }, []);

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
      securityAlertTimerRef.current = setTimeout(() => {
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
      clearNewSessionToastTimer();
      clearHeartbeatToastTimer();
      clearSaveToastTimer();
    };
  }, [clearHeartbeatToastTimer, clearNewSessionToastTimer, clearRestartAutoCloseTimer, clearSaveToastTimer]);

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
    if (!isConnected || initialDataLoaded) {
      return;
    }
    void (async () => {
      await fetchConfig();
      await fetchSessions();
      setInitialDataLoaded(true);
    })();
  }, [fetchConfig, fetchSessions, initialDataLoaded, isConnected]);

  // 聊天处理完成后刷新会话列表，以便拾取自动生成的标题等元数据更新
  const prevProcessingRef = useRef(false);
  useEffect(() => {
    if (prevProcessingRef.current && !isProcessing) {
      void fetchSessions();
    }
    prevProcessingRef.current = isProcessing;
  }, [isProcessing, fetchSessions]);

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
    if (!isConnected || !sessionId || sessionId === 'new') return;
    
    // 仅处理以 sess_ 开头的会话 ID
    if (!sessionId.startsWith('sess_')) return;

    // 新建会话时跳过历史加载
    const isNew = useChatStore.getState().isNewSession;
    if (isNew) {
      useChatStore.getState().setNewSession(false);
      setHistoryPagerMeta(null);  // 新会话无历史，不显示分页栏
      setLoadingHistory(false);
      return;
    }

    // 清理之前的历史加载句柄
    disposeInFlightHistoryHandles();
    setHistoryPagerMeta(null);
    setHistoryLoadingMore(false);
    
    setLoadingHistory(true);
    // 开始历史会话加载
    const restoreHandle = beginHistoryRestore({
      sessionId: sessionId,
      onReady: (messages, totalPages) => {
        if (sessionIdRef.current !== sessionId) {
          setLoadingHistory(false);
          return;
        }
        historyRestoreFromPanelHintRef.current = false;
        clearMessages();
        messages.forEach((message) => addMessage(message));
        setHistoryPagerMeta({
          loadedPages: 1,
          totalPages: totalPages ?? 1,
        });
        setLoadingHistory(false);
        queueMicrotask(() => {
          historyRestoreHandleRef.current = null;
        });
      },
      onEmpty: (emptyTotalPages) => {
        if (sessionIdRef.current !== sessionId) {
          setLoadingHistory(false);
          return;
        }
        clearMessages();
        setHistoryPagerMeta({
          loadedPages: 1,
          totalPages: emptyTotalPages ?? 1,
        });
        if (historyRestoreFromPanelHintRef.current) {
          historyRestoreFromPanelHintRef.current = false;
          addMessage({
            id: `history-restore-empty-${Date.now()}`,
            role: 'system',
            content: tRef.current('sessions.restoreEmpty'),
            timestamp: new Date().toISOString(),
          });
        }
        setLoadingHistory(false);
        historyRestoreHandleRef.current = null;
      },
      onToolReplay: (items) => {
        if (sessionIdRef.current !== sessionId) {
          return;
        }
        clearSubtasks();
        for (const item of items) {
          if (item.kind === 'tool_call') {
            const n = normalizeToolCallPayload(item.payload);
            addToolCall(
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
        if (sessionIdRef.current !== sessionId) {
          return;
        }
        const harnessStore = useHarnessStore.getState();
        for (const item of items) {
          if (item.kind === 'harness_message') {
            const content = typeof item.payload.content === 'string' ? item.payload.content : '';
            const stage = typeof item.payload.stage === 'string' ? item.payload.stage : undefined;
            if (content) {
              harnessStore.addHarnessMessage(content, stage);
              // Update stage result with running status and label from message
              if (stage && content) {
                const existingStage = harnessStore.stageResults.find((s) => s.stage === stage);
                if (existingStage?.status !== 'running') {
                  harnessStore.updateStageResult({
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
              harnessStore.updateStageResult({
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
        setLoadingHistory(false);
      },
    });
    historyRestoreHandleRef.current = restoreHandle;

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
        historyRestoreHandleRef.current = null;
        // 发生错误时，设置 historyPagerMeta 为 null，显示欢迎信息
        setHistoryPagerMeta(null);
        console.error('Failed to load history:', error);
        setLoadingHistory(false);
        // 忽略 "invalid page_idx or session history not found" 错误，因为这是新会话的正常情况
        const errorMessage = error instanceof Error ? error.message : String(error);
        if (sessionIdRef.current === sessionId && !errorMessage.includes('invalid page_idx or session history not found')) {
          clearMessages();
          addMessage({
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
  ]);

  // 新建会话：立即生成可用的 session_id，避免停留在 'new' 导致无法发送消息
  const handleNewSession = useCallback(async () => {
    if (mode === 'team' && sessionIdRef.current && sessionIdRef.current !== 'new') {
      cancel(sessionIdRef.current);
    }
    // 切换模式/新建会话时直接设置状态，避免闪现
    useChatStore.getState().setSwitchingMode(true);
    useChatStore.getState().setNewSession(true);  // 标记新建会话，跳过历史加载
    useChatStore.getState().setInterruptResult(null);
    useChatStore.getState().setProcessing(false);
    useChatStore.getState().setThinking(false);
    useChatStore.getState().setPaused(false);
    // 集群模式下新建会话时清空成员列表和事件列表
    if (mode === 'team') {
      clearTeamRuntimeState();
      setTeamAreaExpanded(false);
    }
    disposeInFlightHistoryHandles();
    setHistoryPagerMeta(null);
    setHistoryLoadingMore(false);
    setProcessing(false);
    setThinking(false);
    setPaused(false);
    clearMessages();
    const { setContextCompressionStats } = useSessionStore.getState();
    setContextCompressionStats({
      rate: 0,
      beforeCompressed: 0,
      afterCompressed: 0,
    });
    clearTodos();
    resetHarnessStore();
    const newSid = generateSessionId();
    // 立即同步更新 ref 到新值，防止后续发送消息使用旧 ID
    sessionIdRef.current = newSid;
    setSessionId(newSid);
    try {
      const payload = await request<{ session_id?: string; sessionId?: string }>('session.create', {
        session_id: newSid,
        mode,
      }, { timeoutMs: 60_000 });  // session.create 可能因队列等待耗时较长，超时设为 60 秒
      // 后端返回 sessionId（驼峰），同时兼容 session_id（下划线）
      const createdSid =
        (typeof payload?.sessionId === 'string' && payload.sessionId)
          ? payload.sessionId
          : (typeof payload?.session_id === 'string' && payload.session_id)
            ? payload.session_id
            : newSid;
      // 如果后端返回的 ID 与生成的不一致，更新 ref
      if (createdSid !== newSid) {
        sessionIdRef.current = createdSid;
        setSessionId(createdSid);
      }
      setCurrentSession(null);
      storeSessionId(createdSid);
      // 设置模式：创建新 session 时已在前方 cancel 旧 session，
      // 此处只需设置模式状态，不再通过 switchMode 触发 interrupt，
      // 避免 interrupt 新 session 或与 cancel 响应产生状态冲突
      setMode(mode);
      if (createdSid && createdSid !== 'new') {
        updateSession(createdSid, { mode });
      }
      useChatStore.getState().setSwitchingMode(false);
      await fetchSessions();
    } catch (error) {
      console.error('Failed to create session:', error);
      // 不再回退 sessionId：后端可能已创建目录，且回退会导致
      // sessionIdRef 与当前 UI 状态不一致，后续 cancel 会指向错误的 session
      // 用户可以重新点击新建来再次尝试
      useChatStore.getState().setSwitchingMode(false);
      return;
    }
    setNewSessionToastVisible(true);
    clearNewSessionToastTimer();
    newSessionToastTimerRef.current = window.setTimeout(() => {
      setNewSessionToastVisible(false);
      newSessionToastTimerRef.current = null;
    }, 2000);
  }, [
    cancel,
    clearMessages,
    clearNewSessionToastTimer,
    clearTodos,
    disposeInFlightHistoryHandles,
    fetchSessions,
    mode,
    request,
    resetHarnessStore,
    setCurrentSession,
    setMode,
    setTeamAreaExpanded,
    setPaused,
    setProcessing,
    setThinking,
    updateSession,
  ]);

  // 切换模式
  const handleSwitchMode = useCallback((mode: AgentMode) => {
    if (!sessionId || sessionId === 'new') return;
    // 切换模式时直接设置状态，避免闪现
    useChatStore.getState().setSwitchingMode(true);
    useChatStore.getState().setProcessing(false);
    useChatStore.getState().setThinking(false);
    useChatStore.getState().setPaused(false);
    // 切换到集群模式时清空成员列表和事件列表
    if (mode === 'team') {
      clearTeamRuntimeState();
    }
    // 从集群模式切换到其他模式时，也需要清空成员列表和事件列表
    if (mode !== 'team' && useSessionStore.getState().mode === 'team') {
      clearTeamRuntimeState();
    }
    void switchMode(sessionId, mode);
  }, [sessionId, switchMode]);

  const handleSendMessage = useCallback((content: string, mediaItems?: MediaItem[]) => {
    const currentSessionId = sessionIdRef.current;
    if (!currentSessionId || currentSessionId === 'new') return;
    disposeInFlightHistoryHandles();
    void sendMessage(content, currentSessionId, mediaItems);
  }, [disposeInFlightHistoryHandles, sendMessage]);

  const handlePersistMedia = useCallback((content: string, mediaItems: MediaItem[]) => {
    const currentSessionId = sessionIdRef.current;
    if (!currentSessionId || currentSessionId === 'new') {
      return Promise.reject(new Error('会话未就绪，请稍后重试'));
    }
    return persistMedia(content, currentSessionId, mediaItems);
  }, [persistMedia]);

  useEffect(() => {
    return setA2UIActionHandler((message) => {
      const currentSessionId = sessionIdRef.current;
      if (!currentSessionId || currentSessionId === 'new') return;
      return sendStructuredChatContent(
        buildA2UIClientEventContent(message),
        currentSessionId,
      );
    });
  }, [sendStructuredChatContent]);

  const handleInterrupt = useCallback((newInput?: string) => {
    const currentSessionId = sessionIdRef.current;
    if (!currentSessionId || currentSessionId === 'new') return;
    const trimmed = newInput?.trim();
    if (!trimmed) return;
    void supplement(currentSessionId, trimmed);
  }, [supplement]);

  const handleCancel = useCallback(() => {
    const currentSessionId = sessionIdRef.current;
    if (!currentSessionId || currentSessionId === 'new') return;
    if (mode === 'team') {
      void pause(currentSessionId);
      return;
    }
    void cancel(currentSessionId);
  }, [cancel, mode, pause]);

  const handleUserAnswer = useCallback((requestId: string, answers: UserAnswer[], source?: string) => {
    const currentSessionId = sessionIdRef.current;
    if (!currentSessionId || currentSessionId === 'new') return;
    void sendUserAnswer(currentSessionId, requestId, answers, source);
  }, [sendUserAnswer]);

  const handleLoadMoreHistory = useCallback(async () => {
    if (!sessionId.startsWith('sess_') || !historyPagerMeta) return;
    if (historyLoadingMoreRef.current || historyPagerMeta.loadedPages >= historyPagerMeta.totalPages) return;

    const sid = sessionId;
    const nextPage = historyPagerMeta.loadedPages + 1;
    const fallbackTotal = historyPagerMeta.totalPages;
    const finishLoadingMore = () => {
      historyLoadingMoreRef.current = false;
      setHistoryLoadingMore(false);
      setLoadingHistory(false);
    };

    historyLoadingMoreRef.current = true;
    setHistoryLoadingMore(true);
    setLoadingHistory(true);
    const pageHandle = fetchHistoryPage({
      sessionId: sid,
      pageIdx: nextPage,
      onReady: ({ messages, toolReplay, harnessReplay, totalPages }) => {
        if (sessionIdRef.current !== sid) {
          finishLoadingMore();
          historyPageHandleRef.current = null;
          return;
        }
        prependMessages(messages);
        for (const item of toolReplay) {
          if (item.kind === 'tool_call') {
            const n = normalizeToolCallPayload(item.payload);
            addToolCall(
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
        for (const item of harnessReplay) {
          if (item.kind === 'harness_message') {
            const content = typeof item.payload.content === 'string' ? item.payload.content : '';
            const stage = typeof item.payload.stage === 'string' ? item.payload.stage : undefined;
            if (content) {
              harnessStore.addHarnessMessage(content, stage);
              // Update stage result with running status and label from message
              if (stage && content) {
                const existingStage = harnessStore.stageResults.find((s) => s.stage === stage);
                if (existingStage?.status !== 'running') {
                  harnessStore.updateStageResult({
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
              harnessStore.updateStageResult({
                stage,
                status: status as 'success' | 'failed' | 'timeout',
                error,
                messages,
                metrics,
              });
            }
          }
        }
        setHistoryPagerMeta({
          loadedPages: nextPage,
          totalPages: totalPages ?? fallbackTotal,
        });
        finishLoadingMore();
        historyPageHandleRef.current = null;
      },
      onEmpty: (emptyTotalPages) => {
        if (sessionIdRef.current !== sid) {
          finishLoadingMore();
          historyPageHandleRef.current = null;
          return;
        }
        setHistoryPagerMeta({
          loadedPages: nextPage,
          totalPages: emptyTotalPages ?? fallbackTotal,
        });
        finishLoadingMore();
        historyPageHandleRef.current = null;
      },
      onError: (message) => {
        console.warn('[history.page]', message);
      },
    });
    historyPageHandleRef.current = pageHandle;

    try {
      await request(HISTORY_GET_METHOD, {
        session_id: sid,
        page_idx: nextPage,
      });
    } catch (error) {
      pageHandle.dispose();
      historyPageHandleRef.current = null;
      console.error('Failed to load older history:', error);
      finishLoadingMore();
    }
  }, [
    addToolCall,
    addToolResult,
    historyPagerMeta,
    prependMessages,
    request,
    sessionId,
  ]);

  const handleRestoreSession = useCallback(
    async (targetSessionId: string, targetMode?: string) => {
      if (!targetSessionId.startsWith('sess_')) return;

      const resolvedMode = targetMode ?? mode;
      if (resolvedMode === 'team' && sessionId && sessionId !== targetSessionId) {
        try {
          await request('session.switch', {
            session_id: targetSessionId,
            mode: 'team',
          });
        } catch (error) {
          console.error('Failed to switch team session:', error);
          window.alert(t('sessions.errors.switchSession'));
          return;
        }
      }

      disposeInFlightHistoryHandles();
      setHistoryPagerMeta(null);
      setHistoryLoadingMore(false);
      setProcessing(false);
      setThinking(false);
      setPaused(false);
      clearTeamRuntimeState();
      clearMessages();
      clearTodos();
      clearSubtasks();
      resetHarnessStore();
      historyRestoreFromPanelHintRef.current = true;
      sessionIdRef.current = targetSessionId;
      setSessionId(targetSessionId);
      setCurrentSession(null);
      storeSessionId(targetSessionId);
      if (resolvedMode) {
        setMode(resolvedMode as AgentMode);
      }
      setActiveNav('chat');
      // 历史加载只由下方 useEffect 发起一次。若 sessionId 与当前相同，须 bump key 才会重跑 effect，
      // 否则 historyPagerMeta 会停在 null，无法向上滚动加载更早分页。
      setHistoryBootstrapKey((k) => k + 1);
      // 勿在此处再 beginHistoryRestore + history.get：会与 effect 并发双份 history.get，消息重复。
    },
    [
      clearMessages,
      clearSubtasks,
      clearTodos,
      disposeInFlightHistoryHandles,
      mode,
      request,
      resetHarnessStore,
      sessionId,
      setActiveNav,
      setCurrentSession,
      setHistoryLoadingMore,
      setHistoryPagerMeta,
      setMode,
      setPaused,
      setProcessing,
      setSessionId,
      setThinking,
      t,
    ]
  );

  const handleNavigate = useCallback((nav: MainNavKey) => {
    setActiveNav(nav);
    if (nav === 'skills') setHasVisitedSkills(true);
    if (nav === 'channels') setHasVisitedChannels(true);
  }, []);

  const handleExportShare = useCallback(async () => {
    const currentSessionId = sessionIdRef.current;
    if (!currentSessionId || currentSessionId === 'new' || (isProcessing && !isPaused) || isExportingShare) {
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

  return (
    <div className={`shell ${sidebarCollapsed ? 'shell--collapsed' : ''}`} data-testid="app-shell" data-session-id={sessionId}>
      {/* Navigation Sidebar - always rendered, 48px icon strip when collapsed */}
      <SessionSidebar
        activeNav={activeNav}
        onNavigate={handleNavigate}
        sessionId={sessionId}
        appVersion={typeof serverConfig?.app_version === 'string' ? serverConfig.app_version : '0.1.7'}
        isConnected={isConnected}
        onNewSession={handleNewSession}
        collapsed={sidebarCollapsed}
        onCollapse={() => setSidebarCollapsed(true)}
        onExpand={() => setSidebarCollapsed(false)}
      />

      {/* Main Content */}
      <main className={`content ${isTeamAreaExpanded ? 'content--team-expanded' : ''}`}>
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
            <div className={`flex-1 flex min-h-0 overflow-hidden ${isTeamAreaExpanded ? '' : 'card'}`}>
              {/* Chat Panel - 在展开时可拖拽调整宽度 */}
              <div
                className={`flex flex-col min-w-0 min-h-0 ${isTeamAreaExpanded ? '' : 'flex-1'}`}
                style={isTeamAreaExpanded ? { width: `${chatPanelWidthPct}%` } : undefined}
              >
                <div className={`flex-1 min-h-0 ${isTeamAreaExpanded ? 'card rounded-l-lg rounded-r-none' : ''}`}>
                  <ChatPanel
                    onSendMessage={handleSendMessage}
                    onPersistMedia={handlePersistMedia}
                    onInterrupt={handleInterrupt}
                    onCancel={handleCancel}
                    onSwitchMode={handleSwitchMode}
                    isProcessing={isProcessing}
                    onNewSession={handleNewSession}
                    onUserAnswer={handleUserAnswer}
                    onExportShare={handleExportShare}
                    isExportingShare={isExportingShare}
                    canExportShare={Boolean(sessionId && sessionId !== 'new' && (!isProcessing || isPaused))}
                    teamAreaExpanded={isTeamAreaExpanded}
                    historyPager={
                      historyPagerMeta
                        ? {
                            loadedPages: historyPagerMeta.loadedPages,
                            totalPages: historyPagerMeta.totalPages,
                            loadingMore: historyLoadingMore,
                            onLoadMore: handleLoadMoreHistory,
                          }
                        : null
                    }
                  />
                </div>
              </div>

              {/* 可拖拽分割线 */}
              {isTeamAreaExpanded && (
                <div
                  className="shrink-0 w-1 cursor-col-resize bg-[var(--bg)] hover:bg-gray-400 active:bg-gray-500 transition-colors"
                  onMouseDown={handleDividerMouseDown}
                />
              )}

              {/* Tool Panel / Expanded Team Panel */}
              {toolPanelHasContent && (
                <ToolPanel
                  sessionId={sessionId}
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
          <div className="app-section">
            <CronPanel sessionId={sessionId} />
          </div>
        )}
        {activeNav === 'configpanel' && (
          <div className="app-section">
            <ConfigPanel
              config={serverConfig}
              isConnected={isConnected}
              onSaveConfig={saveConfigAndRestart}
              onSaveAllConfig={saveAllConfigAndRestart}
              onValidateModel={validateModelConfig}
              initialExpandGroupTag={configInitialExpandGroup}
              onModelsReplaceAll={handleModelsReplaceAll}
              onModelValidate={validateModelConfig}
              onModelsRefresh={handleModelsRefresh}
              onAgentsTeamsSave={handleAgentsTeamsSave}
            />
          </div>
        )}
        {activeNav === 'logspanel' && (
          <div className="app-section">
            <LogsPanel isConnected={isConnected} />
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

      {/* 连接状态提示 */}
      {!isConnected && (
        <div className="app-toast-wrapper app-toast-wrapper--top">
          <div className="app-connection-toast animate-rise">
            {serverConfig ? t('connection.connecting') : t('connection.loadingConfig')}
          </div>
        </div>
      )}

      {/* 新建会话提示 */}
      {newSessionToastVisible && (
        <div className="app-toast-wrapper app-toast-wrapper--top-center">
          <div className="app-session-toast animate-rise">
            {t('chat.sessionCreated')}
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
