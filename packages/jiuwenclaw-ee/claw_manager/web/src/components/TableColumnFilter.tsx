import { useEffect, useRef, useState } from 'react';

export type ColumnFilterOption = {
  value: string;
  label: string;
};

type TableColumnFilterProps = {
  label: string;
  value: string;
  options: ColumnFilterOption[];
  onChange: (value: string) => void;
};

export function TableColumnFilter({ label, value, options, onChange }: TableColumnFilterProps) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const active = value !== '';

  useEffect(() => {
    if (!open) return;
    const onDocClick = (e: MouseEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener('mousedown', onDocClick);
    return () => document.removeEventListener('mousedown', onDocClick);
  }, [open]);

  return (
    <div className="th-filter" ref={rootRef}>
      <span className="th-filter__label">{label}</span>
      <button
        type="button"
        className={`th-filter__btn${active ? ' active' : ''}${open ? ' open' : ''}`}
        aria-label={label}
        aria-expanded={open}
        onClick={() => setOpen((prev) => !prev)}
      >
        <svg className="th-filter__icon" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}>
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            d="M3 4.5h18M7 9.75h10M10.5 15h3"
          />
        </svg>
      </button>
      {open && (
        <div className="th-filter__menu" role="menu">
          {options.map((opt) => (
            <button
              key={opt.value || '__all__'}
              type="button"
              role="menuitemradio"
              aria-checked={value === opt.value}
              className={`th-filter__item${value === opt.value ? ' selected' : ''}`}
              onClick={() => {
                onChange(opt.value);
                setOpen(false);
              }}
            >
              {opt.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
