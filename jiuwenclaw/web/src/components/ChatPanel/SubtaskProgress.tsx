/**
 * SubtaskProgress 组件
 *
 * 显示并行子任务的执行进度
 */

import { useChatStore } from '../../stores';
import clsx from 'clsx';

export function SubtaskProgress() {
  const { activeSubtasks } = useChatStore();

  // 将 Map 转换为数组并按 index 排序
  const subtasks = Array.from(activeSubtasks.values()).sort(
    (a, b) => a.index - b.index
  );

  if (subtasks.length === 0) {
    return null;
  }

  // 获取总数（从第一个子任务）
  const total = subtasks[0]?.total || subtasks.length;

  // 计算完成数量
  const completedCount = total - subtasks.length;

  return (
    <div className="mx-4 my-2 p-3 bg-accent-subtle rounded-lg border border-border">
      <div className="flex items-center gap-2 mb-2">
        <span className="w-2 h-2 rounded-full bg-info animate-pulse" />
        <span className="text-sm font-medium text-text-strong">
          并行任务执行中 ({completedCount}/{total} 完成)
        </span>
      </div>
      <div className="space-y-2">
        {subtasks.map((subtask) => (
          <SubtaskItem key={subtask.task_id} subtask={subtask} />
        ))}
      </div>
    </div>
  );
}

interface SubtaskItemProps {
  subtask: {
    task_id: string;
    description: string;
    status: string;
    index: number;
    total: number;
    tool_name?: string;
    tool_count: number;
    message?: string;
    is_parallel: boolean;
  };
}

function SubtaskItem({ subtask }: SubtaskItemProps) {
  const getStatusIcon = () => {
    switch (subtask.status) {
      case 'starting':
        return (
          <span className="w-3 h-3 rounded-full border-2 border-info animate-pulse flex-shrink-0" />
        );
      case 'tool_call':
        return (
          <span className="w-3 h-3 rounded-full bg-warning animate-pulse flex-shrink-0" />
        );
      case 'tool_result':
        return (
          <span className="w-3 h-3 rounded-full bg-info flex-shrink-0" />
        );
      default:
        return (
          <span className="w-3 h-3 rounded-full border border-border-strong flex-shrink-0" />
        );
    }
  };

  const getStatusText = () => {
    switch (subtask.status) {
      case 'starting':
        return '启动中...';
      case 'tool_call':
        return `调用 ${subtask.tool_name || '工具'} (#${subtask.tool_count})`;
      case 'tool_result':
        return subtask.message ? `${subtask.message.slice(0, 50)}...` : '处理结果...';
      default:
        return subtask.status;
    }
  };

  return (
    <div
      className={clsx(
        'flex items-start gap-2 py-1.5 px-2 rounded text-sm',
        subtask.status === 'tool_call' && 'bg-warning/10',
        subtask.status === 'tool_result' && 'bg-info/10'
      )}
    >
      {getStatusIcon()}
      <div className="flex-1 min-w-0">
        <div className="font-medium text-text-strong truncate">
          任务 {subtask.index + 1}: {subtask.description}
        </div>
        <div className="text-text-muted truncate">{getStatusText()}</div>
      </div>
      {subtask.tool_count > 0 && (
        <span className="text-text-muted text-xs px-1.5 py-0.5 bg-secondary rounded">
          {subtask.tool_count} 次调用
        </span>
      )}
    </div>
  );
}
