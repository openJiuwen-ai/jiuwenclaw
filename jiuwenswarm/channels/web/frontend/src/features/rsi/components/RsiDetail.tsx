/**
 * RSI 右侧实验详情：Header（名称/Tag/操作按钮）+ 主展示框（左栏状态数据 + 右栏画布）。
 * 详情数据来自 rsiStore，按 selectedTaskId 取。组件挂载时自动拉取详情。
 */
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useRsiStore } from '../rsiStore';
import { RsiDetailHeader } from './RsiDetailHeader';
import { RsiResultSummary } from './RsiResultSummary';
import { RsiCanvasArea } from './RsiCanvasArea';
import { ConfigInfoDialog } from './ConfigInfoDialog';

export function RsiDetail() {
  const { t } = useTranslation();
  const selectedTaskId = useRsiStore((s) => s.selectedTaskId);
  const detail = useRsiStore((s) => (s.selectedTaskId ? s.detail[s.selectedTaskId] : undefined));
  const detailLoading = useRsiStore((s) => s.detailLoading);
  const refreshDetail = useRsiStore((s) => s.refreshDetail);
  const list = useRsiStore((s) => s.list);

  const [configOpen, setConfigOpen] = useState(false);

  useEffect(() => {
    if (selectedTaskId) void refreshDetail(selectedTaskId);
  }, [selectedTaskId, refreshDetail]);

  useEffect(() => {
    if (!selectedTaskId) return;
    const status = detail?.task?.status;
    if (status !== 'CREATED' && status !== 'QUEUED' && status !== 'RUNNING') return;
    const timer = window.setInterval(() => {
      void refreshDetail(selectedTaskId);
    }, 800);
    return () => window.clearInterval(timer);
  }, [selectedTaskId, detail?.task?.status, refreshDetail]);

  if ((detailLoading && !detail) || !detail?.task) {
    return <div className="rsi-loading">{t('rsi.list.loading', { defaultValue: '加载中…' })}</div>;
  }

  const liveCost = detail.liveProgress?.usageCost ?? null;
  const createdAt = list.find((item) => item.task_id === selectedTaskId)?.created_at ?? null;

  return (
    <>
      <RsiDetailHeader
        task={detail.task}
        report={detail.report}
        liveCost={liveCost}
        createdAt={createdAt}
        onOpenConfig={() => setConfigOpen(true)}
      />
      <div className="rsi-stage">
        <RsiResultSummary task={detail.task} report={detail.report} usage={detail.usage} />
        <RsiCanvasArea task={detail.task} tree={detail.tree} />
      </div>
      <ConfigInfoDialog open={configOpen} task={detail.task} onClose={() => setConfigOpen(false)} />
    </>
  );
}
