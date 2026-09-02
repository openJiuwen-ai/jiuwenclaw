/**
 * RSI 实验页面主组件（左：实验管理栏 + 右：实验详情）。
 * 整个 RSI 特性在此页面内完成，模块集中，尽量不侵入公共组件。
 */
import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useRsiStore } from './rsiStore';
import { useRsiEvents } from './useRsiEvents';
import { RsiRail } from './components/RsiRail';
import { RsiDetail } from './components/RsiDetail';
import { CreateExperimentDialog } from './components/CreateExperimentDialog';
import { RsiEmptyState } from './components/RsiEmptyState';
import type { RsiTaskListItem } from './types';
import './styles/rsi.css';

export function RsiPage() {
  const { t } = useTranslation();
  const list = useRsiStore((s) => s.list);
  const listLoading = useRsiStore((s) => s.listLoading);
  const listError = useRsiStore((s) => s.listError);
  const selectedTaskId = useRsiStore((s) => s.selectedTaskId);
  const loadList = useRsiStore((s) => s.loadList);
  const selectTask = useRsiStore((s) => s.selectTask);
  const upsertListItem = useRsiStore((s) => s.upsertListItem);

  const [createOpen, setCreateOpen] = useState(false);

  // 订阅推送事件（仅 RSI 页面挂载期间）
  useRsiEvents(true);

  // 进入页面拉取实验列表
  useEffect(() => {
    void loadList();
  }, [loadList]);

  // 列表加载后若未选中，自动选中第一个任务（暂不展示空状态页，直接进详情）
  useEffect(() => {
    if (!listLoading && !selectedTaskId && list.length > 0) {
      void selectTask(list[0].task_id);
    }
  }, [listLoading, list, selectedTaskId, selectTask]);

  const handleSelect = useCallback(
    (taskId: string) => {
      void selectTask(taskId);
    },
    [selectTask],
  );

  const handleCreated = useCallback(
    (item: RsiTaskListItem) => {
      upsertListItem(item);
      void selectTask(item.task_id);
    },
    [upsertListItem, selectTask],
  );

  const hasSelection = Boolean(selectedTaskId);

  return (
    <div className="rsi-page" data-testid="rsi-page">
      <RsiRail
        tasks={list}
        loading={listLoading}
        error={listError}
        selectedTaskId={selectedTaskId}
        onSelect={handleSelect}
        onCreate={() => setCreateOpen(true)}
        onRetry={loadList}
      />

      <div className="rsi-detail" data-testid="rsi-detail">
        {hasSelection ? (
          <RsiDetail />
        ) : (
          <RsiEmptyState onCreate={() => setCreateOpen(true)} text={t('rsi.empty.title')} hint={t('rsi.empty.hint')} />
        )}
      </div>

      <CreateExperimentDialog open={createOpen} onClose={() => setCreateOpen(false)} onCreated={handleCreated} />

      {/* 列表错误兜底提示（不阻断页面） */}
      {listError && (
        <div className="rsi-error" role="alert" style={{ position: 'absolute', bottom: 12, left: 384, right: 24 }}>
          {t('rsi.list.error')}: {listError}
        </div>
      )}
    </div>
  );
}
