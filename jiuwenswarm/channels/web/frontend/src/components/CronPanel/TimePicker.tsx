import { useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Clock } from 'lucide-react';
import { useClickOutside } from './useClickOutside';

const HOURS = Array.from({ length: 24 }, (_, i) => String(i).padStart(2, '0'));
const MINUTES = Array.from({ length: 60 }, (_, i) => String(i).padStart(2, '0'));

interface TimePickerProps {
  value: string; // "HH:MM"，空字符串表示未选择
  onChange: (v: string) => void;
  placeholder?: string;
  className?: string;
  /** 下拉弹层对齐方向：触发按钮靠近容器右边缘时用 'right'，避免弹层溢出 */
  align?: 'left' | 'right';
}

// 原生 <input type="time"> 会自带一个浏览器时钟图标，和自绘的 Clock 图标叠在一起变成"两个时钟"，
// 改成完全自绘的 时/分 两列选择器。宽度由调用方通过 className 控制。
export default function TimePicker({ value, onChange, placeholder, className = 'w-32 shrink-0', align = 'left' }: TimePickerProps) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  useClickOutside(rootRef, open, () => setOpen(false));

  const [h, m] = value ? value.split(':') : ['', ''];

  function pick(hour: string, minute: string) {
    onChange(`${hour}:${minute}`);
  }

  return (
    <div className={`relative ${className}`} ref={rootRef}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full flex-nowrap items-center justify-between gap-1 rounded-md border border-border bg-card px-3 py-1.5 text-sm outline-none hover:border-border-strong"
      >
        <span className={`truncate ${value ? 'text-text' : 'text-text-muted'}`}>{value || placeholder}</span>
        <Clock size={15} className="shrink-0 text-text-muted" />
      </button>
      {open && (
        <div className={`absolute top-[calc(100%+4px)] z-30 flex w-40 overflow-hidden rounded-lg border border-border bg-card shadow-lg ${align === 'right' ? 'right-0' : 'left-0'}`}>
          <div className="flex-1 max-h-52 overflow-y-auto border-r border-border">
            <div className="sticky top-0 bg-card px-3 py-1.5 text-xs text-text-muted">{t('cron.timePicker.hour')}</div>
            {HOURS.map((hh) => (
              <button
                key={hh}
                type="button"
                onClick={() => pick(hh, m || '00')}
                className={`block w-full px-3 py-1.5 text-left text-sm ${hh === h ? 'bg-bg-hover text-text' : 'text-text hover:bg-bg-hover'}`}
              >
                {hh}
              </button>
            ))}
          </div>
          <div className="flex-1 max-h-52 overflow-y-auto">
            <div className="sticky top-0 bg-card px-3 py-1.5 text-xs text-text-muted">{t('cron.timePicker.minute')}</div>
            {MINUTES.map((mm) => (
              <button
                key={mm}
                type="button"
                onClick={() => pick(h || '00', mm)}
                className={`block w-full px-3 py-1.5 text-left text-sm ${mm === m ? 'bg-bg-hover text-text' : 'text-text hover:bg-bg-hover'}`}
              >
                {mm}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
