/**
 * StatusBar 组件
 *
 * 状态栏，显示当前模式、处理状态
 * 采用 JiuwenClaw 风格
 */

import { useTranslation } from 'react-i18next';
import { useChatStore } from '../../stores';
import './StatusBar.css';

interface StatusBarProps {
  onCancel?: () => void;
  teamMode?: boolean;
}

export function StatusBar({ onCancel, teamMode = false }: StatusBarProps) {
  const { t } = useTranslation();
  const { isProcessing, isPaused, pausedTask, interruptResult, switchingMode } = useChatStore();
  const showExec = (isProcessing || isPaused) && !switchingMode;
  /** 有中断结果文案时，统一只显示居中的横条（任务已暂停/恢复/取消/切换/已中断） */
  const showInterruptBarOnly = Boolean(interruptResult?.message);

  return (
    <div className="statusbar-root">
      <div className="statusbar-center">
        {showInterruptBarOnly && !teamMode ? (
          <div
            className={`pill animate-fade-in ${
              interruptResult!.success
                ? 'bg-info text-white border-info'
                : 'bg-danger text-white border-danger'
            }`}
          >
            <span className="text-sm">{interruptResult!.message}</span>
          </div>
        ) : (
          <>
        {/* 执行状态：左侧取消，中间状态 */}
        {!teamMode && showExec && (
          <div className="statusbar-exec">
            {onCancel && (
              <button
                onClick={onCancel}
                className="statusbar-action-btn statusbar-action-btn--cancel"
              >
                {t('statusBar.cancel')}
              </button>
            )}

            <div className={`statusbar-pill ${isPaused ? 'statusbar-pill--paused' : 'statusbar-pill--processing'}`}>
              <span className={`statusbar-dot ${isPaused ? '' : 'statusbar-dot--pulse'}`.trim()} />
              <span>
                {isPaused
                  ? pausedTask
                    ? t('statusBar.pausedWithTask', { task: pausedTask.slice(0, 20) })
                    : t('statusBar.paused')
                  : t('statusBar.processing')}
              </span>
            </div>
          </div>
        )}
          </>
        )}
      </div>
    </div>
  );
}
