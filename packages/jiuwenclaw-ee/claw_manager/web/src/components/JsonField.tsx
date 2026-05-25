import { useTranslation } from 'react-i18next';

interface JsonFieldProps {
  label: string;
  hint?: string;
  value: string;
  onChange: (val: string) => void;
  placeholder?: string;
  rows?: number;
}

export function JsonField({ label, hint, value, onChange, placeholder, rows = 6 }: JsonFieldProps) {
  let error: string | null = null;
  const v = value.trim();
  if (v) {
    try {
      JSON.parse(v);
    } catch (e) {
      error = (e as Error).message;
    }
  }
  return (
    <div>
      <div className="flex items-center justify-between mb-1">
        <label className="label !mb-0">{label}</label>
        {hint && <span className="text-[11px] text-muted">{hint}</span>}
      </div>
      <textarea
        className={`textarea ${error ? '!border-danger' : ''}`}
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
    if (!text.trim()) return null;
    try {
      JSON.parse(text);
      return null;
    } catch (e) {
      return t('errors.invalidJson', { detail: (e as Error).message });
    }
  };
}
