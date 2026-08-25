/**
 * ToolPanel 组件
 *
 * 工具面板，显示 Todo 列表和状态信息
 */

import { useTranslation } from 'react-i18next';
import { useChatStore, useSessionStore, useTodoStore } from '../../stores';
import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { Info } from 'lucide-react';
import { useSessionArtifacts, useSessionArtifactsCount } from '../ArtifactsPanel';
import { useTaskPlanningMetrics } from '../teamArea';
import { ExpandedPanel } from '../teamArea/ExpandedPanel';
import { loadTeamHistoryPanelState } from '../../features/teamHistoryPanelRestore';
import { TaskPlanningPanel } from '../teamArea/TaskPlanningPanel';
import { TeamMembersPanel } from '../teamArea/TeamMembersPanel';
import { CompactTaskList } from '../teamArea/CompactTaskList';
import { FileIcon } from '../FileIcon';
import { CollapsibleSection } from './CollapsibleSection';
import { TeamMemberAvatar } from '../TeamMemberAvatar';
import { isTeamLeaderMember } from '../../utils/teamMemberAvatar';
import { getMemberPlainName, type TabType, type TeamDetailTab } from '../teamArea/shared';
import type { TeamTask, TeamTaskStatus } from '../../stores/sessionStore';
import type { ContextUsageSummary, ProjectInfo, TodoItem, TodoStatus } from '../../types';
import teamIcon from '../../assets/team.svg';
import RecentTasksIcon from '../../assets/work-mode/progress-tasks.svg?react';
import artifactsIcon from '../../assets/artifacts.svg';
import emptyArtifactsIcon from '../../assets/empty-artifacts.svg';
import emptyMembersIcon from '../../assets/empty-members.svg';
import emptyPlanningIcon from '../../assets/empty-planning.svg';
import emptyReferencesIcon from '../../assets/empty-references.svg';
import skillIcon from '../../assets/sidebar/skill.svg';
import { CodeEnvironmentPanel } from '../../features/code-mode/CodeEnvironmentPanel';
import { CodeReviewPanel } from '../../features/code-mode/CodeReviewPanel';
import type { CodeReviewTarget } from '../../features/code-mode/types';
import { useCodeGitDiffWatch } from '../../features/code-mode/useCodeGitDiffWatch';
import { type SingleAgentToolTab } from '../../features/singleAgentPanelState';
import { SubagentExpandedPanel } from '../subagent/SubagentExpandedPanel';
import { useSubagentStore } from '../../stores/subagentStore';
import { useMinWidth } from '../../hooks/useResponsive';
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
  project?: ProjectInfo | null;
  isNewSessionPromotion?: boolean;
  teamAreaExpanded: boolean;
  teamAreaActiveTab: TabType;
  teamAreaActiveDetailTab: TeamDetailTab;
  teamAreaSelectedMemberId?: string;
  codeReviewTarget?: CodeReviewTarget | null;
  teamAreaSelectedArtifactId?: string;
  singleAgentPanelExpanded: boolean;
  singleAgentPanelActiveTab: SingleAgentToolTab;
  singleAgentPanelSelectedArtifactId?: string;
  setTeamAreaExpanded: (expanded: boolean) => void;
  setTeamAreaActiveTab: (tab: TabType) => void;
  setTeamAreaActiveDetailTab: (detailTab: TeamDetailTab) => void;
  setTeamAreaSelectedMemberId: (memberId: string) => void;
  setCodeReviewTarget?: (target: CodeReviewTarget | null) => void;
  setTeamAreaSelectedArtifactId: (artifactId: string) => void;
  setSingleAgentPanelExpanded: (expanded: boolean) => void;
  setSingleAgentPanelActiveTab: (tab: SingleAgentToolTab) => void;
  setSingleAgentPanelSelectedArtifactId: (artifactId: string) => void;
  shouldFullscreen?: boolean;
  onCloseFloating?: () => void;
}

function isEmptyValue(value: unknown): boolean {
  return value === undefined || value === null || value === '';
}

function formatContextTokens(value: number | null): string {
  if (value === null || !Number.isFinite(value)) return '--';
  const absolute = Math.abs(value);
  if (absolute < 1000) return `${Math.round(value)}`;
  if (absolute < 1_000_000) {
    const compact = Number((value / 1000).toFixed(absolute < 100_000 ? 1 : 0));
    return `${compact}k`;
  }
  const compact = Number((value / 1_000_000).toFixed(absolute < 10_000_000 ? 1 : 0));
  return `${compact}m`;
}

function formatContextPercentage(value: number | null): string {
  if (value === null || !Number.isFinite(value)) return '--';
  const percentage = Number((value * 100).toFixed(1));
  return Number.isInteger(percentage) ? `${percentage}` : percentage.toFixed(1);
}

function hasRenderableContextUsageSummary(summary: ContextUsageSummary | null): summary is ContextUsageSummary {
  return Boolean(
    summary &&
      summary.usedTokens !== null &&
      summary.limitTokens !== null &&
      summary.occupancyRate !== null
  );
}

function mergeById<T>(historyItems: T[], currentItems: T[], getId: (item: T) => string): T[] {
  const itemsById = new Map<string, T>(historyItems.map(item => [getId(item), item]));
  currentItems.forEach(item => {
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
  project = null,
  isNewSessionPromotion = false,
  teamAreaExpanded,
  teamAreaActiveTab,
  teamAreaActiveDetailTab,
  teamAreaSelectedMemberId,
  codeReviewTarget = null,
  teamAreaSelectedArtifactId,
  singleAgentPanelExpanded,
  singleAgentPanelActiveTab,
  singleAgentPanelSelectedArtifactId,
  setTeamAreaExpanded,
  setTeamAreaActiveTab,
  setTeamAreaActiveDetailTab,
  setTeamAreaSelectedMemberId,
  setCodeReviewTarget,
  setTeamAreaSelectedArtifactId,
  setSingleAgentPanelExpanded,
  setSingleAgentPanelActiveTab,
  setSingleAgentPanelSelectedArtifactId,
  shouldFullscreen = false,
  onCloseFloating,
}: ToolPanelProps) {
  const { t } = useTranslation();
  const isConnected = useSessionStore((state) => state.isConnected);
  const activeSessionId = useChatStore(s => s.activeSessionId);
  const mode = useSessionStore(s => s.runtimes[activeSessionId ?? '']?.mode ?? 'agent');
  const resolvedSessionId = sessionId ?? activeSessionId ?? '';
  const teamMembers = useSessionStore(s => s.runtimes[activeSessionId ?? '']?.teamMembers ?? []);
  const teamHistoryMessages = useSessionStore(s => s.runtimes[activeSessionId ?? '']?.teamHistoryMessages ?? []);
  const setTeamMembers = useSessionStore(s => s.setTeamMembers);
  const setTeamTaskEvents = useSessionStore(s => s.setTeamTaskEvents);
  const setTeamTasks = useSessionStore(s => s.setTeamTasks);
  const mergeTeamTaskProgressBaseline = useSessionStore(s => s.mergeTeamTaskProgressBaseline);
  const setTeamMemberExecutionEvents = useSessionStore(s => s.setTeamMemberExecutionEvents);
  const setTeamHistoryMessages = useSessionStore(s => s.setTeamHistoryMessages);
  const setTeamHumanShareCommands = useSessionStore(s => s.setTeamHumanShareCommands);
  const isProcessing = useChatStore(s => s.runtimes[activeSessionId ?? '']?.isProcessing ?? false);
  const contextCompressionRate = useSessionStore(s => s.runtimes[activeSessionId ?? '']?.contextCompressionRate ?? 0);
  const contextCompressionBefore = useSessionStore(s => s.runtimes[activeSessionId ?? '']?.contextCompressionBefore ?? null);
  const contextCompressionAfter = useSessionStore(s => s.runtimes[activeSessionId ?? '']?.contextCompressionAfter ?? null);
  const sessionKvCacheHitRate = useSessionStore(s => s.runtimes[activeSessionId ?? '']?.sessionKvCacheHitRate ?? null);
  const contextUsageSummary = useSessionStore(s => s.runtimes[activeSessionId ?? '']?.contextUsageSummary ?? null);
  const [planningExpanded, setPlanningExpanded] = useState(false);
  const [teamPlanningExpanded, setTeamPlanningExpanded] = useState(false);
  const [teamMembersExpanded, setTeamMembersExpanded] = useState(false);
  const [artifactsExpanded, setArtifactsExpanded] = useState(false);
  const { completedTasks: teamCompletedTasks, progressTasks, teamTasks, totalTasks: teamTotalTasks, now } = useTaskPlanningMetrics();
  const artifactsCount = useSessionArtifactsCount();
  const subagentCount = useSubagentStore(state => Object.keys(state.runtimes[resolvedSessionId]?.subagentsById ?? {}).length);
  const sessionArtifacts = useSessionArtifacts();
  const artifactTasks = useMemo(
    () =>
      sessionArtifacts.map(artifact => ({
        task_id: artifact.id,
        title: artifact.name,
        status: 'completed' as const,
        timestamp: artifact.timestamp,
      })),
    [sessionArtifacts],
  );
  const messages = useChatStore(s => s.runtimes[activeSessionId ?? '']?.messages ?? []);
  const toolExecutions = useChatStore(s => s.runtimes[activeSessionId ?? '']?.toolExecutions ?? new Map());
  const skillTasks = useMemo(() => {
    const seen = new Set<string>();
    for (const msg of messages) {
      if (msg.skills && msg.skills.length > 0) {
        for (const skill of msg.skills) {
          const trimmed = skill.trim();
          if (trimmed && !seen.has(trimmed)) {
            seen.add(trimmed);
          }
        }
      }
    }
    for (const execution of toolExecutions.values()) {
      const name = execution.toolCall.name
        .trim()
        .toLowerCase()
        .replace(/[\s-]+/g, '_');
      if (name !== 'skill_tool' && !name.endsWith('.skill_tool') && !name.endsWith('/skill_tool') && !name.endsWith(':skill_tool')) {
        continue;
      }
      const args = execution.toolCall.arguments;
      if (args) {
        const skillName = (args.skill_name ?? args.skillName) as unknown;
        if (typeof skillName === 'string') {
          const trimmed = skillName.trim();
          if (trimmed) seen.add(trimmed);
        }
      }
    }
    return Array.from(seen).map(name => ({
      task_id: `skill-${name}`,
      title: name,
      status: 'completed' as const,
    }));
  }, [messages, toolExecutions]);
  const teamLeaderMemberIds = useSessionStore(s => s.runtimes[activeSessionId ?? '']?.teamLeaderMemberIds ?? []);
  const memberTasks = useMemo(
    () =>
      teamMembers
        .filter(member => {
          const memberKeys = [member.member_id, member.name || ''].map(v =>
            v
              .trim()
              .toLowerCase()
              .replace(/[\s_-]+/g, ''),
          );
          return !(
            isTeamLeaderMember(member.member_id) ||
            member.mode === 'leader' ||
            member.mode === 'team_leader' ||
            teamLeaderMemberIds.some(leaderId =>
              memberKeys.includes(
                leaderId
                  .trim()
                  .toLowerCase()
                  .replace(/[\s_-]+/g, ''),
              ),
            )
          );
        })
        .map(member => ({
          task_id: member.member_id,
          title: `@${member.member_id} ${getMemberPlainName(member)}`,
          status: 'completed' as const,
          assignee: member.member_id,
        })),
    [teamMembers, teamLeaderMemberIds],
  );
  // 规划/性能模式下复用 TaskPlanningPanel 紧凑态：把 TodoItem 降级为 TeamTask
  const todos = useTodoStore(s => s.runtimes[activeSessionId ?? '']?.todos ?? []);
  const codeProject = project?.work_mode === 'code' && !project.is_default ? project : null;
  const canReviewCode = Boolean(codeProject && sessionId && sessionId !== 'new');
  const codeGitDiffWatch = useCodeGitDiffWatch({
    projectId: canReviewCode && codeProject ? codeProject.project_id : null,
    sessionId: canReviewCode && sessionId ? sessionId : null,
    enabled: canReviewCode,
  });
  const codeReviewPanel =
    canReviewCode && codeProject && sessionId ? (
      <CodeReviewPanel project={codeProject} sessionId={sessionId} target={codeReviewTarget} diffWatch={codeGitDiffWatch} isProcessing={isProcessing} />
    ) : undefined;
  const todoTeamTasks = useMemo(() => todos.map(todoItemToTeamTask), [todos]);
  const todoCompletedTasks = useMemo(() => todos.filter(t => t.status === 'completed').length, [todos]);
  const hydratedTeamHistorySessionRef = useRef<string | null>(null);
  const loadingTeamHistorySessionRef = useRef<string | null>(null);
  const floatingPanelRef = useRef<HTMLDivElement>(null);

  const isUltraWide = useMinWidth('ultraWide');

  useEffect(() => {
    if (!onCloseFloating || isUltraWide) return;
    const handler = (e: MouseEvent) => {
      const el = floatingPanelRef.current;
      if (!el) return;
      if (el.contains(e.target as Node)) return;
      onCloseFloating();
    };
    const timer = window.setTimeout(() => {
      document.addEventListener('mousedown', handler);
    }, 0);
    return () => {
      window.clearTimeout(timer);
      document.removeEventListener('mousedown', handler);
    };
  }, [onCloseFloating, isUltraWide]);

  useEffect(() => {
    if (mode !== 'team' || !isConnected || !sessionId || !(sessionId.startsWith('sess_') || sessionId.startsWith('web_'))) {
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
      .then(historyState => {
        loadingTeamHistorySessionRef.current = null;
        hydratedTeamHistorySessionRef.current = sessionId;
        const current = useSessionStore.getState().runtimes[sessionId];
        const mergedMembers = mergeById(historyState.members, current?.teamMembers ?? [], member => member.member_id);
        if (mergedMembers.length > 0) {
          setTeamMembers(sessionId, mergedMembers);
        }

        const mergedTaskEvents = mergeById(historyState.taskEvents, current?.teamTaskEvents ?? [], event => event.task_id);
        // Always apply — an empty restored list must clear stale events too.
        setTeamTaskEvents(sessionId, mergedTaskEvents);

        // History/snapshot is the authoritative board after restore. Never import
        // live-only task_ids (LLM `id` orphans left in the waiting column from
        // a prior optimistic upsert). Always setTeamTasks — including [] — so
        // an empty restore actually clears those orphans instead of leaving
        // the previous store contents untouched.
        const restoredTaskIds = new Set(historyState.tasks.map(task => task.task_id));
        const liveTasksForMerge = (current?.teamTasks ?? []).filter(task => restoredTaskIds.has(task.task_id));
        const mergedTasks = mergeById(historyState.tasks, liveTasksForMerge, task => task.task_id);
        setTeamTasks(sessionId, mergedTasks);
        mergeTeamTaskProgressBaseline(sessionId, historyState.taskProgressBaseline);

        const mergedExecutionEvents = mergeById(historyState.executionEvents, current?.teamMemberExecutionEvents ?? [], event => event.id);
        if (mergedExecutionEvents.length > 0) {
          setTeamMemberExecutionEvents(sessionId, mergedExecutionEvents);
        }

        const mergedHumanShareCommands = mergeById(
          historyState.humanShareCommands,
          current?.teamHumanShareCommands ?? [],
          command => `${command.sessionId}:${command.memberName}`,
        );
        if (mergedHumanShareCommands.length > 0) {
          setTeamHumanShareCommands(sessionId, mergedHumanShareCommands);
        }

        setTeamHistoryMessages(sessionId, historyState.messages);
      })
      .catch(error => {
        loadingTeamHistorySessionRef.current = null;
        if (error instanceof DOMException && error.name === 'AbortError') {
          return;
        }
        console.warn('[team.history.panel] restore failed:', error);
      });

    return () => {
      controller.abort();
    };
  }, [
    isConnected,
    isNewSessionPromotion,
    mergeTeamTaskProgressBaseline,
    mode,
    sessionId,
    setTeamHistoryMessages,
    setTeamHumanShareCommands,
    setTeamMemberExecutionEvents,
    setTeamMembers,
    setTeamTaskEvents,
    setTeamTasks,
  ]);

  const panelExpanded = mode === 'team' ? teamAreaExpanded : singleAgentPanelExpanded;
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
  const visibleContextUsageSummary = shouldMaskContextUsage ? null : contextUsageSummary;
  const contextUsageDisplay = hasRenderableContextUsageSummary(visibleContextUsageSummary)
    ? t('toolPanel.contextUsageValue', {
        percent: formatContextPercentage(visibleContextUsageSummary.occupancyRate),
        used: formatContextTokens(visibleContextUsageSummary.usedTokens),
        limit: formatContextTokens(visibleContextUsageSummary.limitTokens),
      })
    : compressionDisplay;
  const sessionKvCacheDisplay =
    sessionKvCacheHitRate == null ? '--' : `${(sessionKvCacheHitRate * 100).toFixed(1)}%`;

  if (panelExpanded && mode !== 'auto_harness') {
    const isTeam = mode === 'team';
    const testId = isTeam ? 'tool-panel-expanded-team' : 'tool-panel-expanded-single-agent';

    return (
      <div data-testid={testId} className="bg-panel h-full overflow-hidden flex-1 flex flex-col min-w-[512px]">
        <div className="h-full bg-panel flex flex-col overflow-hidden">
          <ExpandedPanel
            activeTab={isTeam ? teamAreaActiveTab : singleAgentPanelActiveTab}
            onTabChange={
              isTeam
                ? tab => {
                    setTeamAreaActiveTab(tab as TabType);
                    if (tab === 'team') setTeamAreaSelectedMemberId('');
                  }
                : tab => setSingleAgentPanelActiveTab(tab as SingleAgentToolTab)
            }
            onCollapse={
              isTeam
                ? () => {
                    setTeamAreaExpanded(false);
                    setTeamAreaSelectedMemberId('');
                  }
                : () => {
                    setSingleAgentPanelExpanded(false);
                  }
            }
            shouldFullscreen={shouldFullscreen}
            reviewPanel={codeReviewPanel}
            selectedArtifactId={isTeam ? teamAreaSelectedArtifactId : singleAgentPanelSelectedArtifactId}
            onArtifactSelect={isTeam ? setTeamAreaSelectedArtifactId : setSingleAgentPanelSelectedArtifactId}
            middleTab={
              isTeam
                ? { key: 'team', label: t('team.membersTab'), icon: <img src={teamIcon} width={16} height={16} aria-hidden="true" /> }
                : { key: 'subagents', label: t('subagent.title'), icon: <img src={teamIcon} width={16} height={16} aria-hidden="true" /> }
            }
            showMiddleTab={isTeam ? true : subagentCount > 0}
            resolveActiveTab={(tab, count, review) => {
              if (tab === 'artifacts' && count > 0) return 'artifacts';
              if (isTeam) return tab === 'review' && !review ? 'planning' : tab;
              if (tab === 'subagents' && subagentCount > 0) return 'subagents';
              if (tab === 'review' && review) return 'review';
              return 'planning';
            }}
            renderMiddleTabContent={() =>
              isTeam ? (
                <TeamMembersPanel
                  variant="expanded"
                  members={teamMembers}
                  selectedMemberId={teamAreaSelectedMemberId ?? ''}
                  selectedMember={teamMembers.find(m => m.member_id === teamAreaSelectedMemberId) ?? null}
                  activeDetailTab={teamAreaActiveDetailTab}
                  historyMessages={teamHistoryMessages}
                  onSelectMember={setTeamAreaSelectedMemberId}
                  onDetailTabChange={setTeamAreaActiveDetailTab}
                />
              ) : (
                <SubagentExpandedPanel sessionId={resolvedSessionId} />
              )
            }
            renderPlanningContent={() =>
              isTeam ? (
                <TaskPlanningPanel
                  variant="expanded"
                  tasks={teamTasks}
                  progressTasks={progressTasks}
                  now={now}
                  members={teamMembers}
                  totalTasks={teamTotalTasks}
                  completedTasks={teamCompletedTasks}
                />
              ) : (
                <TaskPlanningPanel
                  variant="expanded"
                  tasks={todoTeamTasks}
                  members={teamMembers}
                  totalTasks={todos.length}
                  completedTasks={todoCompletedTasks}
                  hideAssignee
                  emptyIllustration={emptyPlanningIcon}
                />
              )
            }
          />
        </div>
      </div>
    );
  }

  // 收起模式 - 悬浮面板
  const isTeam = mode === 'team';
  const planningProps = isTeam
    ? {
        tasks: teamTasks,
        totalTasks: teamTotalTasks,
        completedTasks: teamCompletedTasks,
        expanded: teamPlanningExpanded,
      }
    : {
        tasks: todoTeamTasks,
        totalTasks: todos.length,
        completedTasks: todoCompletedTasks,
        expanded: planningExpanded,
      };
  const expandTo = (tab: TabType) => {
    if (isTeam) {
      setTeamAreaActiveTab(tab);
      if (tab === 'team') {
        setTeamAreaSelectedMemberId('');
      }
      setTeamAreaExpanded(true);
    } else {
      setSingleAgentPanelActiveTab(tab as SingleAgentToolTab);
      setSingleAgentPanelExpanded(true);
    }
  };

  const collapsedSections = [
    {
      key: 'planning',
      testId: isTeam ? 'tool-panel-team-pane' : 'tool-panel-planning-pane',
      render: () => (
        <CollapsibleSection
          title={t('chat.recentTasks')}
          icon={<RecentTasksIcon className="h-4 w-4" aria-hidden="true" />}
          childCount={planningProps.tasks.length}
          maxCollapsedCount={4}
          onExpand={() => expandTo('planning')}
          onExpandAll={() => (isTeam ? setTeamPlanningExpanded(true) : setPlanningExpanded(true))}
          dataTestId={isTeam ? 'tool-panel-team-planning' : 'tool-panel-planning'}
        >
          <TaskPlanningPanel
            variant="compact"
            members={teamMembers}
            hideBorder
            hideHeader
            hideExpandButton
            hideAssignee={!isTeam}
            title={t('chat.recentTasks')}
            maxCollapsedCount={4}
            {...planningProps}
            emptyIllustration={emptyPlanningIcon}
          />
        </CollapsibleSection>
      ),
    },
    isTeam && {
      key: 'members',
      testId: 'tool-panel-team-members-pane',
      render: () => (
        <CollapsibleSection
          title={t('team.membersTab')}
          icon={<img src={teamIcon} width={16} height={16} aria-hidden="true" />}
          childCount={memberTasks.length}
          maxCollapsedCount={4}
          onExpand={() => expandTo('team')}
          onExpandAll={() => setTeamMembersExpanded(true)}
          dataTestId="tool-panel-team-members"
          defaultCollapsed
          autoExpandOnContent
        >
          <CompactTaskList
            tasks={memberTasks}
            members={teamMembers}
            hideAssignee
            maxCollapsedCount={4}
            expanded={teamMembersExpanded}
            emptyText={t('team.noMemberData')}
            emptyIllustration={emptyMembersIcon}
            renderStatusIcon={task => (
              <TeamMemberAvatar member={task.assignee ?? ''} alt={task.title ?? ''} className="h-4 w-4 rounded-full shrink-0" imageClassName="rounded-full" />
            )}
          />
        </CollapsibleSection>
      ),
    },
    canReviewCode &&
      codeProject &&
      sessionId && {
        key: 'code',
        testId: 'tool-panel-code-environment-pane',
        render: () => (
          <CollapsibleSection
            title={t('codeMode.environment')}
            icon={<Info size={16} />}
            showExpandButton={false}
            onExpand={() => {
              setCodeReviewTarget?.({ source: 'working_tree' });
              expandTo('review');
            }}
            dataTestId="tool-panel-code-environment"
          >
            <CodeEnvironmentPanel
              project={codeProject}
              isProcessing={isProcessing}
              diffWatch={codeGitDiffWatch}
              onReview={() => {
                setCodeReviewTarget?.({ source: 'working_tree' });
                if (mode === 'team') {
                  setTeamAreaActiveTab('review');
                  setTeamAreaExpanded(true);
                } else {
                  setSingleAgentPanelActiveTab('review');
                  setSingleAgentPanelExpanded(true);
                }
              }}
            />
          </CollapsibleSection>
        ),
      },
    {
      key: 'artifacts',
      testId: 'tool-panel-artifacts-pane',
      render: () => (
        <CollapsibleSection
          title={t('artifacts.tab')}
          icon={<img src={artifactsIcon} width={16} height={16} aria-hidden="true" />}
          childCount={artifactsCount}
          maxCollapsedCount={4}
          onExpand={() => expandTo('artifacts')}
          onExpandAll={() => setArtifactsExpanded(true)}
          dataTestId="tool-panel-artifacts"
          defaultCollapsed
          autoExpandOnContent
        >
          <CompactTaskList
            tasks={artifactTasks}
            members={[]}
            hideAssignee
            maxCollapsedCount={4}
            expanded={artifactsExpanded}
            emptyText={t('artifacts.empty')}
            emptyIllustration={emptyArtifactsIcon}
            renderStatusIcon={task => <FileIcon fileName={task.title ?? ''} size={16} className="shrink-0" />}
            onTaskClick={taskId => {
              expandTo('artifacts');
              if (isTeam) {
                setTeamAreaSelectedArtifactId(taskId);
              } else {
                setSingleAgentPanelSelectedArtifactId(taskId);
              }
            }}
          />
        </CollapsibleSection>
      ),
    },
    {
      key: 'references',
      testId: 'tool-panel-references-pane',
      render: () => (
        <CollapsibleSection
          title={t('references.tab')}
          icon={<img src={artifactsIcon} width={16} height={16} aria-hidden="true" />}
          childCount={skillTasks.length}
          showExpandButton={false}
          dataTestId="tool-panel-references"
          defaultCollapsed
          autoExpandOnContent
        >
          <CompactTaskList
            tasks={skillTasks}
            members={[]}
            hideAssignee
            emptyText={t('references.empty')}
            emptyIllustration={emptyReferencesIcon}
            renderStatusIcon={() => <img src={skillIcon} width={16} height={16} aria-hidden="true" className="shrink-0" />}
          />
        </CollapsibleSection>
      ),
    },
  ].filter(Boolean) as {
    key: string;
    testId: string;
    render: () => ReactNode;
  }[];

  return (
    <div ref={floatingPanelRef} data-testid="tool-panel-collapsed" className="bg-panel py-0 pl-6 pr-4 tool-panel-floating">
      <div className="bg-panel flex flex-col">
        {collapsedSections.map(section => (
          <div key={section.key} data-testid={section.testId}>
            {section.render()}
          </div>
        ))}
        {!teamAreaExpanded && (
          <>
            <hr className="border-0 border-t border-border m-0" />
            <div data-testid="tool-panel-status-card" className="toolpanel-status-card px-3">
              <h3 data-testid="tool-panel-status-title" className="toolpanel-status-card__title">
                <svg width="14" height="14" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg">
                  <rect x="1" y="8" width="3" height="7" rx="0.5" fill="currentColor" opacity="0.5" />
                  <rect x="6" y="4" width="3" height="11" rx="0.5" fill="currentColor" opacity="0.7" />
                  <rect x="11" y="1" width="3" height="14" rx="0.5" fill="currentColor" />
                </svg>
                {t('toolPanel.status')}
              </h3>
              <div className="space-y-2">
                <div data-testid="tool-panel-status-context-compression" className="toolpanel-status-card__row">
                  <span className="text-text-muted">{t('toolPanel.contextCompression')}</span>
                  <span className="mono text-text">{compressionDisplay}</span>
                </div>
                <div className="toolpanel-status-card__row">
                  <span className="text-text-muted">{t('toolPanel.contextUsage')}</span>
                  <span className="mono text-text">{contextUsageDisplay}</span>
                </div>
                <div className="toolpanel-status-card__row">
                  <span className="text-text-muted">{t('toolPanel.sessionKvCache')}</span>
                  <span className="mono text-text">{sessionKvCacheDisplay}</span>
                </div>
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
