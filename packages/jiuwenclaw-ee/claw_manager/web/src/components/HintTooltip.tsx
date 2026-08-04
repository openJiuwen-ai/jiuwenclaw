import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';

type HintTooltipProps = {
  text: string;
  className?: string;
};

const HIDE_DELAY_MS = 100;

export function HintTooltip({ text, className }: HintTooltipProps) {
  const [anchor, setAnchor] = useState<DOMRect | null>(null);
  const [open, setOpen] = useState(false);
  const hideTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const clearHideTimer = () => {
    if (hideTimerRef.current !== null) {
      clearTimeout(hideTimerRef.current);
      hideTimerRef.current = null;
    }
  };

  const show = (rect: DOMRect) => {
    clearHideTimer();
    setAnchor(rect);
    setOpen(true);
  };

  const scheduleHide = () => {
    clearHideTimer();
    hideTimerRef.current = setTimeout(() => {
      setOpen(false);
      setAnchor(null);
      hideTimerRef.current = null;
    }, HIDE_DELAY_MS);
  };

  useEffect(() => () => clearHideTimer(), []);

  const tooltip =
    open && anchor ? (
      <div
        className="fixed z-[200] max-w-[18rem] rounded-md border border-[var(--border)] bg-[var(--card)] px-2.5 py-2 shadow-lg"
        style={{
          left: Math.min(
            Math.max(8, anchor.left + anchor.width / 2 - 144),
            window.innerWidth - 296,
          ),
          top: anchor.bottom + 6,
        }}
        role="tooltip"
        onMouseEnter={clearHideTimer}
        onMouseLeave={scheduleHide}
      >
        <p className="text-[11px] leading-snug text-muted m-0">{text}</p>
      </div>
    ) : null;

  return (
    <>
      <button
        type="button"
        className={`inline-flex h-4 w-4 shrink-0 items-center justify-center rounded-full border border-border text-[10px] font-medium leading-none text-muted hover:text-text hover:border-text-muted cursor-help ${className ?? ''}`}
        aria-label={text}
        onMouseEnter={(e) => show(e.currentTarget.getBoundingClientRect())}
        onMouseLeave={scheduleHide}
        onFocus={(e) => show(e.currentTarget.getBoundingClientRect())}
        onBlur={scheduleHide}
      >
        ?
      </button>
      {tooltip ? createPortal(tooltip, document.body) : null}
    </>
  );
}
