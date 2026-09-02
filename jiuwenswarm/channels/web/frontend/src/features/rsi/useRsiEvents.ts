// RSI 推送事件订阅：隔离在 RSI feature 内，避免在公共 useWebSocket.ts 中堆叠 rsi.* 分支。
// 在 RSI 页面挂载时注册，卸载时清理。事件归并到 rsiStore。

import { useEffect } from 'react';
import { webClient } from '../../services/webClient';
import { useRsiStore } from './rsiStore';
import { RSI_EVENTS } from './rsiApi';
import type { RsiTrainingStatusChangedPayload, RsiTrainingProgressPayload, RsiTrainingTreeDeltaPayload } from './types';

/**
 * 订阅 RSI 三个推送事件。仅在 RSI 页面挂载期间生效，组件卸载自动解订。
 */
export function useRsiEvents(enabled: boolean): void {
  const applyStatusChanged = useRsiStore((s) => s.applyStatusChanged);
  const applyProgress = useRsiStore((s) => s.applyProgress);
  const applyTreeDelta = useRsiStore((s) => s.applyTreeDelta);

  useEffect(() => {
    if (!enabled) return;
    const offStatus = webClient.on<RsiTrainingStatusChangedPayload>(RSI_EVENTS.statusChanged, ({ payload }) => {
      if (payload?.task_id) applyStatusChanged(payload);
    });
    const offProgress = webClient.on<RsiTrainingProgressPayload>(RSI_EVENTS.progress, ({ payload }) => {
      if (payload?.task_id) applyProgress(payload);
    });
    const offTree = webClient.on<RsiTrainingTreeDeltaPayload>(RSI_EVENTS.treeDelta, ({ payload }) => {
      if (payload?.task_id) applyTreeDelta(payload);
    });
    return () => {
      offStatus();
      offProgress();
      offTree();
    };
  }, [enabled, applyStatusChanged, applyProgress, applyTreeDelta]);
}
