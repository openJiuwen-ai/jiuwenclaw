/**
 * OnboardingGuide — 面向新用户的配置引导向导。
 *
 * 自包含弹框：每一步内嵌精简配置表单，用户在弹框内直接填写并保存
 * （复用后端 RPC：models.replace_all / config.validate_model / config.get / config.set）。
 * 完成一步自动切到下一步，也可返回上一步。首次弹出与「不再自动显示」开关走本机 localStorage。
 *
 * 视觉沿用现有全屏 Modal 模式（遮罩 + bg-card 面板 + animate-rise），
 * 分页推进思路参考 InteractionSlot/InteractionPrompt。
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { X, ChevronLeft, Check, Loader2, Compass, Minimize2 } from 'lucide-react';
import { webRequest } from '../../services/webClient';
import { useSessionStore } from '../../stores';
import type { ModelEntry } from '../../types';
import { useOnboarding, type OnboardingStepId } from './useOnboarding';
import {
  EMPTY_MODEL_FORM,
  EMPTY_SEARCH_KEYS,
  EMPTY_FEATURES,
  type OnboardingModelForm,
  type OnboardingSearchKeys,
  type OnboardingFeatures,
  type OnboardingFeatureKey,
  type ModelValidateState,
} from './types';
import { isOnboardingDismissed, setOnboardingDismissed } from './persistence';
import { WelcomeModeStep } from './steps/WelcomeModeStep';
import { ModelStep } from './steps/ModelStep';
import { AgentConfigStep } from './steps/AgentConfigStep';
import { SecurityStep } from './steps/SecurityStep';
import { OtherConfigStep } from './steps/OtherConfigStep';
import { DoneStep } from './steps/DoneStep';
import './onboarding.css';

interface OnboardingGuideProps {
  open: boolean;
  onClose: () => void;
}

/** 左侧步骤导航栏（含欢迎与完成）。窄屏时通过 CSS 转为顶部横向进度。 */
function StepRail({
  steps,
  stepIndex,
  reached,
  onJump,
}: {
  steps: OnboardingStepId[];
  stepIndex: number;
  reached: number;
  onJump: (index: number) => void;
}) {
  const { t } = useTranslation();
  return (
    <ol className="onboarding-rail__steps">
      {steps.map((step, idx) => {
        const done = idx < stepIndex;
        const active = idx === stepIndex;
        const reachable = idx <= reached;
        return (
          <li
            key={step}
            className={`onboarding-rail__step${active ? ' is-active' : ''}${done ? ' is-done' : ''}`}
          >
            <button
              type="button"
              className="onboarding-rail__node"
              onClick={() => reachable && onJump(idx)}
              disabled={!reachable}
              aria-current={active ? 'step' : undefined}
            >
              <span className="onboarding-rail__dot">
                {done ? <Check size={13} strokeWidth={3} aria-hidden /> : idx + 1}
              </span>
              <span className="onboarding-rail__label">{t(`onboarding.steps.${step}`)}</span>
            </button>
          </li>
        );
      })}
    </ol>
  );
}

function errorMessage(err: unknown): string {
  if (err instanceof Error && err.message) return err.message;
  return '';
}

function OnboardingGuideInner({ onClose }: { onClose: () => void }) {
  const { t } = useTranslation();
  const nav = useOnboarding();

  // 表单始终以空开始，各字段仅显示灰色示例占位符，不预填既有模型的值。
  const [modelForm, setModelForm] = useState<OnboardingModelForm>(EMPTY_MODEL_FORM);
  const [validateState, setValidateState] = useState<ModelValidateState>('idle');
  const [modelError, setModelError] = useState<string | null>(null);

  const [permissionsEnabled, setPermissionsEnabled] = useState(false);
  const [searchKeys, setSearchKeys] = useState<OnboardingSearchKeys>(EMPTY_SEARCH_KEYS);
  // 特性开关一律以关闭态展示，不回填真实配置；仅记录用户手动改动过的键，保存时才写回，避免误改既有设置。
  const [features, setFeatures] = useState<OnboardingFeatures>(EMPTY_FEATURES);
  const touchedFeaturesRef = useRef<Set<OnboardingFeatureKey>>(new Set());

  const [dontShowAgain, setDontShowAgain] = useState(() => isOnboardingDismissed());
  const [saving, setSaving] = useState(false);
  const [stepError, setStepError] = useState<string | null>(null);
  // 最小化：跳转到配置页时不关闭引导，仅收起为悬浮按钮，保留当前步与已填内容。
  const [minimized, setMinimized] = useState(false);

  // 读取已配置内容用于回显（用户可能分多次完成配置）。加载完成前门控依赖配置的步骤，
  // 以保证「已配置则默认折叠」等初始状态正确。
  const [configLoaded, setConfigLoaded] = useState(false);
  // 回填时的模型快照与来源索引：用于「未改动则跳过保存」及保留 YAML 占位符（origin_index）。
  const initialModelRef = useRef<OnboardingModelForm | null>(null);
  const modelOriginRef = useRef<{ model_name: string; api_base: string; origin_index?: number } | null>(
    null,
  );

  useEffect(() => {
    let cancelled = false;
    const asBool = (v: unknown) => v === true || v === 'true';
    void (async () => {
      // 模型：回填后端已配置的默认模型（models.list 返回解密后的完整值）。
      const models = useSessionStore.getState().availableModels;
      const def = models.find((m) => m.is_default) ?? models[0];
      if (def && !cancelled) {
        const prefilled: OnboardingModelForm = {
          model_name: def.model_name ?? '',
          api_base: def.api_base ?? '',
          api_key: def.api_key ?? '',
          model_provider: def.model_provider ?? '',
          reasoning_level: def.reasoning_level ?? '',
        };
        setModelForm(prefilled);
        initialModelRef.current = prefilled;
        modelOriginRef.current = {
          model_name: def.model_name ?? '',
          api_base: def.api_base ?? '',
          origin_index: def.origin_index,
        };
      }
      // 其他配置：读取权限开关、第三方 Key、特性开关的真实值。
      try {
        const cfg = await webRequest<Record<string, unknown>>('config.get');
        if (!cancelled && cfg) {
          setPermissionsEnabled(asBool(cfg.permissions_enabled));
          setSearchKeys((prev) => {
            const next = { ...prev };
            (Object.keys(next) as (keyof OnboardingSearchKeys)[]).forEach((k) => {
              const v = cfg[k];
              if (typeof v === 'string') next[k] = v;
            });
            return next;
          });
          setFeatures((prev) => {
            const next = { ...prev };
            (Object.keys(next) as OnboardingFeatureKey[]).forEach((k) => {
              if (k in cfg) next[k] = asBool(cfg[k]);
            });
            return next;
          });
        }
      } catch {
        // 读取失败不阻塞引导，用空值继续。
      } finally {
        if (!cancelled) setConfigLoaded(true);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // 模型「已配置」状态与摘要（折叠态展示）。
  const availableModels = useSessionStore((s) => s.availableModels);
  const defaultModel = availableModels.find((m) => m.is_default) ?? availableModels[0];
  const modelConfigured = !!defaultModel;
  const modelSummary = defaultModel?.model_name ?? '';

  // 完成/关闭都只按开关决定是否永久关闭自动弹出；未勾选则下次启动仍会自动弹。
  const finish = useCallback(() => {
    setOnboardingDismissed(dontShowAgain);
    onClose();
  }, [dontShowAgain, onClose]);

  const handleClose = useCallback(() => {
    setOnboardingDismissed(dontShowAgain);
    onClose();
  }, [dontShowAgain, onClose]);

  // 跳转到对应配置位置：先把引导最小化为悬浮按钮（不销毁，保留状态），再导航。
  // detail 为字符串时切换主导航；为对象 { nav, configGroup } 时同时定位到配置页签。
  const minimizeAndNavigate = useCallback(
    (detail: string | { nav: string; configGroup?: string }) => {
      setMinimized(true);
      window.dispatchEvent(new CustomEvent('jiuwen:nav', { detail }));
    },
    [],
  );

  const handleValidateModel = useCallback(async () => {
    setValidateState('validating');
    try {
      await webRequest(
        'config.validate_model',
        {
          api_base: modelForm.api_base.trim(),
          api_key: modelForm.api_key.trim(),
          model: modelForm.model_name.trim(),
          model_provider: modelForm.model_provider.trim() || 'OpenAI',
          reasoning_level: modelForm.reasoning_level.trim() || undefined,
        },
        { timeoutMs: 60000 },
      );
      setValidateState('ok');
    } catch {
      setValidateState('err');
    }
  }, [modelForm]);

  const saveModel = useCallback(async (): Promise<boolean> => {
    setModelError(null);
    // 已配置且本次未改动：直接放行，不重复写回（保留 YAML 占位符等既有配置）。
    if (
      initialModelRef.current &&
      JSON.stringify(initialModelRef.current) === JSON.stringify(modelForm)
    ) {
      return true;
    }
    const model_name = modelForm.model_name.trim();
    const api_base = modelForm.api_base.trim();
    const api_key = modelForm.api_key.trim();
    if (!model_name || !api_base || !api_key) {
      setModelError(t('onboarding.model.errRequired'));
      return false;
    }
    setSaving(true);
    try {
      const origin = modelOriginRef.current;
      const keepOriginIndex =
        origin &&
        origin.model_name === model_name &&
        origin.api_base === api_base &&
        origin.origin_index !== undefined;
      const entry: ModelEntry = {
        model_name,
        api_base,
        api_key,
        model_provider: modelForm.model_provider.trim() || 'OpenAI',
        reasoning_level: modelForm.reasoning_level.trim() || undefined,
        is_default: true,
        ...(keepOriginIndex ? { origin_index: origin.origin_index } : {}),
      };
      // 合并既有模型，避免 replace_all 覆盖掉用户已有配置（手动重开场景）。
      const current = useSessionStore.getState().availableModels;
      const others = current.filter(
        (m) => !(m.model_name === entry.model_name && m.api_base === entry.api_base),
      );
      await webRequest('models.replace_all', { models: [entry, ...others] });
      try {
        const resp = await webRequest<{ models: ModelEntry[]; active_model: string }>('models.list');
        if (resp?.models) {
          useSessionStore.getState().setAvailableModels(resp.models, resp.active_model);
        }
      } catch {
        // 刷新失败不影响保存结果。
      }
      return true;
    } catch (err) {
      setModelError(errorMessage(err) || t('onboarding.model.errSave'));
      return false;
    } finally {
      setSaving(false);
    }
  }, [modelForm, t]);

  const saveSecurity = useCallback(async (): Promise<boolean> => {
    setStepError(null);
    setSaving(true);
    try {
      await webRequest('config.set', { permissions_enabled: String(permissionsEnabled) });
      return true;
    } catch (err) {
      setStepError(errorMessage(err) || t('onboarding.errSave'));
      return false;
    } finally {
      setSaving(false);
    }
  }, [permissionsEnabled, t]);

  const saveOther = useCallback(async (): Promise<boolean> => {
    setStepError(null);
    const updates: Record<string, string> = {};
    // 第三方 API Key：仅保存非空项。
    (Object.keys(searchKeys) as (keyof OnboardingSearchKeys)[]).forEach((key) => {
      const value = searchKeys[key].trim();
      if (value) updates[key] = value;
    });
    // 特性开关：仅保存用户手动改动过的键，未触碰的保持既有配置不变。
    touchedFeaturesRef.current.forEach((key) => {
      updates[key] = String(features[key]);
    });
    if (Object.keys(updates).length === 0) return true;
    setSaving(true);
    try {
      await webRequest('config.set', updates);
      return true;
    } catch (err) {
      setStepError(errorMessage(err) || t('onboarding.errSave'));
      return false;
    } finally {
      setSaving(false);
    }
  }, [searchKeys, features, t]);

  const handleAdvance = useCallback(async () => {
    if (saving) return;
    switch (nav.currentStep) {
      case 'welcome':
        if (nav.mode) nav.goNext();
        return;
      case 'model':
        if (await saveModel()) nav.goNext();
        return;
      case 'agent':
        nav.goNext();
        return;
      case 'security':
        if (await saveSecurity()) nav.goNext();
        return;
      case 'other':
        if (await saveOther()) nav.goNext();
        return;
      case 'done':
        finish();
        return;
      default:
        nav.goNext();
    }
  }, [saving, nav, saveModel, saveSecurity, saveOther, finish]);

  const handleSkip = useCallback(() => {
    setModelError(null);
    setStepError(null);
    nav.goNext();
  }, [nav]);

  const patchModel = useCallback((patch: Partial<OnboardingModelForm>) => {
    setModelError(null);
    setValidateState('idle');
    setModelForm((prev) => ({ ...prev, ...patch }));
  }, []);

  const patchSearch = useCallback((patch: Partial<OnboardingSearchKeys>) => {
    setSearchKeys((prev) => ({ ...prev, ...patch }));
  }, []);

  const toggleFeature = useCallback((key: OnboardingFeatureKey, value: boolean) => {
    touchedFeaturesRef.current.add(key);
    setFeatures((prev) => ({ ...prev, [key]: value }));
  }, []);

  const primaryLabel = (() => {
    switch (nav.currentStep) {
      case 'welcome':
        return t('onboarding.actions.start');
      case 'model':
      case 'security':
      case 'other':
        return t('onboarding.actions.saveNext');
      case 'done':
        return t('onboarding.actions.finish');
      default:
        return t('onboarding.actions.next');
    }
  })();

  const showSkip =
    nav.currentStep === 'model' ||
    nav.currentStep === 'agent' ||
    nav.currentStep === 'security' ||
    nav.currentStep === 'other';

  const primaryDisabled = saving || (nav.currentStep === 'welcome' && !nav.mode);

  const stepRequirement: 'required' | 'optional' | null = (() => {
    switch (nav.currentStep) {
      case 'model':
        return 'required';
      case 'agent':
      case 'security':
      case 'other':
        return 'optional';
      default:
        return null;
    }
  })();

  // 依赖后端配置的步骤：加载完成前显示占位，确保回显与「已配置默认折叠」初始态正确。
  const needsConfig =
    nav.currentStep === 'model' ||
    nav.currentStep === 'security' ||
    nav.currentStep === 'other';

  const renderStep = () => {
    if (needsConfig && !configLoaded) {
      return (
        <div className="onboarding-loading">
          <Loader2 size={20} className="onboarding-spin" aria-hidden />
          <span>{t('onboarding.loading')}</span>
        </div>
      );
    }
    switch (nav.currentStep) {
      case 'welcome':
        return <WelcomeModeStep mode={nav.mode} onSelectMode={nav.setMode} />;
      case 'model':
        return (
          <ModelStep
            form={modelForm}
            onChange={patchModel}
            validateState={validateState}
            onValidate={() => void handleValidateModel()}
            error={modelError}
            configured={modelConfigured}
            summary={modelSummary}
            onOpenModelConfig={() =>
              minimizeAndNavigate({ nav: 'configpanel', configGroup: 'model_default' })
            }
          />
        );
      case 'agent':
        return (
          <AgentConfigStep
            onOpenAgentConfig={() =>
              minimizeAndNavigate({ nav: 'configpanel', configGroup: 'agents' })
            }
          />
        );
      case 'security':
        return (
          <SecurityStep
            enabled={permissionsEnabled}
            onChange={setPermissionsEnabled}
            onOpenSecurityConfig={() =>
              minimizeAndNavigate({ nav: 'configpanel', configGroup: 'permissions' })
            }
          />
        );
      case 'other':
        return (
          <OtherConfigStep
            keys={searchKeys}
            onChange={patchSearch}
            features={features}
            onToggleFeature={toggleFeature}
            onOpenOtherConfig={() =>
              minimizeAndNavigate({ nav: 'configpanel', configGroup: 'third_party_api' })
            }
          />
        );
      case 'done':
        return (
          <DoneStep
            mode={nav.mode}
            dontShowAgain={dontShowAgain}
            onToggleDontShow={setDontShowAgain}
            onOpenConfig={() => minimizeAndNavigate('configpanel')}
          />
        );
      default:
        return null;
    }
  };

  // 最小化态：只显示右下角悬浮按钮，点击可回到引导的同一步（状态已保留）。
  if (minimized) {
    return (
      <div className="onboarding-fab">
        <button
          type="button"
          className="onboarding-fab__main"
          onClick={() => setMinimized(false)}
        >
          <Compass size={16} aria-hidden />
          <span>{t('onboarding.minimized.resume')}</span>
          <span className="onboarding-fab__step">
            {nav.stepIndex + 1}/{nav.totalSteps}
          </span>
        </button>
        <button
          type="button"
          className="onboarding-fab__close"
          onClick={handleClose}
          aria-label={t('onboarding.minimized.closeAria')}
        >
          <X size={14} aria-hidden />
        </button>
      </div>
    );
  }

  return (
    <div
      className="onboarding-overlay onboarding-overlay--full"
      role="dialog"
      aria-modal="true"
      aria-label={t('onboarding.title')}
    >
      <div className="onboarding-fullscreen animate-rise">
        {/* 左侧步骤导航栏 */}
        <aside className="onboarding-rail">
          <div className="onboarding-rail__head">
            <h2 className="onboarding-rail__title">{t('onboarding.title')}</h2>
            <p className="onboarding-rail__subtitle">
              {t('onboarding.stepCounter', {
                current: nav.stepIndex + 1,
                total: nav.totalSteps,
              })}
            </p>
          </div>
          <StepRail
            steps={nav.steps}
            stepIndex={nav.stepIndex}
            reached={nav.reached}
            onJump={nav.goTo}
          />
        </aside>

        {/* 右侧内容区 */}
        <div className="onboarding-main">
          <div className="onboarding-main__head">
            <span className="onboarding-main__step">{t(`onboarding.steps.${nav.currentStep}`)}</span>
            <div className="onboarding-main__head-actions">
              <button
                type="button"
                className="onboarding-icon-btn"
                onClick={() => setMinimized(true)}
                aria-label={t('onboarding.minimized.minimizeAria')}
                title={t('onboarding.minimized.minimizeAria')}
              >
                <Minimize2 size={16} aria-hidden />
              </button>
              <button
                type="button"
                className="onboarding-icon-btn"
                onClick={handleClose}
                aria-label={t('common.close')}
                title={t('common.close')}
              >
                <X size={16} aria-hidden />
              </button>
            </div>
          </div>

          <div className="onboarding-main__body">
            <div className="onboarding-main__inner">
              {stepRequirement && (
                <div className="onboarding-req-row">
                  <span className={`onboarding-req onboarding-req--${stepRequirement}`}>
                    {t(`onboarding.badges.${stepRequirement}`)}
                  </span>
                  <span className="onboarding-req-row__text">
                    {t(`onboarding.badges.${stepRequirement}Note`)}
                  </span>
                </div>
              )}
              {renderStep()}
            </div>
          </div>

          <div className="onboarding-main__foot">
            {stepError && <div className="onboarding-main__error">{stepError}</div>}
            <div className="onboarding-main__foot-bar">
              <button
                type="button"
                className="onboarding-btn onboarding-btn--ghost"
                onClick={nav.goPrev}
                disabled={nav.isFirst || saving}
              >
                <ChevronLeft size={15} aria-hidden />
                {t('onboarding.actions.back')}
              </button>
              <div className="onboarding-main__foot-right">
                {showSkip && (
                  <button
                    type="button"
                    className="onboarding-btn onboarding-btn--ghost"
                    onClick={handleSkip}
                    disabled={saving}
                  >
                    {t('onboarding.actions.skip')}
                  </button>
                )}
                <button
                  type="button"
                  className="onboarding-btn onboarding-btn--primary"
                  onClick={() => void handleAdvance()}
                  disabled={primaryDisabled}
                >
                  {saving && <Loader2 size={14} className="onboarding-spin" aria-hidden />}
                  {primaryLabel}
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

export function OnboardingGuide({ open, onClose }: OnboardingGuideProps) {
  if (!open) return null;
  return <OnboardingGuideInner onClose={onClose} />;
}
