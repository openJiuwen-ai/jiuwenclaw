import type { ReactNode } from 'react';

export interface CategoryTabsOption<T extends string = string> {
  value: T;
  label: ReactNode;
}

export interface CategoryTabsProps<T extends string = string> {
  items: CategoryTabsOption<T>[];
  value: T;
  onChange: (value: T) => void;
}

export function CategoryTabs<T extends string = string>({ items, value, onChange }: CategoryTabsProps<T>) {
  return (
    <div className="flex items-center text-[16px]">
      {items.map((item, idx) => (
        <span key={item.value} className="flex items-center">
          {idx > 0 && <span className="inline-flex items-center h-4 text-text-divider px-4">|</span>}
          <button
            type="button"
            onClick={() => onChange(item.value)}
            className={`whitespace-nowrap ${
              value === item.value
                ? 'text-text font-bold'
                : 'text-text-weak hover:text-text'
            }`}
          >
            {item.label}
          </button>
        </span>
      ))}
    </div>
  );
}
