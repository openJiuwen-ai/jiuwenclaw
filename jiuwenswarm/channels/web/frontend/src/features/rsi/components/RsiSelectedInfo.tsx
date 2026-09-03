/**
 * RSI 画布右侧：节点选中信息浮层（绝对定位覆盖画布右侧）。
 * 默认不显示，点击节点（store.selectedNodeId）后才出现；关闭按钮清空选中。
 * 数据源对齐接口契约：
 *   - 标题：nodeDisplayName（root→基线+场景名；adopted→快照+nodeId）
 *   - 优化对象 chips：harness 按 prompt/skill/tool/rail 四分组（改动取 change.summary，未改动取「其余继承」）；
 *     产物优化按 changes 摘要展示（§9.1 RsiNodeChange）
 *   - 描述信息：继承 {parent_id}，{node.summary}（§9.1）
 * 「查看详情」弹窗展示 Provider 返回的完整节点 JSON；节点快照可直接下载。
 */
import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { executeDesktopSave, type DesktopSaveApiResult } from '../../../utils/desktopSave';
import { useRsiStore } from '../rsiStore';
import { nodeDisplayName } from '../rsiPresentation';
import { rsiArtifactDownload, rsiArtifactDownloadUrl } from '../rsiApi';

const HARNESS_GROUPS = ['prompt', 'skill', 'tool', 'rail'] as const;

type DownloadCapableWindow = Window & {
  pywebview?: {
    api?: {
      download_file?: (url: string, filename: string) => DesktopSaveApiResult;
    };
  };
};

function formatJson(value: unknown): string {
  try {
    return JSON.stringify(value, null, 2) ?? String(value);
  } catch {
    return String(value);
  }
}

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

  const downloadNodeArtifact = async () => {
    const artifactId = selected.snapshot_artifact_id;
    if (!artifactId) return;
    setDownloadBusy(true);
    setDownloadError(null);
    try {
      const artifact = await rsiArtifactDownload(taskId, artifactId);
      const downloadUrl = rsiArtifactDownloadUrl(artifact);
      if (!downloadUrl) throw new Error('RSI 产物下载链接不可用');
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

  const content = selected.extra?.content ?? selected.extra ?? {};
  const changeText = (value: unknown, fallback = '—') => {
    const text = String(value ?? '').trim();
    return text || fallback;
  };

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
                  {downloadBusy ? '下载中…' : '下载'}
                </button>
              </div>
              {downloadError && <div className="rsi-selected-info__download-error">{downloadError}</div>}
            </div>
          )}
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
              {title}
            </div>
            <div className="rsi-selected-info__detail-subtitle">
              {t('rsi.detail.viewDetail', { defaultValue: '查看详情' })} · {selected.node_id}
            </div>
            <div className="rsi-selected-info__detail-scroll">
              <div className="rsi-selected-info__meta-grid rsi-selected-info__meta-grid--detail">
                <div><span>迭代</span><strong>{selected.iteration}</strong></div>
                <div><span>父节点</span><strong>{changeText(selected.parent_id)}</strong></div>
                <div><span>状态</span><strong>{selected.type}</strong></div>
                <div><span>采纳</span><strong>{selected.adopted ? '是' : '否'}</strong></div>
                <div><span>分数</span><strong>{selected.score == null ? '—' : selected.score}</strong></div>
                <div><span>失败类别</span><strong>{changeText(selected.failure_class)}</strong></div>
              </div>
              <div className="rsi-selected-info__detail-block">
                <div className="rsi-selected-info__section-label">节点内容</div>
                <div className="rsi-selected-info__desc">{selected.summary || selected.reason || '—'}</div>
                <pre className="rsi-selected-info__json rsi-selected-info__json--content">{formatJson(content)}</pre>
              </div>
              {selected.changes && selected.changes.length > 0 && (
                <div className="rsi-selected-info__detail-block">
                  <div className="rsi-selected-info__section-label">变更记录</div>
                  <div className="rsi-selected-info__changes">
                    {selected.changes.map((change, index) => (
                      <div className="rsi-selected-info__change" key={`${change.group}-${index}`}>
                        <div className="rsi-selected-info__change-head">
                          <strong>{changeText(change.group)}</strong>
                          <span>{changeText(change.operation)}</span>
                        </div>
                        <div>{changeText(change.summary)}</div>
                        <small>
                          {changeText(change.function)} → {changeText(change.target)}
                        </small>
                      </div>
                    ))}
                  </div>
                </div>
              )}
              {selected.snapshot_artifact_id && (
                <div className="rsi-selected-info__detail-block">
                  <div className="rsi-selected-info__section-label">节点产物</div>
                  <div className="rsi-selected-info__artifact-row">
                    <code>{selected.snapshot_artifact_id}</code>
                    <button
                      type="button"
                      className="rsi-selected-info__download-btn"
                      onClick={() => void downloadNodeArtifact()}
                      disabled={downloadBusy}
                    >
                      {downloadBusy ? '下载中…' : '下载产物'}
                    </button>
                  </div>
                </div>
              )}
              <div className="rsi-selected-info__detail-block">
                <div className="rsi-selected-info__section-label">完整返回数据</div>
                <pre className="rsi-selected-info__json">{formatJson(selected)}</pre>
              </div>
              {downloadError && <div className="rsi-selected-info__download-error">{downloadError}</div>}
            </div>
          </div>
        </div>
      )}
    </>
  );
}
