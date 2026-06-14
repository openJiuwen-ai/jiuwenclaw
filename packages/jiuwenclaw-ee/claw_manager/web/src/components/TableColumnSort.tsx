import { useEffect, useRef, useState } from 'react';

export type ColumnSortValue = '' | 'asc' | 'desc';

export type ColumnSortOption = {
  value: ColumnSortValue;
  label: string;
};

type TableColumnSortProps = {
  label: string;
  value: ColumnSortValue;
  options: ColumnSortOption[];
  onChange: (value: ColumnSortValue) => void;
  /** 仅展示排序图标，不展示 label 文本（仍用于 aria-label） */
  iconOnly?: boolean;
};

export function TableColumnSort({
  label,
  value,
  options,
  onChange,
  iconOnly = false,
}: TableColumnSortProps) {
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
      {!iconOnly && <span className="th-filter__label">{label}</span>}
      <button
        type="button"
        className={`th-filter__btn${active ? ' active' : ''}${open ? ' open' : ''}`}
        aria-label={label}
        aria-expanded={open}
        onClick={() => setOpen((prev) => !prev)}
      >
        <svg className="th-filter__icon" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}>
          {value === 'desc' ? (
            <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
          ) : (
            <path strokeLinecap="round" strokeLinejoin="round" d="M5 15l7-7 7 7" />
          )}
        </svg>
      </button>
      {open && (
        <div className="th-filter__menu" role="menu">
          {options.map((opt) => (
            <button
              key={opt.value || '__default__'}
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
