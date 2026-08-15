import { useTranslation } from 'react-i18next';
import { CheckCircle2, AlertCircle, Loader2, KeyRound, ArrowUpRight } from 'lucide-react';
import type { OnboardingModelForm, ModelValidateState } from '../types';
import { OnboardingSection } from './OnboardingSection';

/** 无 API Key 用户的获取指引：华为云 MaaS 服务快速入门。 */
const MAAS_GUIDE_URL = 'https://support.huaweicloud.com/qs-maas/qs-maas-0001.html';

interface ModelStepProps {
  form: OnboardingModelForm;
  onChange: (patch: Partial<OnboardingModelForm>) => void;
  validateState: ModelValidateState;
  onValidate: () => void;
  error: string | null;
  configured?: boolean;
  summary?: string;
  onOpenModelConfig: () => void;
}

const FIELD_KEYS: (keyof OnboardingModelForm)[] = [
  'model_provider',
  'model_name',
  'api_base',
  'api_key',
  'reasoning_level',
];

export function ModelStep({
  form,
  onChange,
  validateState,
  onValidate,
  error,
  configured = false,
  summary,
  onOpenModelConfig,
}: ModelStepProps) {
  const { t } = useTranslation();

  const canValidate =
    !!form.model_name.trim() && !!form.api_base.trim() && !!form.api_key.trim();

  return (
    <div className="onboarding-step">
      <p className="onboarding-step__desc">{t('onboarding.model.desc')}</p>

      <OnboardingSection
        title={t('onboarding.model.sectionTitle')}
        configured={configured}
        summary={summary ? t('onboarding.model.summary', { model: summary }) : undefined}
      >
        <a
          className="onboarding-callout"
          href={MAAS_GUIDE_URL}
          target="_blank"
          rel="noreferrer noopener"
        >
          <span className="onboarding-callout__icon">
            <KeyRound size={18} strokeWidth={2} aria-hidden />
          </span>
          <span className="onboarding-callout__body">
            <span className="onboarding-callout__title">{t('onboarding.model.noKey.title')}</span>
            <span className="onboarding-callout__text">{t('onboarding.model.noKey.desc')}</span>
          </span>
          <span className="onboarding-callout__link">
            {t('onboarding.model.noKey.link')}
            <ArrowUpRight size={14} aria-hidden />
          </span>
        </a>

        <div className="onboarding-form">
          {FIELD_KEYS.map((key) => {
            const optional = key === 'reasoning_level';
            return (
              <label className="onboarding-field" key={key}>
                <span className="onboarding-field__label">
                  {t(`onboarding.model.fields.${key}.label`)}
                  {!optional && <span className="onboarding-field__required">*</span>}
                </span>
                <input
                  type={key === 'api_key' ? 'password' : 'text'}
                  className="onboarding-field__input"
                  value={form[key]}
                  placeholder={t(`onboarding.model.fields.${key}.placeholder`)}
                  autoComplete={key === 'api_key' ? 'new-password' : 'off'}
                  onChange={(e) => onChange({ [key]: e.target.value })}
                />
                <span className="onboarding-field__hint">
                  {t(`onboarding.model.fields.${key}.hint`)}
                </span>
              </label>
            );
          })}
        </div>

        <div className="onboarding-model-validate">
          <button
            type="button"
            className="onboarding-btn onboarding-btn--ghost"
            onClick={onValidate}
            disabled={!canValidate || validateState === 'validating'}
          >
            {validateState === 'validating' && (
              <Loader2 size={14} className="onboarding-spin" aria-hidden />
            )}
            {t('onboarding.model.validate')}
          </button>
          {validateState === 'ok' && (
            <span className="onboarding-validate-result onboarding-validate-result--ok">
              <CheckCircle2 size={14} aria-hidden />
              {t('onboarding.model.validateOk')}
            </span>
          )}
          {validateState === 'err' && (
            <span className="onboarding-validate-result onboarding-validate-result--err">
              <AlertCircle size={14} aria-hidden />
              {t('onboarding.model.validateErr')}
            </span>
          )}
        </div>

        {error && (
          <div className="onboarding-alert onboarding-alert--err">
            <AlertCircle size={14} aria-hidden />
            <span>{error}</span>
          </div>
        )}
      </OnboardingSection>

      <button type="button" className="onboarding-link" onClick={onOpenModelConfig}>
        {t('onboarding.model.link')}
        <ArrowUpRight size={13} aria-hidden />
      </button>

      <p className="onboarding-step__note">{t('onboarding.model.note')}</p>
    </div>
  );
}
