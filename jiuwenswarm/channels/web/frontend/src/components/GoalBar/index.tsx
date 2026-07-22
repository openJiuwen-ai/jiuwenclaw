/**
 * GoalBar — 持续目标（Goal）吸附条
 *
 * 位置：ChatPanel 的 chat-compose 区域内，InteractionSlot（授权/交互卡）之后、
 * InputArea 之前。视觉上是紧贴输入框的吸附条（参照 InteractionSlot.css
 * `.interaction-slot--attached` / ChatPanel.css `.chat-active-team-group` 共用的公式：
 * `width: calc(100% - 48px); margin: 0 auto -1px;` + `border-radius: 18px 18px 0 0`），
 * 不是独立悬浮卡片。
 *
 * 只在存在 goal 时渲染（不管 goal 是通过 "+" 菜单 armed 流程、还是聊天里直接说明、还是
 * 后端自动识别设置的，只要 goalStore 里有数据就显示——armed 态本身不在这里处理，那是
 * InputArea 工具栏"目标"标签的职责，见 InputArea.tsx）。goal 清除时自动消失。
 *
 * 后端协议：一个 session 同时最多一个 Goal，见
 * cjh/goal/Goal持续目标Web前端对接.md。真机联调受限于后端未实现，见 progress.md 待办清单。
 */

import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Pause, Pencil, Play, Target, Trash2 } from 'lucide-react';
import { useChatStore, useGoalStore } from '../../stores';
import type { GoalStatus } from '../../types';
import { EditGoalModal } from './EditGoalModal';
import './GoalBar.css';

interface GoalBarProps {
  onSetGoal: (sessionId: string, objective: string) => void;
  onPauseGoal: (sessionId: string) => void;
  onResumeGoal: (sessionId: string) => void;
  onClearGoal: (sessionId: string) => void;
}

const STATUS_TONE: Record<string, 'active' | 'paused' | 'completed' | 'blocked'> = {
  active: 'active',
  paused: 'paused',
  completed: 'completed',
  blocked: 'blocked',
};

function formatElapsed(createdAtIso: string | undefined, now: number): string {
  if (!createdAtIso) return '0s';
  const startMs = new Date(createdAtIso).getTime();
  const seconds = Math.max(0, Math.floor((now - startMs) / 1000));
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  const remainSeconds = seconds % 60;
  return remainSeconds > 0 ? `${minutes}m ${remainSeconds}s` : `${minutes}m`;
}

export function GoalBar({ onSetGoal, onPauseGoal, onResumeGoal, onClearGoal }: GoalBarProps) {
  const { t } = useTranslation();
  const activeSessionId = useChatStore((s) => s.activeSessionId);
  const goal = useGoalStore((s) => s.runtimes[activeSessionId ?? '']?.goal ?? null);
  const pendingAction = useGoalStore((s) => s.runtimes[activeSessionId ?? '']?.pendingAction ?? null);
  // 后端不下发 created_at（backend-requests.md #2），优先用真实值，没有时退回本地兜底时间
  const localCreatedAt = useGoalStore((s) => (goal ? s.localCreatedAt[goal.goal_id] : undefined));
  // completed 目标的 GoalBar 显隐单独用这个标记控制，不再靠 goal 是否存在于 store 里判断——
  // goal 数据本身要一直保留（"设为目标"徽章等消费方依赖 goal.objective），见 useWebSocket.ts
  // 的 applyIncomingGoal 注释。
  const bannerHidden = useGoalStore((s) => (goal ? Boolean(s.bannerHiddenGoalIds[goal.goal_id]) : false));
  const [editing, setEditing] = useState(false);
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    // completed/blocked 是终态，不再有新的 attempt，耗时不该继续跳字——冻结在跳变那一刻附近的值
    if (!goal || goal.status === 'completed' || goal.status === 'blocked') return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [goal]);

  // 编辑弹窗打开期间目标在后台跑完了（用户可能没注意到，弹窗内容也不会自动同步最新状态）——
  // 与其等 4 秒自动隐藏把弹窗连带 GoalBar 一起悄悄拽没（那样用户完全不知道发生了什么，编辑内容
  // 也白写了），不如一发现就主动关掉并提示，避免用户点保存把一个已经结束的目标悄悄"复活"。
  useEffect(() => {
    if (editing && goal?.status === 'completed' && activeSessionId) {
      setEditing(false);
      useChatStore.getState().addMessage(activeSessionId, {
        id: `error-${Date.now()}`,
        role: 'system',
        content: t('goal.editStaleWarning'),
        timestamp: new Date().toISOString(),
      });
    }
  }, [editing, goal?.status, activeSessionId, t]);

  if (!activeSessionId || !goal) return null;
  if (goal.status === 'completed' && bannerHidden) return null;

  const tone = STATUS_TONE[goal.status as GoalStatus] ?? 'active';
  const isPausable = goal.status === 'active';
  const isResumable = goal.status === 'paused' || goal.status === 'blocked';
  const isBusy = pendingAction !== null;

  return (
    <div className="goal-bar-attached">
      <div className={`goal-bar goal-bar--tone-${tone}`}>
        <Target size={16} strokeWidth={2} className="goal-bar__icon" />
        <div className="goal-bar__main">
          <span className={`goal-bar__status goal-bar__status--${tone}`}>
            {t(`goal.status.${goal.status}`, goal.status)}
          </span>
          <span className="goal-bar__objective" title={goal.objective}>
            {goal.objective}
          </span>
          <span className="goal-bar__elapsed">· {formatElapsed(goal.created_at ?? localCreatedAt, now)}</span>
        </div>
        <div className="goal-bar__actions">
          {goal.status !== 'completed' && (
            <button
              type="button"
              title={t('goal.action.editTooltip')}
              className="goal-bar__action-btn"
              onClick={() => setEditing(true)}
            >
              <Pencil size={14} strokeWidth={2} />
            </button>
          )}
          {(isPausable || isResumable) && (
            <button
              type="button"
              title={isPausable ? t('goal.action.pauseTooltip') : t('goal.action.resumeTooltip')}
              className="goal-bar__action-btn"
              disabled={isBusy}
              onClick={() => (isPausable ? onPauseGoal(activeSessionId) : onResumeGoal(activeSessionId))}
            >
              {isPausable ? <Pause size={14} strokeWidth={2} /> : <Play size={14} strokeWidth={2} />}
            </button>
          )}
          <button
            type="button"
            title={t('goal.action.deleteTooltip')}
            className="goal-bar__action-btn goal-bar__action-btn--danger"
            disabled={isBusy}
            onClick={() => onClearGoal(activeSessionId)}
          >
            <Trash2 size={14} strokeWidth={2} />
          </button>
        </div>
      </div>

      {editing && (
        <EditGoalModal
          initialObjective={goal.objective}
          onCancel={() => setEditing(false)}
          onSave={(objective) => {
            // 防御性兜底：正常情况下上面那个 useEffect 会在目标转 completed 的瞬间就关掉弹窗，
            // 这里理论上不会命中，但保存动作本身是不可逆的（会把已完成的目标复活），多一层检查
            // 成本很低。
            const latestGoal = useGoalStore.getState().runtimes[activeSessionId]?.goal;
            if (!latestGoal || latestGoal.goal_id !== goal.goal_id || latestGoal.status === 'completed') {
              setEditing(false);
              useChatStore.getState().addMessage(activeSessionId, {
                id: `error-${Date.now()}`,
                role: 'system',
                content: t('goal.editStaleWarning'),
                timestamp: new Date().toISOString(),
              });
              return;
            }
            onSetGoal(activeSessionId, objective);
            setEditing(false);
          }}
        />
      )}
    </div>
  );
}
