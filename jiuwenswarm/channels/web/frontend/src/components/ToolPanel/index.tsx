/**
 * ToolPanel 组件
 *
 * 工具面板，显示 Todo 列表和状态信息
 */

import { useTranslation } from 'react-i18next';
import { useChatStore, useSessionStore, useTodoStore } from '../../stores';
import { useEffect, useMemo, useRef } from 'react';
import { FileText, Minimize2 } from 'lucide-react';
import { webRequest } from '../../services/webClient';
import { ArtifactsPanel, useSessionArtifactsCount } from '../ArtifactsPanel';
import { TeamArea } from '../teamArea';
import { loadTeamHistoryPanelState } from '../../features/teamHistoryPanelRestore';
import { TaskPlanningPanel } from '../teamArea/TaskPlanningPanel';
import { HarnessExtensionTree } from './HarnessExtensionTree';
import { type TabType, type TeamDetailTab } from '../teamArea/shared';
import type { TeamTask, TeamTaskStatus } from '../../stores/sessionStore';
import type { TodoItem, TodoStatus } from '../../types';
import teamProcessIcon from '../../assets/team-process.svg';
import './ToolPanel.css';

/** 规划/性能模式下把 TodoItem 降级映射为 TeamTask，复用 TaskPlanningPanel 紧凑态样式 */
function todoItemToTeamTask(todo: TodoItem): TeamTask {
  const statusMap: Record<TodoStatus, TeamTaskStatus> = {
    pending: 'pending',
    in_progress: 'in_progress',
    completed: 'completed',
  };
  const ts = todo.updatedAt ? Date.parse(todo.updatedAt) : NaN;
  return {
    task_id: todo.id,
    title: todo.content || todo.activeForm || todo.id,
    content: todo.activeForm && todo.activeForm !== todo.content ? todo.activeForm : undefined,
    status: statusMap[todo.status] ?? 'pending',
    assignee: todo.claimedBy,
    timestamp: Number.isFinite(ts) ? ts : undefined,
  };
}

interface ToolPanelProps {
  sessionId?: string;
  isNewSessionPromotion?: boolean;
  teamAreaExpanded: boolean;
  teamAreaActiveTab: TabType;
  teamAreaActiveDetailTab: TeamDetailTab;
  teamAreaSelectedMemberId?: string;
  setTeamAreaExpanded: (expanded: boolean) => void;
  setTeamAreaActiveTab: (tab: TabType) => void;
  setTeamAreaActiveDetailTab: (detailTab: TeamDetailTab) => void;
  setTeamAreaSelectedMemberId: (memberId: string) => void;
}

function isEmptyValue(value: unknown): boolean {
  return value === undefined || value === null || value === '';
}

function mergeById<T>(
  historyItems: T[],
  currentItems: T[],
  getId: (item: T) => string
): T[] {
  const itemsById = new Map<string, T>(historyItems.map((item) => [getId(item), item]));
  currentItems.forEach((item) => {
    const id = getId(item);
    const existing = itemsById.get(id);
    if (existing && typeof existing === 'object' && typeof item === 'object') {
      // Partial WS state may omit fields — merge with persisted history to avoid data loss
      const merged = { ...existing } as Record<string, unknown>;
      for (const [key, value] of Object.entries(item as Record<string, unknown>)) {
        if (!isEmptyValue(value) || isEmptyValue(merged[key])) {
          merged[key] = value;
        }
      }
      itemsById.set(id, merged as T);
    } else {
      itemsById.set(id, item);
    }
  });
  return Array.from(itemsById.values());
}

function ExpandedSingleAgentArea({
  activeTab,
  tasks,
  members,
  totalTasks,
  completedTasks,
  onTabChange,
  onCollapse,
}: {
  activeTab: TabType;
  tasks: TeamTask[];
  members: Parameters<typeof TaskPlanningPanel>[0]['members'];
  totalTasks: number;
  completedTasks: number;
  onTabChange: (tab: TabType) => void;
  onCollapse: () => void;
}) {
  const { t } = useTranslation();
  const artifactsCount = useSessionArtifactsCount();
  const resolvedTab = activeTab === 'artifacts' ? 'artifacts' : 'planning';
  const tabs = [
    {
      key: 'planning',
      label: t('team.planning.tab'),
      count: `${completedTasks}/${totalTasks}`,
      icon: <img src={teamProcessIcon} width={16} height={16} aria-hidden="true" />,
    },
    {
      key: 'artifacts',
      label: t('artifacts.tab'),
      count: artifactsCount,
      icon: <FileText size={16} />,
    },
  ] as const;

  return (
    <div className="flex h-full flex-col overflow-hidden bg-card">
      <div className="flex shrink-0 items-center justify-between px-6 py-4 bg-card border-b border-border">
        <div className="flex items-center gap-2">
          {tabs.map((tab) => (
            <button
              key={tab.key}
              className={`h-9 rounded-lg px-4 text-sm  flex items-center gap-2 ${
                resolvedTab === tab.key
                  ? 'bg-secondary font-medium text-text'
                  : 'text-text-muted hover:bg-secondary/50 hover:text-text'
              }`}
              onClick={() => onTabChange(tab.key)}
            >
              {tab.icon}
              {tab.label} ({tab.count})
            </button>
          ))}
        </div>

        <button
          onClick={onCollapse}
          className="rounded p-2 text-text-muted  hover:bg-secondary hover:text-text"
          title={t('team.collapse')}
        >
          <Minimize2 size={12} />
        </button>
      </div>

      <div className="flex min-h-0 flex-1 overflow-hidden">
        {resolvedTab === 'artifacts' ? (
          <div className="flex min-w-0 flex-1 overflow-hidden">
            <ArtifactsPanel />
          </div>
        ) : (
          <TaskPlanningPanel
            variant="expanded"
            tasks={tasks}
            members={members}
            totalTasks={totalTasks}
            completedTasks={completedTasks}
            hideAssignee
          />
        )}
      </div>
    </div>
  );
}

export function ToolPanel({
  sessionId,
  isNewSessionPromotion = false,
  teamAreaExpanded,
  teamAreaActiveTab,
  teamAreaActiveDetailTab,
  teamAreaSelectedMemberId,
  setTeamAreaExpanded,
  setTeamAreaActiveTab,
  setTeamAreaActiveDetailTab,
  setTeamAreaSelectedMemberId,
}: ToolPanelProps) {
  const { t } = useTranslation();
  const { isConnected, memoryUsage, setMemoryUsage } = useSessionStore();
  const activeSessionId = useChatStore((s) => s.activeSessionId);
  const contextCompressionRate = useSessionStore((s) => s.runtimes[activeSessionId ?? '']?.contextCompressionRate ?? 0);
  const contextCompressionBefore = useSessionStore((s) => s.runtimes[activeSessionId ?? '']?.contextCompressionBefore ?? null);
  const contextCompressionAfter = useSessionStore((s) => s.runtimes[activeSessionId ?? '']?.contextCompressionAfter ?? null);
  const mode = useSessionStore((s) => s.runtimes[activeSessionId ?? '']?.mode ?? 'agent');
  const teamMembers = useSessionStore((s) => s.runtimes[activeSessionId ?? '']?.teamMembers ?? []);
  const teamHistoryMessages = useSessionStore((s) => s.runtimes[activeSessionId ?? '']?.teamHistoryMessages ?? []);
  const setTeamMembers = useSessionStore((s) => s.setTeamMembers);
  const setTeamTaskEvents = useSessionStore((s) => s.setTeamTaskEvents);
  const setTeamTasks = useSessionStore((s) => s.setTeamTasks);
  const setTeamMemberExecutionEvents = useSessionStore((s) => s.setTeamMemberExecutionEvents);
  const setTeamHistoryMessages = useSessionStore((s) => s.setTeamHistoryMessages);
  const setTeamHumanShareCommands = useSessionStore((s) => s.setTeamHumanShareCommands);
  const isProcessing = useChatStore((s) => s.runtimes[activeSessionId ?? '']?.isProcessing ?? false);
  const messages = useChatStore((s) => s.runtimes[activeSessionId ?? '']?.messages ?? []);
  // 规划/性能模式下复用 TaskPlanningPanel 紧凑态：把 TodoItem 降级为 TeamTask
  const todos = useTodoStore((s) => s.runtimes[activeSessionId ?? '']?.todos ?? []);
  const todoTeamTasks = useMemo(() => todos.map(todoItemToTeamTask), [todos]);
  const todoCompletedTasks = useMemo(
    () => todos.filter((t) => t.status === 'completed').length,
    [todos],
  );
  const hydratedTeamHistorySessionRef = useRef<string | null>(null);
  const loadingTeamHistorySessionRef = useRef<string | null>(null);

  useEffect(() => {
    if (!isConnected) {
      setMemoryUsage(null);
      return;
    }

    let disposed = false;
    let timerId: number | null = null;

    const refreshMemoryUsage = async () => {
      try {
        const payload = await webRequest<Record<string, unknown>>('memory.compute');
        if (disposed) return;

        const rssMb =
          typeof payload.rss_mb === 'number' && Number.isFinite(payload.rss_mb)
            ? payload.rss_mb
            : null;
        const usedPercent =
          typeof payload.used_percent === 'number' && Number.isFinite(payload.used_percent)
            ? payload.used_percent
            : null;

        setMemoryUsage({ rssMb, usedPercent });
      } catch {
        if (!disposed) {
          setMemoryUsage(null);
        }
      }
    };

    void refreshMemoryUsage();
    timerId = window.setInterval(() => {
      void refreshMemoryUsage();
    }, 10000);

    return () => {
      disposed = true;
      if (timerId != null) {
        window.clearInterval(timerId);
      }
    };
  }, [isConnected, setMemoryUsage]);

  useEffect(() => {
    if (mode !== 'team' || !isConnected || !sessionId?.startsWith('sess_')) {
      if (sessionId) setTeamHistoryMessages(sessionId, []);
      hydratedTeamHistorySessionRef.current = null;
      loadingTeamHistorySessionRef.current = null;
      return;
    }
    if (isNewSessionPromotion) {
      setTeamHistoryMessages(sessionId, []);
      hydratedTeamHistorySessionRef.current = sessionId;
      loadingTeamHistorySessionRef.current = null;
      return;
    }
    if (hydratedTeamHistorySessionRef.current !== sessionId) {
      setTeamHistoryMessages(sessionId, []);
    }
    if (hydratedTeamHistorySessionRef.current === sessionId) {
      return;
    }
    if (loadingTeamHistorySessionRef.current === sessionId) {
      return;
    }

    const controller = new AbortController();
    loadingTeamHistorySessionRef.current = sessionId;
    void loadTeamHistoryPanelState(sessionId, controller.signal)
      .then((historyState) => {
        loadingTeamHistorySessionRef.current = null;
        hydratedTeamHistorySessionRef.current = sessionId;
        const current = useSessionStore.getState().runtimes[sessionId];
        const mergedMembers = mergeById(
          historyState.members,
          current?.teamMembers ?? [],
          (member) => member.member_id
        );
        if (mergedMembers.length > 0) {
          setTeamMembers(sessionId, mergedMembers);
        }

        const mergedTaskEvents = mergeById(
          historyState.taskEvents,
          current?.teamTaskEvents ?? [],
          (event) => event.task_id
        );
        if (mergedTaskEvents.length > 0) {
          setTeamTaskEvents(sessionId, mergedTaskEvents);
        }

        const mergedTasks = mergeById(
          historyState.tasks,
          current?.teamTasks ?? [],
          (task) => task.task_id
        );
        if (mergedTasks.length > 0) {
          setTeamTasks(sessionId, mergedTasks);
        }

        const mergedExecutionEvents = mergeById(
          historyState.executionEvents,
          current?.teamMemberExecutionEvents ?? [],
          (event) => event.id
        );
        if (mergedExecutionEvents.length > 0) {
          setTeamMemberExecutionEvents(sessionId, mergedExecutionEvents);
        }

        const mergedHumanShareCommands = mergeById(
          historyState.humanShareCommands,
          current?.teamHumanShareCommands ?? [],
          (command) => `${command.sessionId}:${command.memberName}`
        );
        if (mergedHumanShareCommands.length > 0) {
          setTeamHumanShareCommands(sessionId, mergedHumanShareCommands);
        }

        setTeamHistoryMessages(sessionId, historyState.messages);
      })
      .catch((error) => {
        loadingTeamHistorySessionRef.current = null;
        if (error instanceof DOMException && error.name === 'AbortError') {
          return;
        }
        console.warn('[team.history.panel] restore failed:', error);
      });

    return () => {
      controller.abort();
    };
  }, [isConnected, isNewSessionPromotion, mode, sessionId, setTeamHistoryMessages, setTeamHumanShareCommands, setTeamMemberExecutionEvents, setTeamMembers, setTeamTaskEvents, setTeamTasks]);

  const memoryDisplay =
    memoryUsage.rssMb == null
      ? '--'
      : `${memoryUsage.rssMb.toFixed(1)} MB${memoryUsage.usedPercent == null ? '' : ` (${memoryUsage.usedPercent.toFixed(1)}%)`}`;
  let latestUserMessageIndex = -1;
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    if (messages[i].role === 'user') {
      latestUserMessageIndex = i;
      break;
    }
  }
  const hasVisibleReplyAfterLatestUser = messages
    .slice(latestUserMessageIndex + 1)
    .some(
      (message) =>
        (message.role === 'assistant' || message.id.startsWith('team-leader-')) &&
        Boolean(message.content.trim())
    );
  const shouldMaskContextUsage =
    isProcessing && latestUserMessageIndex >= 0 && !hasVisibleReplyAfterLatestUser;
  const visibleContextCompressionBefore = shouldMaskContextUsage ? 0 : contextCompressionBefore;
  const visibleContextCompressionAfter = shouldMaskContextUsage ? 0 : contextCompressionAfter;
  const beforeK = ((visibleContextCompressionBefore ?? 0) / 1000).toFixed(1);
  const afterK = ((visibleContextCompressionAfter ?? 0) / 1000).toFixed(1);
  let compressionRateDisplay;
  if (
    visibleContextCompressionBefore === 0 ||
    visibleContextCompressionBefore === null ||
    visibleContextCompressionAfter === 0 ||
    visibleContextCompressionAfter === null
  ) {
    compressionRateDisplay = '--';
  } else if (visibleContextCompressionAfter === visibleContextCompressionBefore) {
    compressionRateDisplay = '100.0';
  } else {
    compressionRateDisplay = Number.isFinite(contextCompressionRate)
      ? contextCompressionRate.toFixed(1)
      : '0.0';
  }
  const compressionDisplay = `${afterK}K/${beforeK}K (${compressionRateDisplay}%)`;

  if (teamAreaExpanded && mode !== 'auto_harness') {
    if (mode !== 'team') {
      return (
        <div
          data-testid="tool-panel"
          className="bg-panel h-full overflow-hidden flex-1 flex flex-col"
        >
          <div className="h-full bg-panel flex flex-col overflow-hidden">
            <ExpandedSingleAgentArea
              activeTab={teamAreaActiveTab}
              tasks={todoTeamTasks}
              members={teamMembers}
              totalTasks={todos.length}
              completedTasks={todoCompletedTasks}
              onTabChange={setTeamAreaActiveTab}
              onCollapse={() => setTeamAreaExpanded(false)}
            />
          </div>
        </div>
      );
    }

    // 展开模式 - 更宽的面板，只显示 TeamArea
    return (
      <div
        data-testid="tool-panel"
        className="bg-panel h-full overflow-hidden flex-1 flex flex-col"
      >
        <div className="h-full bg-panel flex flex-col overflow-hidden">
          <TeamArea
            members={teamMembers}
            historyMessages={teamHistoryMessages}
            expanded={true}
            activeTab={teamAreaActiveTab}
            activeDetailTab={teamAreaActiveDetailTab}
            selectedMemberId={teamAreaSelectedMemberId}
            onTabChange={setTeamAreaActiveTab}
            onDetailTabChange={setTeamAreaActiveDetailTab}
            onMemberSelect={setTeamAreaSelectedMemberId}
            onCollapse={() => {
              setTeamAreaExpanded(false);
              setTeamAreaSelectedMemberId('');
            }}
          />
        </div>
      </div>
    );
  }

  // 收起模式 - 原始宽度
  return (
    <div
      data-testid="tool-panel"
      className="bg-panel border-l border-border h-full overflow-hidden py-3 shrink-0"
      style={{ width: 'var(--tool-panel-width)' }}
    >
      <div className="h-full bg-panel flex flex-col overflow-hidden">
        {/* Auto-harness extension file tree */}
        {mode === 'auto_harness' ? (
          <div className="flex-1 overflow-hidden mb-3">
            <div className="overflow-hidden h-full flex flex-col px-3">
              <HarnessExtensionTree />
            </div>
          </div>
        ) : mode === 'team' ? (
          /* 团队任务概览和成员列表 */
          <div className="flex-1 overflow-hidden mb-3">
            <div className="overflow-hidden h-full flex flex-col">
              <TeamArea
                members={teamMembers}
                historyMessages={teamHistoryMessages}
                expanded={false}
                onExpand={(tab, memberId) => {
                  setTeamAreaActiveTab(tab);
                  setTeamAreaActiveDetailTab('members');
                  setTeamAreaSelectedMemberId(memberId || '');
                  setTeamAreaExpanded(true);
                }}
              />
            </div>
          </div>
        ) : (
          /* 任务概述（复用集群模式紧凑态样式，数据来自 TodoItem） */
          <div className="flex-1 overflow-hidden mb-3">
            <TaskPlanningPanel
              variant="compact"
              tasks={todoTeamTasks}
              members={teamMembers}
              totalTasks={todos.length}
              completedTasks={todoCompletedTasks}
              hideBorder
              onExpand={() => {
                setTeamAreaActiveTab('planning');
                setTeamAreaExpanded(true);
              }}
              hideAssignee
              title={t('chat.recentTasks')}
            />
          </div>
        )}

        {/* 状态显示 - 只在收起模式下显示 */}
        {!teamAreaExpanded && (
          <>
            <hr className="border-0 border-t border-border m-0" />
            <div className="toolpanel-status-card px-3">
            <h3 className="toolpanel-status-card__title">
              <svg width="14" height="14" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg">
                <rect x="1" y="8" width="3" height="7" rx="0.5" fill="currentColor" opacity="0.5" />
                <rect x="6" y="4" width="3" height="11" rx="0.5" fill="currentColor" opacity="0.7" />
                <rect x="11" y="1" width="3" height="14" rx="0.5" fill="currentColor" />
              </svg>
              {t('toolPanel.status')}
            </h3>
            <div className="space-y-2">
              <div className="toolpanel-status-card__row">
                <span className="text-text-muted">{t('toolPanel.contextCompression')}</span>
                <span className="mono text-text">{compressionDisplay}</span>
              </div>
              <div className="toolpanel-status-card__row">
                <span className="text-text-muted">{t('toolPanel.memoryUsage')}</span>
                <span className="mono text-text">{memoryDisplay}</span>
              </div>
            </div>
          </div>
          </>
        )}
      </div>
    </div>
  );
}
