import { useMemo, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { CircleCheck, File, Puzzle, XCircle } from 'lucide-react';
import { TeamMemberAvatar } from '../TeamMemberAvatar';
import type { TeamTask as SessionTeamTask } from '../../stores/sessionStore';
import teamProcessIcon from '../../assets/team-process.svg';
import {
  BOARD_COLUMNS,
  ExpandIcon,
  getBoardTaskContent,
  getBoardTaskTitle,
  getMemberDisplayName,
  getTaskColumnKey,
  type TaskColumnKey,
  type TeamMember,
} from './shared';

type TaskPlanningPanelProps = {
  variant: 'compact' | 'expanded';
  tasks: SessionTeamTask[];
  members: TeamMember[];
  totalTasks: number;
  completedTasks: number;
  onExpand?: () => void;
};

const STATUS_CARD_STYLE: Record<TaskColumnKey, { dotColor: string; hoverBg: string; activeRing: string; activeBg: string; textColor: string }> = {
  completed: {
    dotColor: 'bg-[#088c58]',
    hoverBg: 'hover:bg-[#d3f3e6]',
    activeRing: 'hover:ring-1 hover:ring-[#088c58]/30',
    activeBg: 'bg-[#d3f3e6]/40',
    textColor: 'text-[#088c58]',
  },
  running: {
    dotColor: 'bg-[#5e7ce0]',
    hoverBg: 'hover:bg-[#d1e6fa]',
    activeRing: 'hover:ring-1 hover:ring-[#5e7ce0]/30',
    activeBg: 'bg-[#d1e6fa]/40',
    textColor: 'text-[#5e7ce0]',
  },
  waiting: {
    dotColor: 'bg-[#777777]',
    hoverBg: 'hover:bg-[#e8e8e8]',
    activeRing: 'hover:ring-1 hover:ring-[#777777]/30',
    activeBg: 'bg-[#e8e8e8]/40',
    textColor: 'text-[#777777]',
  },
  cancelled: {
    dotColor: 'bg-[#c84646]',
    hoverBg: 'hover:bg-[#fde2e2]',
    activeRing: 'hover:ring-1 hover:ring-[#c84646]/30',
    activeBg: 'bg-[#fde2e2]/40',
    textColor: 'text-[#c84646]',
  },
};

export function TaskPlanningPanel({
  variant,
  tasks,
  members,
  totalTasks,
  completedTasks,
  onExpand,
}: TaskPlanningPanelProps) {
  const { t } = useTranslation();
  const groupedTasks = useMemo(() => {
    const groups: Record<TaskColumnKey, SessionTeamTask[]> = {
      waiting: [],
      running: [],
      completed: [],
      cancelled: [],
    };

    tasks.forEach((task) => {
      groups[getTaskColumnKey(task)].push(task);
    });

    return groups;
  }, [tasks]);

  const progressPercent = totalTasks > 0
    ? Math.round((completedTasks / totalTasks) * 100)
    : 0;

  if (variant === 'compact') {
    const allTasks = [...tasks].sort((a, b) => (a.timestamp || 0) - (b.timestamp || 0));

    const tabCounts = {
      completed: groupedTasks.completed.length,
      running: groupedTasks.running.length,
      waiting: groupedTasks.waiting.length,
      cancelled: groupedTasks.cancelled.length,
    };

    const tabLabels = {
      completed: t('team.planning.columns.completed'),
      running: t('team.planning.columns.running'),
      waiting: t('team.planning.columns.waiting'),
      cancelled: t('team.planning.columns.failed'),
    };

    return (
      <div className="mb-3 flex flex-[2] flex-col overflow-hidden rounded-lg border border-border bg-card min-h-0">
        <div className="flex w-full shrink-0 items-center justify-between bg-card px-4 py-3 border-b border-border">
          <div className="flex items-center gap-2">
            <img src={teamProcessIcon} width={16} height={16} />            <span className="text-sm font-medium text-text">{t('team.taskOverview')}</span>
          </div>
          <button
            onClick={onExpand}
            className="rounded p-2 text-text-muted transition-colors hover:bg-secondary hover:text-text"
            title={t('team.expand')}
          >
            <ExpandIcon />
          </button>
        </div>
        <div className="px-4 py-3 shrink-0">
          {allTasks.length > 0 && (
            <div className="mb-4">
              <div className="flex items-center justify-start mb-2">
                <div className="flex items-baseline gap-1">
                  <span className="text-lg font-semibold text-text-strong">{completedTasks}</span>
                  <span className="text-sm text-text-muted">/ {totalTasks}</span>
                </div>
              </div>
              <div className="h-2 bg-secondary rounded-full overflow-hidden">
                <div
                  className="h-full bg-accent rounded-full transition-all duration-300"
                  style={{ width: `${progressPercent}%` }}
                />
              </div>
            </div>
          )}
          <div className="flex justify-between gap-2">
            {(['completed', 'running', 'waiting', 'cancelled'] as const).map((key) => {
              const style = STATUS_CARD_STYLE[key];
              const columnTasks = groupedTasks[key];
              const hasTasks = columnTasks.length > 0;
              return (
                <StatusCardWithPopover
                  key={key}
                  count={tabCounts[key]}
                  label={tabLabels[key]}
                  tasks={columnTasks}
                  style={style}
                  hasTasks={hasTasks}
                  onExpand={onExpand}
                />
              );
            })}
          </div>
        </div>
        <div className="flex-1 overflow-y-auto px-4 pb-3">
          {allTasks.length === 0 ? (
            <div className="text-center py-8 text-sm text-text-muted">
              {t('team.noTasks')}
            </div>
          ) : (
            <div className="space-y-2">
              {allTasks.map((task, index) => {
                const assigneeExists = Boolean(task.assignee && members.some(member => member.member_id === task.assignee));
                const assigneeName = getMemberDisplayName(task.assignee || '');
                const title = getBoardTaskTitle(task);
                const columnKey = getTaskColumnKey(task);
                return (
                  <div key={task.task_id} className="flex items-center gap-3 px-3 py-2 rounded-md bg-secondary">
                    <span className="text-sm font-medium text-muted w-6">
                      {String(index + 1).padStart(2, '0')}
                    </span>
                    {assigneeExists ? (
                      <TeamMemberAvatar
                        member={task.assignee}
                        alt={assigneeName}
                        className="h-4 w-4 rounded-full shrink-0"
                        imageClassName="rounded-full"
                      />
                    ) : (
                      <UnassignedTeamAvatar className="h-4 w-4 rounded-full shrink-0" />
                    )}
                    <span className="flex-1 text-sm text-text truncate">{title}</span>
                    {columnKey === 'completed' && <CircleCheck className="w-4 h-4 text-ok shrink-0" />}
                    {columnKey === 'running' && (
                      <svg width="16" height="16" className="w-4 h-4 text-info animate-spin flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 2v4" />
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="m16.2 7.8 2.9-2.9" />
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M18 12h4" />
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="m16.2 16.2 2.9 2.9" />
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 18v4" />
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="m4.9 19.1 2.9-2.9" />
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M2 12h4" />
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="m4.9 4.9 2.9 2.9" />
                      </svg>
                    )}
                    {columnKey === 'waiting' && (
                      <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" className="lucide lucide-clock4-icon lucide-clock-4"><circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/></svg>
                    )}
                    {columnKey === 'cancelled' && <XCircle className="w-4 h-4 text-danger shrink-0" />}
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="flex-1 overflow-hidden bg-card">
      <div className="flex h-full flex-col px-6 pb-6">
        <div className="mb-5 flex items-center gap-4">
          <h2 className="text-sm font-medium text-text-strong">{t('team.planning.progressTitle')}</h2>
          <span className="text-sm font-medium text-text-strong">{progressPercent}%</span>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto rounded-lg bg-secondary p-6">
          <div
            className="grid min-w-[920px] gap-5"
            style={{ gridTemplateColumns: 'repeat(4, minmax(220px, 1fr))' }}
          >
            {BOARD_COLUMNS.map((column) => (
              <BoardColumn
                key={column.key}
                column={column}
                tasks={groupedTasks[column.key]}
                members={members}
              />
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

function BoardColumn({
  column,
  tasks,
  members,
}: {
  column: typeof BOARD_COLUMNS[number];
  tasks: SessionTeamTask[];
  members: TeamMember[];
}) {
  const { t } = useTranslation();

  return (
    <section className="min-w-0">
      <div className={`mb-3 inline-flex h-7 items-center rounded-full px-4 text-sm font-medium shadow-[0_1px_2px_rgba(25,25,25,0.04)] ${column.pillClassName}`}>
        <span className={`mr-2 h-1.5 w-1.5 rounded-full ${column.dotClassName}`} />
        {t(column.labelKey)} {tasks.length}
      </div>
      <div className="space-y-3">
        {tasks.map((task) => (
          <BoardTaskCard key={task.task_id} task={task} members={members} />
        ))}
      </div>
    </section>
  );
}

function BoardTaskCard({
  task,
  members,
}: {
  task: SessionTeamTask;
  members: TeamMember[];
}) {
  const assigneeExists = Boolean(task.assignee && members.some(member => member.member_id === task.assignee));
  const assigneeName = getMemberDisplayName(task.assignee || '');
  const title = getBoardTaskTitle(task);
  const content = getBoardTaskContent(task);

  return (
    <article className="rounded-2xl border border-border bg-[#fafafa] p-1 shadow-sm">
      <div className="rounded-2xl border border-border bg-white px-4 py-4">
        <h3 className="truncate text-base font-medium leading-[18px] text-text-strong" title={title}>
          {title}
        </h3>
        {content ? (
          <p className="mt-2 line-clamp-2 text-sm leading-5 text-text-muted" title={content}>
            {content}
          </p>
        ) : null}
        <TaskResourcePanel skills={task.skills} files={task.files} />
      </div>
      <div className="mt-3 flex h-8 items-center bg-[#fafafa] px-1 pb-1">
        {assigneeExists ? (
          <div title={assigneeName}>
            <TeamMemberAvatar
              member={task.assignee}
              alt={assigneeName}
              className="h-8 w-8 rounded-full"
              imageClassName="rounded-full"
            />
          </div>
        ) : (
          <UnassignedTeamAvatar className="h-8 w-8 rounded-full" />
        )}
      </div>
    </article>
  );
}

function UnassignedTeamAvatar({
  className,
}: {
  className?: string;
}) {
  const { t } = useTranslation();

  return (
    <div
      className={`flex shrink-0 items-center justify-center overflow-hidden border border-border bg-card text-[12px] font-medium text-muted ${className || ''}`}
      aria-label={t('team.planning.unassignedAvatar')}
      title={t('team.planning.unassigned')}
    >
      --
    </div>
  );
}

function TaskResourcePanel({
  skills,
  files,
}: {
  skills?: string[];
  files?: string[];
}) {
  const { t } = useTranslation();
  const skillCount = skills?.length ?? 0;
  const fileCount = files?.length ?? 0;
  const hasSkills = skillCount > 0;
  const hasFiles = fileCount > 0;
  const [activeTab, setActiveTab] = useState<'skills' | 'files'>('skills');

  if (!hasSkills && !hasFiles) {
    return null;
  }

  let resolvedActiveTab: 'skills' | 'files' = 'files';
  if (activeTab === 'files' && hasFiles) {
    resolvedActiveTab = 'files';
  } else if (hasSkills) {
    resolvedActiveTab = 'skills';
  }
  const activeItems = resolvedActiveTab === 'skills' ? skills : files;

  return (
    <div className="mt-4 rounded-lg bg-secondary px-3 py-3">
      <div className="flex h-6 items-center gap-4 border-b border-border" role="tablist" aria-label={t('team.planning.resources')}>
        {hasSkills && (
          <ResourceTab
            label={t('team.planning.skills')}
            count={skillCount}
            active={resolvedActiveTab === 'skills'}
            onClick={() => setActiveTab('skills')}
          />
        )}
        {hasFiles && (
          <ResourceTab
            label={t('team.planning.files')}
            count={fileCount}
            active={resolvedActiveTab === 'files'}
            onClick={() => setActiveTab('files')}
          />
        )}
      </div>
      <div className="min-h-[44px] pt-3">
        {activeItems?.map((item) => (
          <ResourceLine
            key={`${resolvedActiveTab}-${item}`}
            icon={resolvedActiveTab === 'skills' ? <Puzzle className="h-4 w-4 shrink-0 text-muted" aria-hidden="true" /> : <File className="h-4 w-4 shrink-0 text-muted" aria-hidden="true" />}
            label={item}
          />
        ))}
      </div>
    </div>
  );
}

function ResourceTab({
  label,
  count,
  active = false,
  onClick,
}: {
  label: string;
  count: number;
  active?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      className="relative flex h-6 items-start gap-1 text-xs focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
      onClick={onClick}
      role="tab"
      aria-selected={active}
    >
      <span className={active ? 'font-medium text-text-strong' : 'text-text'}>
        {label}
      </span>
      <span className="flex h-4 min-w-4 items-center justify-center rounded-full bg-secondary px-1 text-[10px] leading-4 text-text-strong">
        {count}
      </span>
      {active && <span className="absolute -bottom-px left-0 h-0.5 w-6 bg-text-strong" />}
    </button>
  );
}

function ResourceLine({
  icon,
  label,
}: {
  icon: ReactNode;
  label: string;
}) {
  return (
    <div className="mb-2 flex items-center gap-1 text-xs text-text last:mb-0">
      {icon}
      <span className="truncate">{label}</span>
    </div>
  );
}

function StatusCardWithPopover({
  count,
  label,
  tasks,
  style,
  hasTasks,
  onExpand,
}: {
  count: number;
  label: string;
  tasks: SessionTeamTask[];
  style: { dotColor: string; hoverBg: string; activeRing: string; activeBg: string; textColor: string };
  hasTasks: boolean;
  onExpand?: () => void;
}) {
  const { t } = useTranslation();
  const [isHovered, setIsHovered] = useState(false);
  const containerRef = useRef<HTMLButtonElement>(null);
  const popoverRef = useRef<HTMLDivElement>(null);

  const baseClasses = hasTasks
    ? `flex-1 flex flex-col items-center justify-center py-2 rounded-md bg-secondary cursor-pointer transition-all ${style.hoverBg} ${style.activeRing}`
    : `flex-1 flex flex-col items-center justify-center py-2 rounded-md bg-secondary opacity-60`;

  return (
    <div className="relative flex-1">
      <button
        ref={containerRef}
        type="button"
        className={baseClasses}
        onClick={onExpand}
        onMouseEnter={() => setIsHovered(true)}
        onMouseLeave={() => setIsHovered(false)}
        onFocus={() => setIsHovered(true)}
        onBlur={() => setIsHovered(false)}
        aria-label={`${label}: ${count} ${t('team.planning.tasks')}`}
      >
        <div className="flex items-center gap-1.5">
          <span className={`h-2 w-2 rounded-full ${style.dotColor}`} />
          <span className="text-lg font-bold text-text-strong">{count}</span>
        </div>
        <span className={`text-xs mt-1 ${hasTasks ? style.textColor : 'text-text-muted'}`}>{label}</span>
      </button>

      {hasTasks && isHovered && (
        <div
          ref={popoverRef}
          className="absolute left-1/2 z-50 mt-1 w-64 -translate-x-1/2 rounded-lg border border-border bg-card shadow-lg overflow-hidden cursor-pointer"
          style={{ top: '100%' }}
          onClick={onExpand}
        >
          <div className={`px-3 py-2 border-b border-border ${style.activeBg}`}>
            <div className="flex items-center justify-between">
              <span className={`text-xs font-medium ${style.textColor}`}>{label}</span>
              <span className="text-xs text-text-muted">
                {count} {t('team.planning.tasks')}
              </span>
            </div>
          </div>

          <div className="max-h-48 overflow-y-auto">
            {tasks.slice(0, 10).map((task) => {
              const assigneeName = getMemberDisplayName(task.assignee || '');
              const title = getBoardTaskTitle(task);
              return (
                <div
                  key={task.task_id}
                  className="flex items-center gap-2 px-3 py-2 border-b border-border last:border-b-0 hover:bg-secondary"
                >
                  <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${style.dotColor}`} />
                  <span className="flex-1 text-xs text-text truncate">{title}</span>
                  {task.assignee && (
                    <span className="text-[10px] text-text-muted shrink-0">{assigneeName}</span>
                  )}
                </div>
              );
            })}
            {tasks.length > 10 && (
              <div className="px-3 py-2 text-center text-[10px] text-text-muted border-t border-border">
                +{tasks.length - 10} {t('team.planning.more')}
              </div>
            )}
          </div>

          <div className="px-3 py-2 border-t border-border bg-secondary">
            <span className="text-[10px] text-text-muted">{t('team.planning.clickToExpand')}</span>
          </div>
        </div>
      )}
    </div>
  );
}
