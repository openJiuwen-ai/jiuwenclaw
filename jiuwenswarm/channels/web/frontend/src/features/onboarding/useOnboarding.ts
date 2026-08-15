/**
 * 配置引导向导状态机。
 *
 * 只负责「当前所选模式 / 步骤序列 / 当前步 / 已到达步」这类导航状态，
 * 各步表单值与保存逻辑由 OnboardingGuide 集中管理（走 webRequest）。
 *
 * 分页推进思路参考 InteractionSlot/InteractionPrompt 的 page/reached 模式：
 * reached 记录用户曾到达的最远一步，用于进度条上「已访问步骤可点击回跳」。
 */

import { useCallback, useMemo, useState } from 'react';

export type OnboardingMode = 'minimal' | 'classic';

export type OnboardingStepId =
  | 'welcome'
  | 'model'
  | 'agent'
  | 'security'
  | 'other'
  | 'done';

/** 精简模式：最小可运行配置（模型）。 */
const MINIMAL_STEPS: OnboardingStepId[] = ['welcome', 'model', 'done'];

/**
 * 经典模式：对齐「配置信息」页的 4 个页签
 * （模型配置 / Agent 配置 / 安全配置 / 其他配置）逐一介绍并配置。
 */
const CLASSIC_STEPS: OnboardingStepId[] = [
  'welcome',
  'model',
  'agent',
  'security',
  'other',
  'done',
];

export interface OnboardingNav {
  mode: OnboardingMode | null;
  steps: OnboardingStepId[];
  stepIndex: number;
  currentStep: OnboardingStepId;
  totalSteps: number;
  reached: number;
  isFirst: boolean;
  isLast: boolean;
  setMode: (mode: OnboardingMode) => void;
  goNext: () => void;
  goPrev: () => void;
  goTo: (index: number) => void;
  reset: () => void;
}

export function useOnboarding(): OnboardingNav {
  const [mode, setModeState] = useState<OnboardingMode | null>(null);
  const [stepIndex, setStepIndex] = useState(0);
  const [reached, setReached] = useState(0);

  const steps = useMemo<OnboardingStepId[]>(() => {
    if (mode === 'minimal') return MINIMAL_STEPS;
    if (mode === 'classic') return CLASSIC_STEPS;
    // 尚未选择模式时，只有欢迎页。
    return ['welcome'];
  }, [mode]);

  const totalSteps = steps.length;
  const safeIndex = Math.min(stepIndex, totalSteps - 1);
  const currentStep = steps[safeIndex] ?? 'welcome';

  const setMode = useCallback((next: OnboardingMode) => {
    setModeState(next);
  }, []);

  const goNext = useCallback(() => {
    setStepIndex((prev) => {
      const next = prev + 1;
      setReached((r) => Math.max(r, next));
      return next;
    });
  }, []);

  const goPrev = useCallback(() => {
    setStepIndex((prev) => Math.max(0, prev - 1));
  }, []);

  const goTo = useCallback((index: number) => {
    setStepIndex(() => Math.max(0, index));
  }, []);

  const reset = useCallback(() => {
    setModeState(null);
    setStepIndex(0);
    setReached(0);
  }, []);

  return {
    mode,
    steps,
    stepIndex: safeIndex,
    currentStep,
    totalSteps,
    reached,
    isFirst: safeIndex === 0,
    isLast: safeIndex >= totalSteps - 1,
    setMode,
    goNext,
    goPrev,
    goTo,
    reset,
  };
}
