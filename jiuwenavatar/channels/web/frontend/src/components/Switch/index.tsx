interface SwitchProps {
  checked: boolean;
  onChange: (checked: boolean) => void;
  disabled?: boolean;
  title?: string;
}

export function Switch({ checked, onChange, disabled = false, title }: SwitchProps) {
  const handleClick = (e: React.MouseEvent) => {
    e.stopPropagation();
    onChange(!checked);
  };

  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      disabled={disabled}
      onClick={handleClick}
      title={title}
      className={`
        relative inline-flex h-5 w-9 flex-shrink-0 cursor-pointer rounded-full 
        border-2 transition-colors duration-200 ease-in-out
        focus:outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2
        ${checked 
          ? 'bg-accent border-accent' 
          : 'bg-gray-200 dark:bg-gray-600 border-gray-300 dark:border-gray-500'}
        ${disabled ? 'opacity-50 cursor-not-allowed' : ''}
      `}
    >
      <span
        className={`
          pointer-events-none inline-block h-4 w-4 transform rounded-full bg-white shadow
          transition duration-200 ease-in-out
          ${checked ? 'translate-x-4' : 'translate-x-0.5'}
        `}
      />
    </button>
  );
}
