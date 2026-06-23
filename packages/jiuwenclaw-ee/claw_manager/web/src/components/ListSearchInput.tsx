type ListSearchInputProps = {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  className?: string;
};

export function ListSearchInput({
  value,
  onChange,
  placeholder,
  className = 'input !w-[38rem]',
}: ListSearchInputProps) {
  return (
    <input
      className={className}
      placeholder={placeholder}
      value={value}
      onChange={(e) => onChange(e.target.value)}
    />
  );
}
