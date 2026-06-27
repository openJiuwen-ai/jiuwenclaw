const BASE_CLASS_NAME = 'input min-w-[10rem] w-full max-w-[38rem]';

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
  className,
}: ListSearchInputProps) {
  return (
    <input
      className={className ? `${BASE_CLASS_NAME} ${className}` : BASE_CLASS_NAME}
      placeholder={placeholder}
      value={value}
      onChange={(e) => onChange(e.target.value)}
    />
  );
}
