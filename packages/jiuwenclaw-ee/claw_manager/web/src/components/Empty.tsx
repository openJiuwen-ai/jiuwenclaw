interface EmptyProps {
  text: string;
  icon?: React.ReactNode;
}

export function Empty({ text, icon }: EmptyProps) {
  return (
    <div className="flex flex-col items-center justify-center text-muted py-10 gap-3">
      <div className="opacity-60">
        {icon ?? (
          <svg className="w-10 h-10" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M9 17h6m-3-3v3M7 8h10M5 5h14a2 2 0 012 2v10a2 2 0 01-2 2H5a2 2 0 01-2-2V7a2 2 0 012-2z" />
          </svg>
        )}
      </div>
      <div className="text-xs">{text}</div>
    </div>
  );
}
