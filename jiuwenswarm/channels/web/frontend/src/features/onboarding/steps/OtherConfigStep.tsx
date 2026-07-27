import { useTranslation } from 'react-i18next';
import { ArrowUpRight } from 'lucide-react';
import { Switch } from '../../../components/Switch';
import { OnboardingSection } from './OnboardingSection';
import {
  ONBOARDING_FEATURE_GROUPS,
  ONBOARDING_SEARCH_FEATURES,
  type OnboardingFeatureKey,
  type OnboardingFeatures,
  type OnboardingSearchKeys,
} from '../types';

interface OtherConfigStepProps {
  keys: OnboardingSearchKeys;
  onChange: (patch: Partial<OnboardingSearchKeys>) => void;
  features: OnboardingFeatures;
  onToggleFeature: (key: OnboardingFeatureKey, value: boolean) => void;
  onOpenOtherConfig: () => void;
}

const KEY_FIELDS: (keyof OnboardingSearchKeys)[] = [
  'jina_api_key',
  'bocha_api_key',
  'perplexity_api_key',
  'serper_api_key',
  'github_token',
];

export function OtherConfigStep({
  keys,
  onChange,
  features,
  onToggleFeature,
  onOpenOtherConfig,
}: OtherConfigStepProps) {
  const { t } = useTranslation();

  const renderToggle = (key: OnboardingFeatureKey) => (
    <div className="onboarding-toggle-row" key={key}>
      <div className="onboarding-toggle-row__body">
        <span className="onboarding-toggle-row__title">
          {t(`onboarding.other.features.items.${key}.label`)}
        </span>
        <span className="onboarding-toggle-row__text">
          {t(`onboarding.other.features.items.${key}.text`)}
        </span>
      </div>
      <Switch
        checked={features[key]}
        onChange={(v) => onToggleFeature(key, v)}
        title={t(`onboarding.other.features.items.${key}.label`)}
      />
    </div>
  );

  const keyCount = KEY_FIELDS.filter((k) => keys[k].trim()).length;
  const searchConfigured = ONBOARDING_SEARCH_FEATURES.some((k) => features[k]);

  return (
    <div className="onboarding-step">
      <p className="onboarding-step__desc">{t('onboarding.other.desc')}</p>

      {/* 第一类：第三方服务配置 */}
      <div className="onboarding-cat-label">{t('onboarding.other.thirdParty.title')}</div>

      <OnboardingSection
        title={t('onboarding.other.apiKeyTitle')}
        hint={t('onboarding.other.thirdParty.hint')}
        configured={keyCount > 0}
        summary={t('onboarding.other.configuredCount', { count: keyCount })}
      >
        <div className="onboarding-form">
          {KEY_FIELDS.map((key) => (
            <label className="onboarding-field" key={key}>
              <span className="onboarding-field__label">
                {t(`onboarding.other.fields.${key}.label`)}
              </span>
              <input
                type="password"
                className="onboarding-field__input"
                value={keys[key]}
                placeholder={t(`onboarding.other.fields.${key}.placeholder`)}
                autoComplete="new-password"
                onChange={(e) => onChange({ [key]: e.target.value })}
              />
              <span className="onboarding-field__hint">
                {t(`onboarding.other.fields.${key}.hint`)}
              </span>
            </label>
          ))}
        </div>
      </OnboardingSection>

      <OnboardingSection
        title={t('onboarding.other.freeSearch')}
        configured={searchConfigured}
        summary={t('onboarding.other.enabledSummary')}
      >
        <div className="onboarding-toggle-list">
          {ONBOARDING_SEARCH_FEATURES.map((key) => renderToggle(key))}
        </div>
      </OnboardingSection>

      {/* 第二类：高级功能配置 */}
      <div className="onboarding-cat-label">{t('onboarding.other.features.title')}</div>

      {ONBOARDING_FEATURE_GROUPS.map((group) => {
        const configured = group.keys.some((k) => features[k]);
        return (
          <OnboardingSection
            key={group.id}
            title={t(`onboarding.other.features.groups.${group.id}.label`)}
            hint={t(`onboarding.other.features.groups.${group.id}.hint`)}
            configured={configured}
            summary={t('onboarding.other.enabledSummary')}
          >
            <div className="onboarding-toggle-list">
              {group.keys.map((key) => renderToggle(key))}
            </div>
          </OnboardingSection>
        );
      })}

      <button type="button" className="onboarding-link" onClick={onOpenOtherConfig}>
        {t('onboarding.other.link')}
        <ArrowUpRight size={13} aria-hidden />
      </button>

      <p className="onboarding-step__note">{t('onboarding.other.note')}</p>
    </div>
  );
}
