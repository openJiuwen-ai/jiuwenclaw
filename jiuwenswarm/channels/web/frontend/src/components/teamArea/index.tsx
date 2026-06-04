/**
 * TeamArea component - cluster mode task overview and member execution detail.
 */

import { useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { Minimize2, Users } from 'lucide-react';
import { useSessionStore, useTodoStore } from '../../stores';
import type { Message } from '../../types';
import { TaskPlanningPanel } from './TaskPlanningPanel';
import { TeamMembersPanel } from './TeamMembersPanel';
import teamProcessIcon from '../../assets/team-process.svg';
import {
  normalizeTaskStatus,
  type TabType,
  type TeamDetailTab,
  type TeamAreaProps,
  type TeamMember,
} from './shared';

function useTaskPlanningMetrics() {
  const { todos } = useTodoStore();
  const { teamTaskEvents, teamTasks } = useSessionStore();

  const totalTasks = useMemo(() => {
    if (teamTasks.length > 0) return teamTasks.length;
    const taskIds = new Set<string>();
    todos.forEach((todo) => taskIds.add(todo.id));
    teamTaskEvents.forEach((event) => {
      if (event.task_id) taskIds.add(event.task_id);
    });
    return taskIds.size;
  }, [teamTaskEvents, teamTasks.length, todos]);

  const completedTasks = useMemo(() => {
    if (teamTasks.length > 0) {
      return teamTasks.filter((task) => task.status === 'completed').length;
    }
    const completed = new Set<string>();
    todos.forEach((todo) => {
      if (normalizeTaskStatus(todo.status) === 'completed') completed.add(todo.id);
    });
    teamTaskEvents.forEach((event) => {
      if (event.task_id && normalizeTaskStatus(event.status, event.type) === 'completed') {
        completed.add(event.task_id);
      }
    });
    return completed.size;
  }, [teamTaskEvents, teamTasks, todos]);

  return { completedTasks, teamTasks, totalTasks };
}

function CompactTeamArea({
  members,
  onExpand,
}: {
  members: TeamMember[];
  onExpand?: (tab: TabType, memberId?: string) => void;
}) {
  const { completedTasks, teamTasks, totalTasks } = useTaskPlanningMetrics();

  return (
    <>
      <TaskPlanningPanel
        variant="compact"
        tasks={teamTasks}
        members={members}
        totalTasks={totalTasks}
        completedTasks={completedTasks}
        onExpand={() => onExpand?.('planning')}
      />
      <TeamMembersPanel
        variant="compact"
        members={members}
        tasks={teamTasks}
        onExpand={() => onExpand?.('team')}
        onMemberClick={(memberId) => onExpand?.('team', memberId)}
      />
    </>
  );
}



function ExpandedTeamArea({
  members,
  historyMessages = [],
  activeTab,
  activeDetailTab,
  selectedMemberId: externalSelectedMemberId,
  onTabChange,
  onDetailTabChange,
  onMemberSelect,
  onCollapse,
}: {
  members: TeamMember[];
  historyMessages?: Message[];
  activeTab: TabType;
  activeDetailTab: TeamDetailTab;
  selectedMemberId?: string;
  onTabChange: (tab: TabType) => void;
  onDetailTabChange: (tab: TeamDetailTab) => void;
  onMemberSelect?: (memberId: string) => void;
  onCollapse?: () => void;
}) {
  const { t } = useTranslation();
  const { completedTasks, teamTasks, totalTasks } = useTaskPlanningMetrics();

  const selectedMember = useMemo(() => {
    if (!externalSelectedMemberId) return null;
    return members.find((member) => member.member_id === externalSelectedMemberId) || null;
  }, [members, externalSelectedMemberId]);

  const handleSelectMember = (memberId: string) => {
    onMemberSelect?.(memberId);
  };

  const tabs = [
    {
      key: 'planning',
      label: t('team.planning.tab'),
      count: completedTasks + '/' + totalTasks,
      icon: <img src={teamProcessIcon} width={16} height={16} />,
    },
    {
      key: 'team',
      label: t('team.membersTab'),
      icon: <Users size={16} />,
    },
  ] as const;

  return (
    <div className="flex h-full flex-col overflow-hidden bg-card">
      <div className="flex shrink-0 items-center justify-between px-6 py-6 bg-card border-border">
        <div className="flex items-center gap-2">
          {tabs.map((tab) => (
            <button
              key={tab.key}
              className={`h-9 rounded-lg px-4 text-sm transition-colors flex items-center gap-2 ${
                activeTab === tab.key
                  ? 'bg-secondary font-medium text-text'
                  : 'text-text-muted hover:bg-secondary/50 hover:text-text'
              }`}
              onClick={() => onTabChange(tab.key as TabType)}
            >
              {tab.icon}
              {tab.label}{'count' in tab ? ' (' + tab.count + ')' : ''}
            </button>
          ))}
        </div>

        <button
          onClick={onCollapse}
          className="rounded p-2 text-text-muted transition-colors hover:bg-secondary hover:text-text"
          title={t('team.collapse')}
        >
          <Minimize2 size={16} />
        </button>
      </div>

      <div className="flex min-h-0 flex-1 overflow-hidden">
        {activeTab === 'planning' ? (
          <TaskPlanningPanel
            variant="expanded"
            tasks={teamTasks}
            members={members}
            totalTasks={totalTasks}
            completedTasks={completedTasks}
          />
        ) : (
          <TeamMembersPanel
            variant="expanded"
            members={members}
            selectedMemberId={selectedMember?.member_id || ''}
            selectedMember={selectedMember}
            activeDetailTab={activeDetailTab}
            historyMessages={historyMessages}
            onSelectMember={handleSelectMember}
            onDetailTabChange={onDetailTabChange}
          />
        )}
      </div>
    </div>
  );
}

export function TeamArea(props: TeamAreaProps) {
  const { members, historyMessages = [] } = props;

  if (props.expanded) {
    return (
      <ExpandedTeamArea
        members={members}
        historyMessages={historyMessages}
        activeTab={props.activeTab}
        activeDetailTab={props.activeDetailTab}
        selectedMemberId={props.selectedMemberId}
        onTabChange={props.onTabChange}
        onDetailTabChange={props.onDetailTabChange}
        onMemberSelect={props.onMemberSelect}
        onCollapse={props.onCollapse}
      />
    );
  }
  return <CompactTeamArea members={members} onExpand={props.onExpand} />;
}
