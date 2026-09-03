import { useCallback, useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react';
import { createPortal } from 'react-dom';

const VIEWPORT_MARGIN = 8;
const TOOLTIP_GAP = 6;

type TooltipState = {
  text: string;
  top: number;
  buttonLeft: number;
  buttonRight: number;
};

type TooltipHandlers = {
  onMouseEnter: (event: { currentTarget: EventTarget | null }) => void;
  onMouseLeave: () => void;
  onFocus: (event: { currentTarget: EventTarget | null }) => void;
  onBlur: () => void;
};

/**
 * data-tooltip 的自适应定位方案：默认水平居中于触发按钮下方，
 * 右侧空间不足时提示右缘对齐按钮右缘，左侧空间不足时左缘对齐按钮左缘，
 * 仍放不下时收进视口内（VIEWPORT_MARGIN 兜底）。
 *
 * 用法：
 *   const { tooltip, handlers } = useAdaptiveTooltip();
 *   <button data-tooltip="提示" {...handlers}>...</button>
 *   {tooltip}
 */
export function useAdaptiveTooltip(): { tooltip: ReactNode; handlers: TooltipHandlers } {
  const [state, setState] = useState<TooltipState | null>(null);
  const [left, setLeft] = useState<number | null>(null);
  const tooltipRef = useRef<HTMLDivElement>(null);

  const show = useCallback((event: { currentTarget: EventTarget | null }) => {
    const el = event.currentTarget as HTMLElement | null;
    const text = el?.getAttribute('data-tooltip') ?? '';
    if (!el || !text) return;
    const rect = el.getBoundingClientRect();
    setLeft(null);
    setState({ text, top: rect.bottom + TOOLTIP_GAP, buttonLeft: rect.left, buttonRight: rect.right });
  }, []);

  const hide = useCallback(() => setState(null), []);

  // 测量真实宽度后计算水平位置：空间够则居中，不够则贴边/收进视口
  useLayoutEffect(() => {
    if (!state) {
      setLeft(null);
      return;
    }
    const el = tooltipRef.current;
    if (!el) return;
    const width = el.offsetWidth;
    const centered = state.buttonLeft + (state.buttonRight - state.buttonLeft) / 2 - width / 2;
    const maxLeft = window.innerWidth - VIEWPORT_MARGIN - width;
    if (centered < VIEWPORT_MARGIN) {
      setLeft(Math.max(VIEWPORT_MARGIN, Math.min(state.buttonLeft, maxLeft)));
    } else if (centered > maxLeft) {
      setLeft(Math.min(Math.max(state.buttonRight - width, VIEWPORT_MARGIN), maxLeft));
    } else {
      setLeft(centered);
    }
  }, [state]);

  // 滚动/缩放后锚点位置失效，直接隐藏，避免提示悬在错误位置
  useEffect(() => {
    if (!state) return;
    const hideTooltip = () => setState(null);
    window.addEventListener('resize', hideTooltip);
    window.addEventListener('scroll', hideTooltip, true);
    return () => {
      window.removeEventListener('resize', hideTooltip);
      window.removeEventListener('scroll', hideTooltip, true);
    };
  }, [state]);

  const tooltip = state
    ? createPortal(
        <div
          ref={tooltipRef}
          className="adaptive-tooltip"
          style={{
            position: 'fixed',
            top: state.top,
            left: left ?? -9999,
            visibility: left === null ? 'hidden' : 'visible',
            zIndex: 10000,
          }}
          role="tooltip"
        >
          {state.text}
        </div>,
        document.body
      )
    : null;

  const handlers: TooltipHandlers = {
    onMouseEnter: show,
    onMouseLeave: hide,
    onFocus: show,
    onBlur: hide,
  };

  return { tooltip, handlers };
}
