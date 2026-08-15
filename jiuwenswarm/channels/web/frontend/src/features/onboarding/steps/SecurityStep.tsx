import { useTranslation } from 'react-i18next';
import { ShieldCheck, ArrowUpRight, CheckCircle2 } from 'lucide-react';
import { Switch } from '../../../components/Switch';

interface SecurityStepProps {
  enabled: boolean;
  onChange: (enabled: boolean) => void;
  onOpenSecurityConfig: () => void;
}

export function SecurityStep({ enabled, onChange, onOpenSecurityConfig }: SecurityStepProps) {
  const { t } = useTranslation();

  return (
    <div className="onboarding-step">
      <p className="onboarding-step__desc">{t('onboarding.security.desc')}</p>

      <div className="onboarding-toggle-card">
        <span className="onboarding-toggle-card__icon">
          <ShieldCheck size={18} strokeWidth={2} aria-hidden />
        </span>
        <div className="onboarding-toggle-card__body">
          <div className="onboarding-toggle-card__title-row">
            <span className="onboarding-toggle-card__title">{t('onboarding.security.toggleTitle')}</span>
            {enabled && (
              <span className="onboarding-badge onboarding-badge--configured">
                <CheckCircle2 size={12} aria-hidden />
                {t('onboarding.badges.configured')}
              </span>
            )}
          </div>
          <div className="onboarding-toggle-card__text">{t('onboarding.security.toggleText')}</div>
        </div>
        <Switch checked={enabled} onChange={onChange} title={t('onboarding.security.toggleTitle')} />
      </div>

      <button type="button" className="onboarding-link" onClick={onOpenSecurityConfig}>
        {t('onboarding.security.link')}
        <ArrowUpRight size={13} aria-hidden />
      </button>

      <p className="onboarding-step__note">
        {enabled ? t('onboarding.security.noteEnabled') : t('onboarding.security.noteDisabled')}
      </p>
    </div>
  );
}
