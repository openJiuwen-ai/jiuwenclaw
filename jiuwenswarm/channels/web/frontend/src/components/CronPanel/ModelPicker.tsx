import { useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ChevronDown, Check } from 'lucide-react';
import { useClickOutside } from './useClickOutside';
import { useSessionStore } from '../../stores/sessionStore';
import { ModelProviderIcon } from '../ModelProviderIcon';

interface ModelPickerProps {
  value: string | null;
  onChange: (modelName: string) => void;
  disabled?: boolean;
}

// 参考会话界面的模型下拉框（components/ChatPanel/InputArea.tsx 的模型选择器），
// 复用同样的 ModelProviderIcon 厂商图标组件与 sessionStore.availableModels 数据源。
export default function ModelPicker({ value, onChange, disabled = false }: ModelPickerProps) {
  const { t } = useTranslation();
  const availableModels = useSessionStore((s) => s.availableModels);
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  useClickOutside(rootRef, open && !disabled, () => setOpen(false));
  const selected = availableModels.find((m) => m.model_name === value) ?? null;

  return (
    <div className="relative" ref={rootRef}>
      <button
        type="button"
        disabled={disabled}
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center justify-between rounded-md border border-border bg-card px-3 py-1.5 text-sm outline-none hover:border-border-strong disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:border-border"
      >
        {selected ? (
          <span className="inline-flex items-center gap-2 text-text">
            <ModelProviderIcon model={selected} />
            {selected.alias || selected.model_name}
          </span>
        ) : (
          <span className="text-text-muted">{t('cron.drawer.placeholderSelect')}</span>
        )}
        <ChevronDown size={14} className={`text-text-muted transition-transform ${open ? 'rotate-180' : ''}`} />
      </button>
      {open && !disabled && (
        <div className="absolute left-0 top-[calc(100%+4px)] z-30 max-h-60 w-full overflow-y-auto rounded-lg border border-border bg-card p-1.5 shadow-lg">
          {availableModels.length === 0 ? (
            <div className="px-2 py-2 text-xs text-text-muted">{t('cron.modelPicker.empty')}</div>
          ) : (
            <>
              <div className="px-2 py-1 text-xs text-text-muted">{t('cron.modelPicker.configured')}</div>
              {availableModels.map((m) => {
                const key = m.model_name;
                const active = key === value;
                return (
                  <button
                    key={key}
                    type="button"
                    onClick={() => {
                      onChange(m.model_name);
                      setOpen(false);
                    }}
                    className={`flex w-full items-center justify-between gap-2 rounded-md px-2 py-2 text-left text-sm transition-colors ${
                      active ? 'bg-bg-hover text-text' : 'text-text hover:bg-bg-hover'
                    }`}
                  >
                    <span className="inline-flex items-center gap-2">
                      <ModelProviderIcon model={m} />
                      {m.alias || m.model_name}
                    </span>
                    {active && <Check size={14} className="text-accent" />}
                  </button>
                );
              })}
            </>
          )}
        </div>
      )}
    </div>
  );
}
