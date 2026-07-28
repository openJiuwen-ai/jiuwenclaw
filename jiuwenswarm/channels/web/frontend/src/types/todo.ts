/**
 * Todo 类型定义
 */

export type TodoStatus = 'pending' | 'in_progress' | 'completed' | 'cancelled';

export interface TodoItem {
  id: string;
  content: string;
  activeForm: string;
  status: TodoStatus;
  createdAt: string;
  updatedAt: string;
  depends?: string[];
  claimedBy?: string;
}

const TODO_STATUSES: ReadonlySet<string> = new Set([
  'pending',
  'in_progress',
  'completed',
  'cancelled',
]);

/** Normalize backend/client spelling (canceled → cancelled). */
export function normalizeTodoStatus(status: unknown): TodoStatus {
  if (status === 'canceled') return 'cancelled';
  if (typeof status === 'string' && TODO_STATUSES.has(status)) {
    return status as TodoStatus;
  }
  return 'pending';
}

/** Terminal statuses must not be downgraded by a stale todo.updated snapshot. */
export function isTodoStatusRegression(
  previous: TodoStatus,
  incoming: TodoStatus,
): boolean {
  if (previous === 'completed' || previous === 'cancelled') {
    return incoming === 'pending' || incoming === 'in_progress';
  }
  return false;
}
