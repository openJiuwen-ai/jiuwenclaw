import { useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { ChevronDown, CheckCircle2 } from 'lucide-react';

interface OnboardingSectionProps {
  title: string;
  hint?: string;
  /** 是否已配置：显示「已配置」徽标，并默认折叠。 */
  configured?: boolean;
  /** 折叠态下展示的摘要（如已配置的模型名 / 已填项数量）。 */
  summary?: string;
  defaultOpen?: boolean;
  children: ReactNode;
}

export function OnboardingSection({
  title,
  hint,
  configured = false,
  summary,
  defaultOpen,
  children,
}: OnboardingSectionProps) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(defaultOpen ?? !configured);

  return (
    <section className={`onboarding-section${open ? ' is-open' : ''}`}>
      <button
        type="button"
        className="onboarding-section__head"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
      >
        <ChevronDown size={16} className="onboarding-section__chevron" aria-hidden />
        <span className="onboarding-section__titles">
          <span className="onboarding-section__title">{title}</span>
          {!open && summary ? (
            <span className="onboarding-section__summary">{summary}</span>
          ) : (
            hint && <span className="onboarding-section__hint">{hint}</span>
          )}
        </span>
        {configured && (
          <span className="onboarding-badge onboarding-badge--configured">
            <CheckCircle2 size={12} aria-hidden />
            {t('onboarding.badges.configured')}
          </span>
        )}
      </button>
      {open && <div className="onboarding-section__body">{children}</div>}
    </section>
  );
}
