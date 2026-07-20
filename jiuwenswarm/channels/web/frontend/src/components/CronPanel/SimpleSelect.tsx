import { useRef, useState } from 'react';
import { ChevronDown, Check } from 'lucide-react';
import { useClickOutside } from './useClickOutside';

interface SimpleSelectOption {
  value: string;
  label: string;
  disabled?: boolean;
}

interface SimpleSelectProps {
  value: string;
  onChange: (v: string) => void;
  options: SimpleSelectOption[];
  placeholder?: string;
  className?: string;
  disabled?: boolean;
}

// 原生 <select> 的浏览器默认下拉箭头样式不统一也不好看，参考 ModelPicker 的自绘下拉结构，
// 做一个通用版本给 项目/时区 等纯文本选项复用。
export default function SimpleSelect({ value, onChange, options, placeholder = '', className = 'w-full', disabled = false }: SimpleSelectProps) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  useClickOutside(rootRef, open && !disabled, () => setOpen(false));
  const selected = options.find((o) => o.value === value) ?? null;

  return (
    <div className={`relative ${className}`} ref={rootRef}>
      <button
        type="button"
        disabled={disabled}
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center justify-between rounded-md border border-border bg-card px-3 py-1.5 text-sm outline-none hover:border-border-strong disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:border-border"
      >
        <span className={selected ? 'text-text' : 'text-text-muted'}>{selected ? selected.label : placeholder}</span>
        <ChevronDown size={14} className={`text-text-muted transition-transform ${open ? 'rotate-180' : ''}`} />
      </button>
      {open && !disabled && (
        <div className="absolute left-0 top-[calc(100%+4px)] z-30 max-h-60 w-full overflow-y-auto rounded-lg border border-border bg-card p-1.5 shadow-lg">
          {options.map((opt) => {
            const active = opt.value === value;
            return (
              <button
                key={opt.value}
                type="button"
                disabled={opt.disabled}
                onClick={() => {
                  if (opt.disabled) return;
                  onChange(opt.value);
                  setOpen(false);
                }}
                className={`flex w-full items-center justify-between gap-2 rounded-md px-2 py-2 text-left text-sm transition-colors ${
                  opt.disabled
                    ? 'cursor-not-allowed text-text-muted'
                    : active
                      ? 'bg-bg-hover text-text'
                      : 'text-text hover:bg-bg-hover'
                }`}
              >
                {opt.label}
                {active && !opt.disabled && <Check size={14} className="text-accent" />}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
