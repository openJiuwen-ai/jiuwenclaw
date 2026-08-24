import { useId } from 'react';
import './RadioGroup.css';

export type RadioOption = { value: string; label: string };
export function RadioGroup({
  value,
  options,
  disabled,
  'aria-label': ariaLabel,
  onChange,
}: {
  value: string;
  options: readonly RadioOption[];
  disabled?: boolean;
  'aria-label': string;
  onChange: (value: string) => void;
}) {
  const name = useId();
  return (
    <div className="ui-radio-group" role="radiogroup" aria-label={ariaLabel}>
      {options.map((option) => (
        <label key={option.value} className="ui-radio-group__option">
          <input
            type="radio"
            name={name}
            value={option.value}
            checked={value === option.value}
            disabled={disabled}
            onChange={() => onChange(option.value)}
          />
          <span>{option.label}</span>
        </label>
      ))}
    </div>
  );
}
