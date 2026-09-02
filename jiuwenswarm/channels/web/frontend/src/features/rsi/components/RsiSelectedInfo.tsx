/**
 * RSI 画布右侧：节点选中信息浮层（绝对定位覆盖画布右侧）。
 * 默认不显示，点击节点（store.selectedNodeId）后才出现；关闭按钮清空选中。
 * 数据源对齐接口契约：
 *   - 标题：nodeDisplayName（root→基线+场景名；adopted→快照+nodeId）
 *   - 优化对象 chips：harness 按 prompt/skill/tool/rail 四分组（改动取 change.summary，未改动取「其余继承」）；
 *     产物优化按 changes 摘要展示（§9.1 RsiNodeChange）
 *   - 描述信息：继承 {parent_id}，{node.summary}（§9.1）
 * 「查看详情」弹窗为中间产物预览占位，接口 ready 后填充（rsi.artifact.* 待定）。
 */
import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useRsiStore } from '../rsiStore';
import { nodeDisplayName } from '../rsiPresentation';

const HARNESS_GROUPS = ['prompt', 'skill', 'tool', 'rail'] as const;

interface RsiSelectedInfoProps {
  taskId: string;
}

export function RsiSelectedInfo({ taskId }: RsiSelectedInfoProps) {
  const { t } = useTranslation();
  const tree = useRsiStore((s) => s.detail[taskId]?.tree ?? null);
  const task = useRsiStore((s) => s.detail[taskId]?.task ?? null);
  const selectedNodeId = useRsiStore((s) => s.detail[taskId]?.selectedNodeId ?? null);
  const setSelectedNode = useRsiStore((s) => s.setSelectedNode);
  const [detailOpen, setDetailOpen] = useState(false);

  const selected = useMemo(() => {
    if (!tree || !selectedNodeId) return null;
    return tree.nodes.find((n) => n.node_id === selectedNodeId) ?? null;
  }, [tree, selectedNodeId]);

  // 无选中节点时不渲染浮层
  if (!selected || !task) {
    return null;
  }

  const title = nodeDisplayName(selected.type, selected.node_id, task.scenario, task.artifact_type);
  const parent = selected.parent_id ? tree?.nodes.find((n) => n.node_id === selected.parent_id) : null;

  // 优化对象 chips：harness 按 4 分组（改动/其余继承）；产物按 changes 摘要
  let chips: Array<{ text: string; inherited: boolean }>;
  if (task.scenario === 'harness') {
    chips = HARNESS_GROUPS.map((g) => {
      const change = selected.changes?.find((c) => c.group === g);
      return { text: change ? change.summary : t('rsi.detail.othersInherit'), inherited: !change };
    });
  } else {
    chips = (selected.changes ?? []).map((c) => ({ text: c.summary, inherited: false }));
    if (chips.length === 0) chips = [{ text: t('rsi.detail.othersInherit'), inherited: true }];
  }

  // 描述信息：继承 {parent}，{summary}
  const desc = [parent ? t('rsi.detail.inherit') + ' ' + parent.node_id : null, selected.summary]
    .filter(Boolean)
    .join('，');

  return (
    <>
      <div className="rsi-selected-info" data-testid="rsi-selected-info">
        <div className="rsi-selected-info__header">
          <span className="rsi-selected-info__title">{title}</span>
          <button
            type="button"
            className="rsi-selected-info__close"
            onClick={() => setSelectedNode(null)}
            aria-label="close"
          >
            ×
          </button>
        </div>
        <div className="rsi-selected-info__body">
          <div>
            <div className="rsi-selected-info__section-label">
              {t('rsi.detail.optimizationObject', { defaultValue: '优化对象' })}
            </div>
            <div className="rsi-selected-info__chips">
              {chips.map((c, i) => (
                <span
                  key={i}
                  className={'rsi-selected-info__chip' + (c.inherited ? ' rsi-selected-info__chip--inherited' : '')}
                >
                  {c.text}
                </span>
              ))}
            </div>
          </div>
          <div>
            <div className="rsi-selected-info__section-label">
              {t('rsi.detail.descInfo', { defaultValue: '描述信息' })}
            </div>
            <div className="rsi-selected-info__desc">{desc || '—'}</div>
          </div>
        </div>
        <div className="rsi-selected-info__footer">
          <button type="button" className="rsi-selected-info__detail-btn" onClick={() => setDetailOpen(true)}>
            {t('rsi.detail.viewDetail', { defaultValue: '查看详情' })}
          </button>
        </div>
      </div>
      {detailOpen && (
        <div className="rsi-selected-info__detail-overlay" onClick={() => setDetailOpen(false)}>
          <div className="rsi-selected-info__detail-panel" onClick={(e) => e.stopPropagation()}>
            <button
              type="button"
              className="rsi-selected-info__detail-close"
              onClick={() => setDetailOpen(false)}
              aria-label="close"
            >
              ×
            </button>
            <div className="rsi-selected-info__detail-title">
              {t('rsi.detail.viewDetail', { defaultValue: '查看详情' })}
            </div>
            <div className="rsi-selected-info__detail-placeholder">
              {t('rsi.detail.artifactPreviewPlaceholder', {
                defaultValue: '中间产物预览（接口 ready 后填充）',
              })}
            </div>
          </div>
        </div>
      )}
    </>
  );
}
