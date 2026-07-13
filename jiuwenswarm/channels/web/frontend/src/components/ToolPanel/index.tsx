/**
 * ToolPanel 组件
 *
 * 工具面板，显示 Todo 列表和状态信息
 */

import { useTranslation } from 'react-i18next';
import { useChatStore, useSessionStore } from '../../stores';
import { useEffect, useRef } from 'react';
import { webRequest } from '../../services/webClient';
import { TodoList } from '../TodoList';
import { TeamArea } from '../teamArea';
import { HarnessExtensionTree } from './HarnessExtensionTree';
import { loadTeamHistoryPanelState } from '../../features/teamHistoryPanelRestore';
import { type TabType, type TeamDetailTab } from '../teamArea/shared';
import './ToolPanel.css';

interface ToolPanelProps {
  sessionId?: string;
  teamAreaExpanded: boolean;
  teamAreaActiveTab: TabType;
  teamAreaActiveDetailTab: TeamDetailTab;
  teamAreaSelectedMemberId?: string;
  setTeamAreaExpanded: (expanded: boolean) => void;
  setTeamAreaActiveTab: (tab: TabType) => void;
  setTeamAreaActiveDetailTab: (detailTab: TeamDetailTab) => void;
  setTeamAreaSelectedMemberId: (memberId: string) => void;
  sidebarCollapsed?: boolean;
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

export function ToolPanel({
  sessionId,
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
  const {
    contextCompressionRate,
    contextCompressionBefore,
    contextCompressionAfter,
    isConnected,
    memoryUsage,
    setMemoryUsage,
    mode,
    teamMembers,
    setTeamMembers,
    setTeamTaskEvents,
    setTeamTasks,
    setTeamMemberExecutionEvents,
    teamHistoryMessages,
    setTeamHistoryMessages,
    setTeamHumanShareCommands,
  } = useSessionStore();
  const { isProcessing, messages } = useChatStore();
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
      setTeamHistoryMessages([]);
      hydratedTeamHistorySessionRef.current = null;
      loadingTeamHistorySessionRef.current = null;
      return;
    }
    if (hydratedTeamHistorySessionRef.current !== sessionId) {
      setTeamHistoryMessages([]);
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
        const current = useSessionStore.getState();
        const mergedMembers = mergeById(
          historyState.members,
          current.teamMembers,
          (member) => member.member_id
        );
        if (mergedMembers.length > 0) {
          setTeamMembers(mergedMembers);
        }

        const mergedTaskEvents = mergeById(
          historyState.taskEvents,
          current.teamTaskEvents,
          (event) => event.task_id
        );
        if (mergedTaskEvents.length > 0) {
          setTeamTaskEvents(mergedTaskEvents);
        }

        const mergedTasks = mergeById(
          historyState.tasks,
          current.teamTasks,
          (task) => task.task_id
        );
        if (mergedTasks.length > 0) {
          setTeamTasks(mergedTasks);
        }

        const mergedExecutionEvents = mergeById(
          historyState.executionEvents,
          current.teamMemberExecutionEvents,
          (event) => event.id
        );
        if (mergedExecutionEvents.length > 0) {
          setTeamMemberExecutionEvents(mergedExecutionEvents);
        }

        const mergedHumanShareCommands = mergeById(
          historyState.humanShareCommands,
          current.teamHumanShareCommands,
          (command) => `${command.sessionId}:${command.memberName}`
        );
        if (mergedHumanShareCommands.length > 0) {
          setTeamHumanShareCommands(mergedHumanShareCommands);
        }

        setTeamHistoryMessages(historyState.messages);
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
  }, [isConnected, mode, sessionId, setTeamHistoryMessages, setTeamHumanShareCommands, setTeamMemberExecutionEvents, setTeamMembers, setTeamTaskEvents, setTeamTasks]);

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

  if (teamAreaExpanded && mode === 'team') {
    // 展开模式 - 更宽的面板，只显示 TeamArea
    return (
      <div
        data-testid="tool-panel"
        className="bg-panel h-full overflow-hidden flex-1 flex flex-col rounded-r-lg"
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
      className="bg-panel border-border h-full overflow-hidden px-3 shrink-0"
      style={{ width: 'var(--tool-panel-width)' }}
    >
      <div className="h-full bg-panel flex flex-col overflow-hidden">
        {/* Auto-harness extension file tree */}
        {mode === 'auto_harness' ? (
          <div className="flex-1 overflow-hidden mb-4">
            <div className="bg-card rounded-lg border border-border overflow-hidden h-full">
              <HarnessExtensionTree />
            </div>
          </div>
        ) : mode === 'team' ? (
          /* 团队任务概览和成员列表 */
          <div className="flex-1 overflow-hidden mb-4">
            <div className="bg-card rounded-lg overflow-hidden h-full flex flex-col">
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
          /* Todo 列表 */
          <div className="flex-1 overflow-y-auto mb-4">
            <div className="bg-card rounded-lg border border-border overflow-hidden h-full">
              <TodoList />
            </div>
          </div>
        )}

        {/* 状态显示 - 只在收起模式下显示 */}
        {!teamAreaExpanded && (
          <div className="toolpanel-status-card">
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
        )}

        {/* 底部信息区：与左侧版本信息保持一致 - 只在收起模式下显示 */}
        {!teamAreaExpanded && (
          <div
            className="shrink-0 pt-4 text-text-muted text-center"
            style={{ fontSize: 'var(--font-size-xs)' }}
          >
            <div className="px-2.5">
              <span>{t('toolPanel.poweredBy')}</span>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
