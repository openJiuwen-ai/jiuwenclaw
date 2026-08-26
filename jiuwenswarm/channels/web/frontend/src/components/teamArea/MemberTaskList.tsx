import { useTranslation } from 'react-i18next';
import {
  Chevron,
  formatTime,
  getTaskStatusLabel,
  StatusIcon,
  type MemberTask,
  type TaskStatus,
} from './shared';

export type MemberTaskListItem = Pick<MemberTask, 'id' | 'title' | 'detail' | 'status' | 'raw'> & {
  statusHistory?: Array<{ status: string; atMs?: number; source?: string }>;
};

function formatRawValue(value: unknown): string {
  if (value === null || value === undefined || value === '') return '-';
  if (typeof value === 'string') return value;
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  try {
    return JSON.stringify(value);
  } catch {
    return String(value ?? '');
  }
}

function buildTaskRawEntries(task: MemberTaskListItem): Array<[string, string]> {
  const raw = task.raw || {
    task_id: task.id,
    title: task.title,
    detail: task.detail,
    status: task.status,
  };
  return Object.entries(raw).map(([key, value]) => [key, formatRawValue(value)]);
}

export function MemberTaskListBar({
  tasks,
  expanded,
  onToggle,
}: {
  tasks: MemberTaskListItem[];
  expanded: boolean;
  onToggle: () => void;
}) {
  const { t } = useTranslation();
  const completedCount = tasks.filter((task) => task.status === 'completed').length;

  return (
    <button
      type="button"
      onClick={onToggle}
      className="flex h-[54px] w-full items-center justify-between px-5 text-left hover:bg-secondary"
      aria-expanded={expanded}
    >
      <div className="flex min-w-0 items-center gap-2">
        <span className="text-sm font-medium text-text">{t('team.memberTasks')}</span>
        <span className="text-muted">|</span>
        <span className="shrink-0 text-sm text-text-muted">
          {expanded ? t('team.collapseView') : t('team.expandView')}
        </span>
      </div>
      <div className="ml-4 flex shrink-0 items-center gap-4">
        <span className="text-sm text-text-muted">{completedCount}/{tasks.length}</span>
        <span className="text-text-muted"><Chevron expanded={expanded} /></span>
      </div>
    </button>
  );
}

export function MemberTaskListItems({
  tasks,
  emptyLabel = 'team.noMemberTasks',
}: {
  tasks: MemberTaskListItem[];
  emptyLabel?: string;
}) {
  const { t } = useTranslation();

  if (tasks.length === 0) {
    return <div className="py-4 text-center text-sm text-text-muted">{t(emptyLabel)}</div>;
  }

  return (
    <div className="space-y-3">
      {tasks.map((task) => (
        <div key={task.id} className="flex items-start gap-3 rounded-md px-1 py-1.5">
          <StatusIcon status={task.status} />
          <div className="min-w-0 flex-1">
            <div className="truncate text-sm text-text">{task.title}</div>
            {task.detail ? <div className="mt-1 text-xs leading-5 text-text-muted">{task.detail}</div> : null}
            <div className="mt-1 text-[11px] text-muted">
              {getTaskStatusLabel(task.status)}
              {task.id ? ` · ${task.id}` : ''}
            </div>
            {task.statusHistory && task.statusHistory.length > 1 ? (
              <div className="mt-1 text-[11px] leading-5 text-muted">
                {t('team.process.fields.taskStatus')}：{task.statusHistory.map((change, index) => (
                  <span key={`${change.status}-${index}`}>
                    {index > 0 ? ' → ' : ''}
                    {getTaskStatusLabel(change.status as TaskStatus)}
                    {change.atMs ? ` ${formatTime(change.atMs)}` : ''}
                  </span>
                ))}
              </div>
            ) : null}
            <div className="mt-2 rounded bg-[var(--color-team-detail-surface)] px-3 py-2 text-[11px] leading-5 text-[var(--color-team-detail-text)]">
              {buildTaskRawEntries(task).map(([label, value]) => (
                <div key={label} className="grid grid-cols-[88px_minmax(0,1fr)] gap-2">
                  <span className="text-[var(--color-team-detail-label)]">{label}</span>
                  <span className="whitespace-pre-wrap break-words">{value}</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}
