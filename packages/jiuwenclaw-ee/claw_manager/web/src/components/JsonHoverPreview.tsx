import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { safeStringify, truncate } from '../utils/format';

interface JsonHoverPreviewProps {
  value: unknown;
  previewMax?: number;
}

const HIDE_DELAY_MS = 200;

export function JsonHoverPreview({ value, previewMax = 120 }: JsonHoverPreviewProps) {
  const [anchor, setAnchor] = useState<DOMRect | null>(null);
  const [open, setOpen] = useState(false);
  const hideTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const compact = safeStringify(value, 0);
  const pretty = safeStringify(value, 2);

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

  if (!compact) {
    return <span className="text-[11px] text-muted">—</span>;
  }

  const tooltip =
    open && anchor && pretty ? (
      <div
        className="fixed z-[200] max-w-[32rem] max-h-72 overflow-auto rounded-md border border-[var(--border)] bg-[var(--card)] p-3 shadow-lg"
        style={{
          left: Math.min(anchor.left, window.innerWidth - 520),
          top: anchor.bottom + 2,
        }}
        role="tooltip"
        onMouseEnter={() => {
          clearHideTimer();
          setOpen(true);
        }}
        onMouseLeave={scheduleHide}
      >
        <pre className="mono text-[11px] leading-relaxed whitespace-pre-wrap break-all m-0 text-text">
          {pretty}
        </pre>
      </div>
    ) : null;

  return (
    <>
      <span
        className="mono text-[11px] text-muted line-clamp-2 break-all cursor-default"
        onMouseEnter={(e) => show(e.currentTarget.getBoundingClientRect())}
        onMouseLeave={scheduleHide}
      >
        {truncate(compact, previewMax)}
      </span>
      {tooltip ? createPortal(tooltip, document.body) : null}
    </>
  );
}
