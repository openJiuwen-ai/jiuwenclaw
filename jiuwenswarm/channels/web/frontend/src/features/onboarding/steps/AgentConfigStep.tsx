import { useTranslation } from 'react-i18next';
import { Bot, Users, ArrowUpRight } from 'lucide-react';

interface AgentConfigStepProps {
  onOpenAgentConfig: () => void;
}

export function AgentConfigStep({ onOpenAgentConfig }: AgentConfigStepProps) {
  const { t } = useTranslation();

  return (
    <div className="onboarding-step">
      <p className="onboarding-step__desc">{t('onboarding.agent.desc')}</p>

      <div className="onboarding-info-list">
        <div className="onboarding-info-card">
          <span className="onboarding-info-card__icon">
            <Bot size={18} strokeWidth={2} aria-hidden />
          </span>
          <div className="onboarding-info-card__body">
            <div className="onboarding-info-card__title">{t('onboarding.agent.agent.title')}</div>
            <div className="onboarding-info-card__text">{t('onboarding.agent.agent.text')}</div>
          </div>
        </div>

        <div className="onboarding-info-card">
          <span className="onboarding-info-card__icon">
            <Users size={18} strokeWidth={2} aria-hidden />
          </span>
          <div className="onboarding-info-card__body">
            <div className="onboarding-info-card__title">{t('onboarding.agent.team.title')}</div>
            <div className="onboarding-info-card__text">{t('onboarding.agent.team.text')}</div>
          </div>
        </div>
      </div>

      <button type="button" className="onboarding-link" onClick={onOpenAgentConfig}>
        {t('onboarding.agent.link')}
        <ArrowUpRight size={13} aria-hidden />
      </button>

      <p className="onboarding-step__note">{t('onboarding.agent.note')}</p>
    </div>
  );
}
