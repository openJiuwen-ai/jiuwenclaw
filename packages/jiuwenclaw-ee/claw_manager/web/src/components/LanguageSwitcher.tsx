import { useTranslation } from 'react-i18next';

export function LanguageSwitcher() {
  const { i18n } = useTranslation();
  const isZh = i18n.language.startsWith('zh');

  return (
    <div className="flex items-center gap-1 rounded-lg bg-secondary/60 px-2 py-1">
      <button
        type="button"
        onClick={() => i18n.changeLanguage('zh')}
        className={`text-xs px-2 py-1 rounded ${isZh ? 'bg-accent text-white font-medium' : 'text-muted hover:text-text'}`}
      >
        中
      </button>
      <button
        type="button"
        onClick={() => i18n.changeLanguage('en')}
        className={`text-xs px-2 py-1 rounded ${!isZh ? 'bg-accent text-white font-medium' : 'text-muted hover:text-text'}`}
      >
        En
      </button>
    </div>
  );
}
