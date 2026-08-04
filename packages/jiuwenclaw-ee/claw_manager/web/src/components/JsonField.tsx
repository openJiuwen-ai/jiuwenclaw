import { useTranslation } from 'react-i18next';
import { isExamplePrefixed, jsonContentForValidation } from '../utils/jsonExample';

interface JsonFieldProps {
  label: string;
  hint?: string;
  value: string;
  onChange: (val: string) => void;
  placeholder?: string;
  rows?: number;
}

export function JsonField({ label, hint, value, onChange, placeholder, rows = 6 }: JsonFieldProps) {
  const { t } = useTranslation();
  const jsonText = jsonContentForValidation(value);
  let error: string | null = null;
  if (jsonText) {
    try {
      JSON.parse(jsonText);
    } catch (e) {
      error = (e as Error).message;
    }
  }
  const showExampleTag = isExamplePrefixed(value);

  return (
    <div>
      <div className="flex items-center justify-between gap-2 mb-1 flex-wrap">
        <div className="flex items-center gap-2 min-w-0">
          <label className="label !mb-0">{label}</label>
          {showExampleTag && (
            <span className="pill sm muted shrink-0">{t('instanceConfig.permissions.exampleTag')}</span>
          )}
        </div>
        {hint && <span className="text-[11px] text-muted">{hint}</span>}
      </div>
      <textarea
        className={`textarea mono text-xs ${error ? '!border-danger' : ''}`}
        rows={rows}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        spellCheck={false}
      />
      {error && <div className="text-[11px] text-danger mt-1">{error}</div>}
    </div>
  );
}

export function tryParseJson<T = unknown>(text: string, fallback: T): T {
  const v = text.trim();
  if (!v) return fallback;
  try {
    return JSON.parse(v) as T;
  } catch {
    return fallback;
  }
}

export function useInvalidJsonChecker() {
  const { t } = useTranslation();
  return (text: string) => {
    const v = jsonContentForValidation(text);
    if (!v) return null;
    try {
      JSON.parse(v);
      return null;
    } catch (e) {
      return t('errors.invalidJson', { detail: (e as Error).message });
    }
  };
}
