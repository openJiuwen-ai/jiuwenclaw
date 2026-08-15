import { useTranslation } from 'react-i18next';
import { PartyPopper, Settings2, Compass } from 'lucide-react';
import type { OnboardingMode } from '../useOnboarding';

interface DoneStepProps {
  mode: OnboardingMode | null;
  dontShowAgain: boolean;
  onToggleDontShow: (value: boolean) => void;
  onOpenConfig: () => void;
}

export function DoneStep({ mode, dontShowAgain, onToggleDontShow, onOpenConfig }: DoneStepProps) {
  const { t } = useTranslation();

  return (
    <div className="onboarding-step onboarding-step--done">
      <span className="onboarding-done-icon" aria-hidden>
        <PartyPopper size={28} strokeWidth={2} />
      </span>
      <h3 className="onboarding-done-title">{t('onboarding.done.title')}</h3>
      <p className="onboarding-step__desc">
        {mode === 'minimal' ? t('onboarding.done.descMinimal') : t('onboarding.done.descClassic')}
      </p>

      <button type="button" className="onboarding-link onboarding-link--block" onClick={onOpenConfig}>
        <Settings2 size={14} aria-hidden />
        {t('onboarding.done.openConfig')}
      </button>

      <div className="onboarding-reopen-hint">
        <span className="onboarding-reopen-hint__icon">
          <Compass size={16} strokeWidth={2} aria-hidden />
        </span>
        <span className="onboarding-reopen-hint__text">{t('onboarding.done.reopenHint')}</span>
      </div>

      <label className="onboarding-checkbox">
        <input
          type="checkbox"
          checked={dontShowAgain}
          onChange={(e) => onToggleDontShow(e.target.checked)}
        />
        <span>{t('onboarding.done.dontShowAgain')}</span>
      </label>
    </div>
  );
}
