/**
 * RSI 空状态：无实验时右侧居中占位。
 */
interface RsiEmptyStateProps {
  onCreate: () => void;
  text: string;
  hint?: string;
  buttonText?: string;
}

export function RsiEmptyState({ onCreate, text, hint, buttonText }: RsiEmptyStateProps) {
  return (
    <div className="rsi-empty">
      <div className="rsi-empty__card">
        <svg
          className="rsi-empty__icon"
          width="80"
          height="80"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth={1}
          aria-hidden="true"
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            d="M8 2v4M16 2v4M3 7h18M5 5h14a1 1 0 0 1 1 1v14a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1z"
          />
          <path strokeLinecap="round" strokeLinejoin="round" d="M9 14l2 2 4-4" opacity="0.5" />
        </svg>
        <div className="rsi-empty__title">{text}</div>
        {hint && <div className="rsi-empty__hint">{hint}</div>}
        <button type="button" className="rsi-btn rsi-btn--primary rsi-empty__btn" onClick={onCreate}>
          {buttonText ?? '创建实验'}
        </button>
      </div>
    </div>
  );
}
