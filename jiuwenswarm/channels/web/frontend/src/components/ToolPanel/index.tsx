/**
 * ToolPanel 组件
 *
 * 工具面板，显示 Todo 列表和状态信息
 */

import { useTranslation } from 'react-i18next';
import { useChatStore, useSessionStore, useTodoStore } from '../../stores';
import { useEffect, useId, useMemo, useRef, useState, type ReactNode } from 'react';
import { FileCheck2, FileText, Info, Minimize2 } from 'lucide-react';
import { ArtifactsPanel, useSessionArtifacts, useSessionArtifactsCount } from '../ArtifactsPanel';
import { TeamArea, useTaskPlanningMetrics } from '../teamArea';
import { loadTeamHistoryPanelState } from '../../features/teamHistoryPanelRestore';
import { TaskPlanningPanel } from '../teamArea/TaskPlanningPanel';
import { CompactTaskList } from '../teamArea/CompactTaskList';
import { FileIcon } from '../FileIcon';
import { CollapsibleSection } from './CollapsibleSection';
import { HarnessExtensionTree } from './HarnessExtensionTree';
import { TeamMemberAvatar } from '../TeamMemberAvatar';
import { isTeamLeaderMember } from '../../utils/teamMemberAvatar';
import { getMemberPlainName, type TabType, type TeamDetailTab } from '../teamArea/shared';
import type { TeamTask, TeamTaskStatus } from '../../stores/sessionStore';
import type { ProjectInfo, TodoItem, TodoStatus } from '../../types';
import teamProcessIcon from '../../assets/team-process.svg';
import teamIcon from '../../assets/team.svg';
import recentTasksIcon from '../../assets/work-mode/recent-tasks.svg';
import artifactsIcon from '../../assets/artifacts.svg';
import skillIcon from '../../assets/sidebar/skill.svg';
import maximizeIcon from '../../assets/maximize.svg';
import expandIcon from '../../assets/expand.svg';
import { CodeEnvironmentPanel } from '../../features/code-mode/CodeEnvironmentPanel';
import { CodeReviewPanel } from '../../features/code-mode/CodeReviewPanel';
import type { CodeReviewTarget } from '../../features/code-mode/types';
import { useCodeGitDiffWatch } from '../../features/code-mode/useCodeGitDiffWatch';
import { type SingleAgentToolTab } from '../../features/singleAgentPanelState';
import { SubagentExpandedPanel } from '../subagent/SubagentExpandedPanel';
import { useSubagentStore } from '../../stores/subagentStore';
import TeamMembersIcon from '../../assets/subagent/team-members.svg?react';
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
  onMaximize?: () => void;
  onRestore?: () => void;
  maximized?: boolean;
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
  sessionId,
  activeTab,
  tasks,
  members,
  totalTasks,
  completedTasks,
  onTabChange,
  onCollapse,
  onMaximize,
  onRestore,
  maximized,
  reviewPanel,
  selectedArtifactId,
  onArtifactSelect,
}: {
  sessionId: string;
  activeTab: SingleAgentToolTab;
  tasks: TeamTask[];
  members: Parameters<typeof TaskPlanningPanel>[0]['members'];
  totalTasks: number;
  completedTasks: number;
  onTabChange: (tab: SingleAgentToolTab) => void;
  onCollapse: () => void;
  onMaximize: () => void;
  onRestore: () => void;
  maximized: boolean;
  reviewPanel?: ReactNode;
  selectedArtifactId?: string;
  onArtifactSelect: (artifactId: string) => void;
}) {
  const { t } = useTranslation();
  const tabPanelId = useId();
  const artifactsCount = useSessionArtifactsCount();
  const subagentCount = useSubagentStore((state) => Object.keys(state.runtimes[sessionId]?.subagentsById ?? {}).length);
  const resolvedTab =
    activeTab === 'artifacts' && artifactsCount > 0
      ? 'artifacts'
      : activeTab === 'subagents' && subagentCount > 0
        ? 'subagents'
        : activeTab === 'review' && reviewPanel
          ? 'review'
          : 'planning';
  const tabs = [
    {
      key: 'planning',
      label: t('team.planning.tab'),
      count: `${completedTasks}/${totalTasks}`,
      icon: <img src={teamProcessIcon} width={16} height={16} aria-hidden="true" />,
    },
    ...(subagentCount > 0
      ? [{
          key: 'subagents' as const,
          label: t('subagent.title'),
          icon: <TeamMembersIcon className="h-4 w-4" aria-hidden="true" />,
        }]
      : []),
    ...(artifactsCount > 0
      ? [{
          key: 'artifacts' as const,
          label: t('artifacts.tab'),
          count: artifactsCount,
          icon: <FileText size={16} />,
        }]
      : []),
    ...(reviewPanel ? [{ key: 'review' as const, label: t('codeMode.review'), icon: <FileCheck2 size={16} /> }] : []),
  ];

  return (
    <div data-testid="tool-panel-expanded-body" className="flex h-full flex-col overflow-hidden bg-card">
      <div data-testid="tool-panel-expanded-header" className="single-agent-tool-tabs">
        <div data-testid="tool-panel-expanded-tabs" className="single-agent-tool-tabs__list" role="tablist" aria-label={t('team.toolTabs')}>
          {tabs.map((tab) => (
            <button
              key={tab.key}
              data-testid="tool-panel-tab"
              data-variant={tab.key}
              id={`${tabPanelId}-${tab.key}`}
              type="button"
              role="tab"
              aria-selected={resolvedTab === tab.key}
              aria-controls={`${tabPanelId}-panel`}
              className={`single-agent-tool-tab ${
                resolvedTab === tab.key
                  ? 'single-agent-tool-tab--active'
                  : ''
              }`}
              onClick={() => onTabChange(tab.key as SingleAgentToolTab)}
            >
              {tab.icon}
              {tab.label}{'count' in tab ? ` (${tab.count})` : ''}
            </button>
          ))}
        </div>

        <div className="flex items-center gap-2" >
          <button
            onClick={maximized ? onRestore : onMaximize}
            data-testid="tool-panel-maximize"
            className="rounded p-2 text-text-muted hover:bg-secondary hover:text-text"
            aria-label={maximized ? t('team.restore') : t('team.maximize')}
            title={maximized ? t('team.restore') : t('team.maximize')}
          >
            {maximized ? <Minimize2 size={12} /> : <img src={maximizeIcon} alt="" width={12} height={12} />}
          </button>
          <button
            onClick={onCollapse}
            data-testid="tool-panel-collapse"
            className="rounded p-2 text-text-muted  hover:bg-secondary hover:text-text"
            aria-label={t('team.collapse')}
            title={t('team.collapse')}
          >
            <img src={expandIcon} alt="" width={12} height={12} />
          </button>
        </div>
      </div>

      <div
        data-testid="tool-panel-expanded-content"
        id={`${tabPanelId}-panel`}
        className="flex min-h-0 flex-1 overflow-hidden"
        role="tabpanel"
        aria-labelledby={`${tabPanelId}-${resolvedTab}`}
      >
        {resolvedTab === 'subagents' ? (
          <SubagentExpandedPanel sessionId={sessionId} />
        ) : resolvedTab === 'artifacts' ? (
          <div data-testid="tool-panel-artifacts-pane" data-variant="artifacts" className="flex min-w-0 flex-1 overflow-hidden">
            <ArtifactsPanel selectedArtifactId={selectedArtifactId} onSelectArtifact={onArtifactSelect} />
          </div>
        ) : resolvedTab === 'review' && reviewPanel ? (
          <div data-testid="tool-panel-review-pane" data-variant="review" className="flex min-w-0 flex-1 overflow-hidden">{reviewPanel}</div>
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
  onMaximize,
  onRestore,
  maximized = false,
}: ToolPanelProps) {
  const { t } = useTranslation();
  const { isConnected } = useSessionStore();
  const activeSessionId = useChatStore((s) => s.activeSessionId);
  const mode = useSessionStore((s) => s.runtimes[activeSessionId ?? '']?.mode ?? 'agent');
  const lastMacroRoutedMode = useSessionStore(
    (s) => s.runtimes[activeSessionId ?? '']?.lastMacroRoutedMode ?? null,
  );
  const effectiveMode =
    mode === 'auto' && lastMacroRoutedMode ? lastMacroRoutedMode : mode;
  const resolvedSessionId = sessionId ?? activeSessionId ?? '';
  const teamMembers = useSessionStore((s) => s.runtimes[activeSessionId ?? '']?.teamMembers ?? []);
  const teamHistoryMessages = useSessionStore((s) => s.runtimes[activeSessionId ?? '']?.teamHistoryMessages ?? []);
  const setTeamMembers = useSessionStore((s) => s.setTeamMembers);
  const setTeamTaskEvents = useSessionStore((s) => s.setTeamTaskEvents);
  const setTeamTasks = useSessionStore((s) => s.setTeamTasks);
  const mergeTeamTaskProgressBaseline = useSessionStore((s) => s.mergeTeamTaskProgressBaseline);
  const setTeamMemberExecutionEvents = useSessionStore((s) => s.setTeamMemberExecutionEvents);
  const setTeamHistoryMessages = useSessionStore((s) => s.setTeamHistoryMessages);
  const setTeamHumanShareCommands = useSessionStore((s) => s.setTeamHumanShareCommands);
  const isProcessing = useChatStore((s) => s.runtimes[activeSessionId ?? '']?.isProcessing ?? false);
  const [planningExpanded, setPlanningExpanded] = useState(false);
  const [teamPlanningExpanded, setTeamPlanningExpanded] = useState(false);
  const [teamMembersExpanded, setTeamMembersExpanded] = useState(false);
  const { completedTasks: teamCompletedTasks, teamTasks, totalTasks: teamTotalTasks } = useTaskPlanningMetrics();
  const artifactsCount = useSessionArtifactsCount();
  const sessionArtifacts = useSessionArtifacts();
  const artifactTasks = useMemo(
    () => sessionArtifacts.map((artifact) => ({
      task_id: artifact.id,
      title: artifact.name,
      status: 'completed' as const,
      timestamp: artifact.timestamp,
    })),
    [sessionArtifacts],
  );
  const messages = useChatStore((s) => s.runtimes[activeSessionId ?? '']?.messages ?? []);
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
    return Array.from(seen).map((name) => ({
      task_id: `skill-${name}`,
      title: name,
      status: 'completed' as const,
    }));
  }, [messages]);
  const teamLeaderMemberIds = useSessionStore((s) => s.runtimes[activeSessionId ?? '']?.teamLeaderMemberIds ?? []);
  const memberTasks = useMemo(
    () => teamMembers
      .filter((member) => {
        const memberKeys = [member.member_id, member.name || ''].map((v) => v.trim().toLowerCase().replace(/[\s_-]+/g, ''));
        return !(
          isTeamLeaderMember(member.member_id) ||
          member.mode === 'leader' ||
          member.mode === 'team_leader' ||
          teamLeaderMemberIds.some((leaderId) => memberKeys.includes(leaderId.trim().toLowerCase().replace(/[\s_-]+/g, '')))
        );
      })
      .map((member) => ({
        task_id: member.member_id,
        title: `${getMemberPlainName(member)} @${member.member_id}`,
        status: 'completed' as const,
        assignee: member.member_id,
      })),
    [teamMembers, teamLeaderMemberIds],
  );
  // 规划/性能模式下复用 TaskPlanningPanel 紧凑态：把 TodoItem 降级为 TeamTask
  const todos = useTodoStore((s) => s.runtimes[activeSessionId ?? '']?.todos ?? []);
  const codeProject = project?.work_mode === 'code' && !project.is_default ? project : null;
  const canReviewCode = Boolean(codeProject && sessionId && sessionId !== 'new');
  const codeGitDiffWatch = useCodeGitDiffWatch({
    projectId: canReviewCode && codeProject ? codeProject.project_id : null,
    sessionId: canReviewCode && sessionId ? sessionId : null,
    enabled: canReviewCode,
  });
  const codeReviewPanel = canReviewCode && codeProject && sessionId
    ? <CodeReviewPanel project={codeProject} sessionId={sessionId} target={codeReviewTarget} diffWatch={codeGitDiffWatch} isProcessing={isProcessing} />
    : undefined;
  const todoTeamTasks = useMemo(() => todos.map(todoItemToTeamTask), [todos]);
  const todoCompletedTasks = useMemo(
    () => todos.filter((t) => t.status === 'completed').length,
    [todos],
  );
  const hydratedTeamHistorySessionRef = useRef<string | null>(null);
  const loadingTeamHistorySessionRef = useRef<string | null>(null);

  useEffect(() => {
    if (
      mode !== 'team'
      || !isConnected
      || !sessionId
      || !(sessionId.startsWith('sess_') || sessionId.startsWith('web_'))
    ) {
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
        // Always apply — an empty restored list must clear stale events too.
        setTeamTaskEvents(sessionId, mergedTaskEvents);

        // History/snapshot is the authoritative board after restore. Never import
        // live-only task_ids (LLM `id` orphans left in the waiting column from
        // a prior optimistic upsert). Always setTeamTasks — including [] — so
        // an empty restore actually clears those orphans instead of leaving
        // the previous store contents untouched.
        const restoredTaskIds = new Set(historyState.tasks.map((task) => task.task_id));
        const liveTasksForMerge = (current?.teamTasks ?? []).filter((task) =>
          restoredTaskIds.has(task.task_id)
        );
        const mergedTasks = mergeById(
          historyState.tasks,
          liveTasksForMerge,
          (task) => task.task_id
        );
        setTeamTasks(sessionId, mergedTasks);
        mergeTeamTaskProgressBaseline(sessionId, historyState.taskProgressBaseline);

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
  }, [isConnected, isNewSessionPromotion, mergeTeamTaskProgressBaseline, mode, sessionId, setTeamHistoryMessages, setTeamHumanShareCommands, setTeamMemberExecutionEvents, setTeamMembers, setTeamTaskEvents, setTeamTasks]);

  const panelExpanded = effectiveMode === 'team' ? teamAreaExpanded : singleAgentPanelExpanded;

  if (panelExpanded && mode !== 'auto_harness') {
    if (effectiveMode !== 'team') {
      return (
        <div
          data-testid="tool-panel-expanded-single-agent"
          className="bg-panel h-full overflow-hidden flex-1 flex flex-col min-w-[512px]"
        >
          <div className="h-full bg-panel flex flex-col overflow-hidden">
            <ExpandedSingleAgentArea
              sessionId={resolvedSessionId}
              activeTab={singleAgentPanelActiveTab}
              tasks={todoTeamTasks}
              members={teamMembers}
              totalTasks={todos.length}
              completedTasks={todoCompletedTasks}
              onTabChange={setSingleAgentPanelActiveTab}
              onCollapse={() => { setSingleAgentPanelExpanded(false); onRestore?.(); }}
              onMaximize={() => onMaximize?.()}
              onRestore={() => onRestore?.()}
              maximized={maximized}
              reviewPanel={codeReviewPanel}
              selectedArtifactId={singleAgentPanelSelectedArtifactId}
              onArtifactSelect={setSingleAgentPanelSelectedArtifactId}
            />
          </div>
        </div>
      );
    }

    // 展开模式 - 更宽的面板，只显示 TeamArea
    return (
      <div
        data-testid="tool-panel-expanded-team"
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
            selectedArtifactId={teamAreaSelectedArtifactId}
            onTabChange={setTeamAreaActiveTab}
            onDetailTabChange={setTeamAreaActiveDetailTab}
            onMemberSelect={setTeamAreaSelectedMemberId}
            onArtifactSelect={setTeamAreaSelectedArtifactId}
            onCollapse={() => {
              setTeamAreaExpanded(false);
              setTeamAreaSelectedMemberId('');
            }}
            reviewPanel={codeReviewPanel}
          />
        </div>
      </div>
    );
  }

  if (mode === 'auto_harness') {
    return (
      <div
        data-testid="tool-panel"
        className="bg-panel border-l border-border h-full overflow-hidden py-3 shrink-0"
        style={{ width: 'var(--tool-panel-width)' }}
      >
        <div className="h-full bg-panel flex flex-col overflow-hidden">
          <div className="flex-1 overflow-hidden mb-3">
            <div className="overflow-hidden h-full flex flex-col px-3">
              <HarnessExtensionTree />
            </div>
          </div>
        </div>
      </div>
    );
  }

  // 收起模式 - 悬浮面板
  const isTeam = effectiveMode === 'team';
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
          icon={<img src={recentTasksIcon} width={16} height={16} aria-hidden="true" />}
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
          childCount={teamMembers.length}
          maxCollapsedCount={4}
          onExpand={() => expandTo('team')}
          onExpandAll={() => setTeamMembersExpanded(true)}
          dataTestId="tool-panel-team-members"
        >
          <CompactTaskList
            tasks={memberTasks}
            members={teamMembers}
            hideAssignee
            maxCollapsedCount={4}
            expanded={teamMembersExpanded}
            emptyText={t('team.noMemberData')}
            renderStatusIcon={(task) => (
              <TeamMemberAvatar
                member={task.assignee ?? ''}
                alt={task.title ?? ''}
                className="h-4 w-4 rounded-full shrink-0"
                imageClassName="rounded-full"
              />
            )}
          />
        </CollapsibleSection>
      ),
    },
    canReviewCode && codeProject && sessionId && {
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
              if (isTeam) {
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
          onExpand={() => expandTo('artifacts')}
          dataTestId="tool-panel-artifacts"
        >
          <CompactTaskList
            tasks={artifactTasks}
            members={[]}
            hideAssignee
            emptyText={t('artifacts.empty')}
            renderStatusIcon={(task) => <FileIcon fileName={task.title ?? ''} size={16} className="shrink-0" />}
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
        >
          <CompactTaskList
            tasks={skillTasks}
            members={[]}
            hideAssignee
            emptyText={t('references.empty')}
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
    <div
      data-testid="tool-panel-collapsed"
      className="bg-panel py-0 px-6 tool-panel-floating"
    >
      <div className="bg-panel flex flex-col">
        {collapsedSections.map((section) => (
          <div key={section.key} data-testid={section.testId}>
            {section.render()}
          </div>
        ))}
      </div>
    </div>
  );
}
