/**
 * StatusBar 组件
 *
 * 状态栏，显示当前模式、处理状态、暂停/恢复按钮
 * 采用 JiuwenClaw 风格
 */

import { useChatStore } from '../../stores';
import './StatusBar.css';

interface StatusBarProps {
  onPause?: () => void;
  onCancel?: () => void;
  onResume?: () => void;
}

export function StatusBar({ onPause, onCancel, onResume }: StatusBarProps) {
  const { isProcessing, isPaused, pausedTask, interruptResult } = useChatStore();
  const showExec = isProcessing || isPaused;
  /** 有中断结果文案时，统一只显示居中的横条（任务已暂停/恢复/取消/切换/已中断） */
  const showInterruptBarOnly = Boolean(interruptResult?.message);

  return (
    <div className="statusbar-root">
      <div className="statusbar-center">
        {showInterruptBarOnly ? (
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
        {/* 执行状态：左侧取消，中间状态，右侧暂停/恢复 */}
        {showExec && (
          <div className="statusbar-exec">
            {onCancel && (
              <button
                onClick={onCancel}
                className="statusbar-action-btn statusbar-action-btn--cancel"
              >
                取消
              </button>
            )}

            <div className={`statusbar-pill ${isPaused ? 'statusbar-pill--paused' : 'statusbar-pill--processing'}`}>
              <span className={`statusbar-dot ${isPaused ? '' : 'statusbar-dot--pulse'}`.trim()} />
              <span>
                {isPaused
                  ? `已暂停${pausedTask ? `: ${pausedTask.slice(0, 20)}...` : ''}`
                  : '处理中...'}
              </span>
            </div>

            {isPaused ? (
              onResume && (
                <button
                  onClick={onResume}
                  className="statusbar-action-btn statusbar-action-btn--resume"
                >
                  恢复
                </button>
              )
            ) : (
              onPause && (
              <button
                onClick={onPause}
                className="statusbar-action-btn statusbar-action-btn--pause"
              >
                暂停
              </button>
              )
            )}
          </div>
        )}
          </>
        )}
      </div>
    </div>
  );
}
