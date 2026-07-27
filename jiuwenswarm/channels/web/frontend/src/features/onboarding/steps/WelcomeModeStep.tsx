import { useTranslation } from 'react-i18next';
import { Rocket, Compass, Check } from 'lucide-react';
import type { OnboardingMode } from '../useOnboarding';

interface WelcomeModeStepProps {
  mode: OnboardingMode | null;
  onSelectMode: (mode: OnboardingMode) => void;
}

const MODE_META: {
  id: OnboardingMode;
  icon: typeof Rocket;
}[] = [
  { id: 'minimal', icon: Rocket },
  { id: 'classic', icon: Compass },
];

export function WelcomeModeStep({ mode, onSelectMode }: WelcomeModeStepProps) {
  const { t } = useTranslation();

  return (
    <div className="onboarding-step">
      <p className="onboarding-step__desc">{t('onboarding.welcome.desc')}</p>

      <div className="onboarding-mode-grid">
        {MODE_META.map(({ id, icon: Icon }) => {
          const selected = mode === id;
          return (
            <button
              type="button"
              key={id}
              onClick={() => onSelectMode(id)}
              aria-pressed={selected}
              className={`onboarding-mode-card${selected ? ' onboarding-mode-card--selected' : ''}`}
            >
              <span className="onboarding-mode-card__icon">
                <Icon size={20} strokeWidth={2} aria-hidden />
              </span>
              <span className="onboarding-mode-card__body">
                <span className="onboarding-mode-card__title">
                  {t(`onboarding.welcome.${id}.title`)}
                </span>
                <span className="onboarding-mode-card__desc">
                  {t(`onboarding.welcome.${id}.desc`)}
                </span>
              </span>
              {selected && (
                <span className="onboarding-mode-card__check" aria-hidden>
                  <Check size={14} strokeWidth={3} />
                </span>
              )}
            </button>
          );
        })}
      </div>
    </div>
  );
}
