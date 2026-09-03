/**
 * RSI 详情 Header：实验名称 + Tag 信息区 + 右侧操作按钮（状态切换）。
 */
import { useCallback, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { executeDesktopSave, type DesktopSaveApiResult } from '../../../utils/desktopSave';
import type { RsiTaskGetResult, RsiReportGetResult } from '../types';
import {
  actionsForStatus,
  typeDisplayLabel,
  statusBadgeInfo,
  formatDateTime,
  type StatusBadgeKind,
  formatCost,
  type RsiActionKind,
} from '../rsiPresentation';
import { useRsiStore } from '../rsiStore';
import {
  rsiTrainingPause,
  rsiTrainingResume,
  rsiTrainingStart,
  rsiTrainingTerminate,
  rsiArtifactDownload,
  rsiArtifactDownloadUrl,
} from '../rsiApi';

type DownloadCapableWindow = Window & {
  pywebview?: {
    api?: {
      download_file?: (url: string, filename: string) => DesktopSaveApiResult;
    };
  };
};

interface RsiDetailHeaderProps {
  task: RsiTaskGetResult;
  report: RsiReportGetResult | null;
  liveCost: number | null;
  createdAt: string | null;
  onOpenConfig: () => void;
}

export function RsiDetailHeader({ task, report, liveCost, createdAt, onOpenConfig }: RsiDetailHeaderProps) {
  const { t } = useTranslation();
  const patchTaskStatus = useRsiStore((s) => s.patchTaskStatus);
  const [busy, setBusy] = useState(false);

  const handleAction = useCallback(
    async (action: RsiActionKind) => {
      if (action === 'config') {
        onOpenConfig();
        return;
      }
      setBusy(true);
      try {
        if (action === 'start') {
          const res = await rsiTrainingStart(task.task_id);
          patchTaskStatus(task.task_id, res.status);
        } else if (action === 'pause') {
          const res = await rsiTrainingPause(task.task_id);
          patchTaskStatus(task.task_id, res.status);
        } else if (action === 'resume') {
          const res = await rsiTrainingResume(task.task_id);
          patchTaskStatus(task.task_id, res.status);
        } else if (action === 'terminate') {
          const res = await rsiTrainingTerminate(task.task_id);
          patchTaskStatus(task.task_id, res.status);
        } else if (action === 'download') {
          const artifactId = report?.metrics.best_artifact_id ?? undefined;
          const artifact = await rsiArtifactDownload(task.task_id, artifactId);
          const downloadUrl = rsiArtifactDownloadUrl(artifact);
          if (!downloadUrl) throw new Error('RSI 产物下载链接不可用');
          const pywebviewApi = (window as DownloadCapableWindow).pywebview?.api;
          if (pywebviewApi?.download_file) {
            const outcome = await executeDesktopSave(() => pywebviewApi.download_file!(downloadUrl, artifact.filename));
            if (outcome === 'failed') window.alert(t('artifacts.downloadFailed', { name: artifact.filename }));
          } else {
            window.open(downloadUrl, '_blank', 'noopener,noreferrer');
          }
        } else if (action === 'install') {
          // 复用 harness.packages.*（§12）：安装插件由既有插件面板承接，
          // 这里触发跳转到插件管理，避免在 RSI 内重复实现安装流程。
          window.dispatchEvent(new CustomEvent('jiuwen:nav', { detail: 'connectorMarket' }));
        }
      } catch (e) {
        console.error('[rsi] action failed', action, e);
      } finally {
        setBusy(false);
      }
    },
    [task.task_id, report, patchTaskStatus, onOpenConfig, t],
  );

  const actions = actionsForStatus(task.status, task.scenario);

  const actionLabel: Record<RsiActionKind, string> = {
    config: t('rsi.detail.actionConfig'),
    start: t('rsi.detail.actionStart'),
    pause: t('rsi.detail.actionPause'),
    resume: t('rsi.detail.actionResume'),
    terminate: t('rsi.detail.actionTerminate'),
    install: t('rsi.detail.actionInstall'),
    download: t('rsi.detail.actionDownload'),
  };

  // 类型标签 + 状态徽章 + 数值标签
  const typeLabel = typeDisplayLabel(task.scenario, task.artifact_type);
  const badge = statusBadgeInfo(task.status);
  const maxIter = t('rsi.detail.tagMaxIterations') + '：' + task.config.max_iterations;
  const maxWidth = t('rsi.detail.tagMaxSearchWidth') + '：' + task.config.search_width;
  const createdLabel = t('rsi.detail.tagCreatedAt', { defaultValue: '创建时间' }) + '：' + formatDateTime(createdAt);

  return (
    <div className="rsi-detail__header">
      <div className="rsi-detail__title-block">
        <div className="rsi-detail__name">{task.name}</div>
        <div className="rsi-detail__tags">
          {badge.kind ? (
            <span className="rsi-detail__tag rsi-detail__tag--status">
              <StatusIcon kind={badge.kind} />
              {badge.label}
            </span>
          ) : (
            <span className="rsi-detail__tag rsi-detail__tag--status">{badge.label}</span>
          )}
          <span className="rsi-detail__divider" />
          <span className="rsi-detail__tag rsi-detail__tag--meta">
            {t('rsi.detail.tagType')}：{typeLabel}
          </span>
          <span className="rsi-detail__divider" />
          <span className="rsi-detail__tag rsi-detail__tag--meta">{maxIter}</span>
          <span className="rsi-detail__divider" />
          <span className="rsi-detail__tag rsi-detail__tag--meta">{maxWidth}</span>
          <span className="rsi-detail__divider" />
          <span className="rsi-detail__tag rsi-detail__tag--meta">{createdLabel}</span>
        </div>
      </div>
      <div className="rsi-detail__actions">
        {liveCost != null && (
          <span className="rsi-canvas-area__cost" style={{ marginRight: 4 }}>
            <svg
              width="14"
              height="14"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth={1.5}
              aria-hidden
            >
              <text x="12" y="18" textAnchor="middle" fontSize="16" fill="currentColor" stroke="none">
                ¥
              </text>
            </svg>
            {t('rsi.detail.estimatedCost', { cost: formatCost(liveCost) })}
          </span>
        )}
        {actions.map((action) => {
          const primary = action === 'config';
          return (
            <button
              key={action}
              type="button"
              className={`rsi-btn ${primary ? 'rsi-btn--ghost' : 'rsi-btn--primary'}`}
              onClick={() => handleAction(action)}
              disabled={busy}
              data-testid={`rsi-action-${action}`}
            >
              {actionLabel[action]}
            </button>
          );
        })}
      </div>
    </div>
  );
}

function StatusIcon({ kind }: { kind: StatusBadgeKind }) {
  if (kind === 'running') {
    return (
      <svg viewBox="0 0 16 16" width="14" height="14" fill="none" aria-hidden="true">
        <path
          d="M11 11L5 11L5 9.03003L3 9.03003L3 11C3 12.1 3.9 13 5 13L11 13C12.1 13 13 12.1 13 11L13 9.03003L11 9.03003L11 11Z"
          fill="rgb(11,184,178)"
          fillRule="nonzero"
        />
        <path
          d="M11 3L5 3C3.9 3 3 3.9 3 5L3 7.03L5 7.03L5 5L11 5L11 7.03L13 7.03L13 5C13 3.9 12.1 3 11 3Z"
          fill="rgb(11,184,178)"
          fillRule="nonzero"
        />
      </svg>
    );
  }
  if (kind === 'queued') {
    return (
      <svg viewBox="0 0 16 16" width="14" height="14" fill="none" aria-hidden="true">
        <path
          d="M8 3C5.24 3 3 5.24 3 8C3 10.76 5.24 13 8 13C10.76 13 13 10.76 13 8C13 5.24 10.76 3 8 3ZM8 11C6.34 11 5 9.66 5 8C5 6.34 6.34 5 8 5C9.66 5 11 6.34 11 8C11 9.66 9.66 11 8 11Z"
          fill="rgb(194,194,194)"
          fillRule="nonzero"
        />
      </svg>
    );
  }
  if (kind === 'completed') {
    return (
      <svg viewBox="0 0 16 16" width="14" height="14" fill="none" aria-hidden="true">
        <circle cx="8" cy="8" r="7" fill="rgb(34,197,94)" />
        <path d="M6.5 9.2L4.8 7.5L3.8 8.5L6.5 11.2L12 5.7L11 4.7L6.5 9.2Z" fill="white" fillRule="nonzero" />
      </svg>
    );
  }
  // failed
  return (
    <svg viewBox="0 0 16 16" width="14" height="14" fill="none" aria-hidden="true">
      <circle cx="8" cy="8" r="7" fill="rgb(239,68,68)" />
      <path
        d="M8 4.5C7.6 4.5 7.25 4.85 7.25 5.25L7.25 8.5C7.25 8.9 7.6 9.25 8 9.25C8.4 9.25 8.75 8.9 8.75 8.5L8.75 5.25C8.75 4.85 8.4 4.5 8 4.5ZM8 11.5C8.41 11.5 8.75 11.16 8.75 10.75C8.75 10.34 8.41 10 8 10C7.59 10 7.25 10.34 7.25 10.75C7.25 11.16 7.59 11.5 8 11.5Z"
        fill="white"
        fillRule="nonzero"
      />
    </svg>
  );
}
