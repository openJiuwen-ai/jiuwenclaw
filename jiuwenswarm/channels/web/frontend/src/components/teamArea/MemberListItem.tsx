import { useTranslation } from 'react-i18next';
import { TeamMemberAvatar } from '../TeamMemberAvatar';
import {
  getMemberDisplayName,
  getMemberStatusDotClass,
  getMemberStatusKey,
  getMemberStatusLabel,
  type TeamMember,
} from './shared';

interface TaskProgress {
  completed: number;
  total: number;
}

export function MemberListItem({
  member,
  selected,
  compact,
  onClick,
  taskProgress,
}: {
  member: TeamMember;
  selected?: boolean;
  compact?: boolean;
  onClick?: () => void;
  taskProgress?: TaskProgress;
}) {
  const { t } = useTranslation();
  const displayName = getMemberDisplayName(member);
  const statusLabel = getMemberStatusLabel(member);
  const statusKey = getMemberStatusKey(member);

  const progressPercent = taskProgress && taskProgress.total > 0
    ? Math.round((taskProgress.completed / taskProgress.total) * 100)
    : 0;
  const radius = 14;
  const strokeWidth = 2;
  const circumference = 2 * Math.PI * radius;
  const strokeDashoffset = circumference - (progressPercent / 100) * circumference;

  return (
    <button
      type="button"
      onClick={onClick}
      className={`flex w-full items-center gap-3 rounded-lg text-left transition-colors ${
        compact ? 'p-2' : 'p-3'
      } ${
        selected
          ? 'border border-accent bg-accent-subtle'
          : 'border border-transparent hover:bg-secondary'
      }`}
    >
      <div className="relative shrink-0">
        <TeamMemberAvatar
          member={member.member_id}
          alt={displayName}
          className={`${compact ? 'h-8 w-8' : 'h-10 w-10'} rounded-full`}
          imageClassName="rounded-full"
        />
        <span className={`absolute -bottom-0.5 -right-0.5 h-3 w-3 rounded-full border-2 border-card ${getMemberStatusDotClass(member)}`} />
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className={`${compact ? 'text-xs' : 'text-sm'} truncate font-medium text-text`}>
            {displayName}
          </span>
        </div>
        {!compact && member.mode && (
          <div className="mt-0.5 truncate text-xs text-text-muted">
            {t('team.runningMode', { mode: member.mode })}
          </div>
        )}
      </div>
      {taskProgress && taskProgress.total > 0 ? (
        <div className="shrink-0 relative">
          <svg width="32" height="32" className="shrink-0">
            <circle
              cx="16"
              cy="16"
              r={radius}
              fill="none"
              stroke="var(--border)"
              strokeWidth={strokeWidth}
            />
            <circle
              cx="16"
              cy="16"
              r={radius}
              fill="none"
              stroke="var(--accent)"
              strokeWidth={strokeWidth}
              strokeLinecap="round"
              strokeDasharray={circumference}
              strokeDashoffset={strokeDashoffset}
              transform="rotate(-90 16 16)"
            />
          </svg>
          <span className="absolute inset-0 flex items-center justify-center text-[10px] font-medium text-text">
            {taskProgress.completed}/{taskProgress.total}
          </span>
        </div>
      ) : (
        <span className={`shrink-0 rounded px-1.5 py-0.5 text-[10px] ${
          statusKey === 'running'
            ? 'bg-accent-subtle text-accent'
            : 'bg-secondary text-muted'
        }`}>
          {statusLabel}
        </span>
      )}
    </button>
  );
}
