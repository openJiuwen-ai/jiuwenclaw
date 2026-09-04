/**
 * RSI 画布右侧：节点选中信息浮层（绝对定位覆盖画布右侧）。
 * 默认不显示，点击节点（store.selectedNodeId）后才出现；关闭按钮清空选中。
 * 数据源对齐接口契约：
 *   - 标题：nodeDisplayName（root→基线+场景名；adopted→快照+nodeId）
 *   - 优化对象 chips：harness 按 prompt/skill/tool/rail 四分组（改动取变更摘要，未改动取「其余继承」）；
 *     产物优化按 changes 摘要展示（§9.1 RsiNodeChange）
 *   - 描述信息：继承 {parent_id}，{node.description}
 * 「查看详情」弹窗通过后端产物目录接口读取节点产物文件。
 */
import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import selectedInfoIcon from '../../../assets/rsi/rsi-icon.svg';
import { useRsiStore } from '../rsiStore';
import { nodeChangeGroup, nodeChangeSummary, nodeDisplayName } from '../rsiPresentation';
import { resolveRsiArtifactSource } from '../rsiArtifactFiles';
import {
  rsiArtifactDownload,
  rsiArtifactDownloadUrl,
} from '../rsiApi';
import { executeDesktopSave, type DesktopSaveApiResult } from '../../../utils/desktopSave';
import { RsiArtifactDetailDialog } from './RsiArtifactDetailDialog';

const HARNESS_GROUPS = ['prompt', 'skill', 'tool', 'rail'] as const;

type DownloadCapableWindow = Window & {
  pywebview?: {
    api?: {
      download_file?: (url: string, filename: string) => DesktopSaveApiResult;
    };
  };
};

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
  const [downloadBusy, setDownloadBusy] = useState(false);
  const [downloadError, setDownloadError] = useState<string | null>(null);

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
  const artifactSource = resolveRsiArtifactSource(selected, taskId);
  const canViewArtifact = artifactSource !== null;

  // 优化对象 chips：harness 按 4 分组（改动/其余继承）；产物按 changes 摘要。
  // 基线节点或无变更节点没有优化对象，不渲染占位 tag。
  let chips: Array<{ text: string; inherited: boolean }>;
  const changes = selected.changes ?? [];
  if (selected.type === 'ROOT' || changes.length === 0) {
    chips = [];
  } else if (task.scenario === 'HARNESS') {
    chips = HARNESS_GROUPS.map((g) => {
      const change = selected.changes?.find((c) => nodeChangeGroup(c) === g);
      return { text: change ? nodeChangeSummary(change) : t('rsi.detail.othersInherit'), inherited: !change };
    });
  } else {
    chips = changes.map((c) => ({ text: nodeChangeSummary(c), inherited: false }));
  }

  // 描述信息：继承 {parent}，{summary}
  const desc = [parent ? t('rsi.detail.inherit') + ' ' + parent.node_id : null, selected.description]
    .filter(Boolean)
    .join('，');

  const downloadNodeArtifact = async () => {
    const artifactId = selected.snapshot_artifact_id;
    if (!artifactId) return;
    setDownloadBusy(true);
    setDownloadError(null);
    try {
      const artifact = await rsiArtifactDownload(taskId, artifactId);
      const downloadUrl = rsiArtifactDownloadUrl(artifact);
      if (artifact.is_directory || !downloadUrl) {
        // Directory artifacts are browsed through the folder dialog. The
        // dialog downloads each selected file without repackaging the folder.
        if (canViewArtifact) {
          setDetailOpen(true);
          return;
        }
        throw new Error('RSI 产物目录不可用');
      }
      const pywebviewApi = (window as DownloadCapableWindow).pywebview?.api;
      if (pywebviewApi?.download_file) {
        const outcome = await executeDesktopSave(() => pywebviewApi.download_file!(downloadUrl, artifact.filename));
        if (outcome === 'failed') throw new Error('桌面端保存失败');
      } else {
        const link = document.createElement('a');
        link.href = downloadUrl;
        link.download = artifact.filename;
        link.target = '_blank';
        link.rel = 'noopener noreferrer';
        document.body.appendChild(link);
        link.click();
        link.remove();
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      setDownloadError(message);
    } finally {
      setDownloadBusy(false);
    }
  };

  return (
    <>
      <div className="rsi-selected-info" data-testid="rsi-selected-info">
        <div className="rsi-selected-info__header">
          <img className="rsi-selected-info__icon" src={selectedInfoIcon} alt="" aria-hidden />
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
          {chips.length > 0 && (
            <div>
              <div className="rsi-selected-info__section-label">
                {t('rsi.detail.optimizationObject', { defaultValue: '优化对象' })}
              </div>
              <div className="rsi-selected-info__chips">
                {chips.map((c, i) => (
                  <span
                    key={i}
                    className="rsi-selected-info__chip"
                  >
                    {c.text}
                  </span>
                ))}
              </div>
            </div>
          )}
          <div>
            <div className="rsi-selected-info__section-label">
              {t('rsi.detail.descInfo', { defaultValue: '描述信息' })}
            </div>
            <div className="rsi-selected-info__desc">{desc || '—'}</div>
          </div>
          <div className="rsi-selected-info__meta-grid">
            <div><span>迭代</span><strong>{selected.iteration}</strong></div>
            <div><span>类型</span><strong>{selected.type}</strong></div>
            <div><span>分数</span><strong>{selected.score == null ? '—' : selected.score}</strong></div>
            <div><span>采纳</span><strong>{selected.adopted ? '是' : '否'}</strong></div>
          </div>
          {selected.snapshot_artifact_id && (
            <div className="rsi-selected-info__artifact">
              <div className="rsi-selected-info__section-label">节点产物</div>
              <div className="rsi-selected-info__artifact-row">
                <code>{selected.snapshot_artifact_id}</code>
                <button
                  type="button"
                  className="rsi-selected-info__download-btn"
                  onClick={() => void downloadNodeArtifact()}
                  disabled={downloadBusy}
                >
                  {downloadBusy ? '处理中…' : canViewArtifact ? '打开产物' : '下载'}
                </button>
              </div>
              {downloadError && <div className="rsi-selected-info__download-error">{downloadError}</div>}
            </div>
          )}
        </div>
        <div className="rsi-selected-info__footer">
          {canViewArtifact && (
            <button type="button" className="rsi-selected-info__detail-btn" onClick={() => setDetailOpen(true)}>
              {t('rsi.detail.viewDetail', { defaultValue: '查看详情' })}
            </button>
          )}
        </div>
      </div>
      {detailOpen && (
        <RsiArtifactDetailDialog source={artifactSource} title={title} onClose={() => setDetailOpen(false)} />
      )}
    </>
  );
}
