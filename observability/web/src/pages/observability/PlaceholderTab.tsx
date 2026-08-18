import { useTranslation } from 'react-i18next';

interface Props {
  label: string;
}

export function PlaceholderTab({ label }: Props) {
  const { t } = useTranslation();
  return (
    <div className="card p-8 text-center text-muted">
      <div className="text-base font-semibold mb-1">{label}</div>
      <div className="text-sm">{t('observability.placeholderHint')}</div>
    </div>
  );
}
