import { useEffect, useLayoutEffect, useMemo, useRef, useState, type CSSProperties, type RefObject } from 'react';
import { breakpoints, canFitBoth, canFitToolPanelOnly, type BreakpointKey } from '../styles/breakpoints';

/* ── 基础：通用媒体查询 ── */

export function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(() => {
    if (typeof window === 'undefined') return false;
    return window.matchMedia(query).matches;
  });

  useEffect(() => {
    const mql = window.matchMedia(query);
    const handler = (e: MediaQueryListEvent) => setMatches(e.matches);
    setMatches(mql.matches);
    mql.addEventListener('change', handler);
    return () => mql.removeEventListener('change', handler);
  }, [query]);

  return matches;
}

export function useMaxWidth(key: BreakpointKey): boolean {
  return useMediaQuery(`(max-width: ${breakpoints[key]}px)`);
}

export function useMinWidth(key: BreakpointKey): boolean {
  return useMediaQuery(`(min-width: ${breakpoints[key]}px)`);
}

/* ── 业务：侧边栏 / 工具面板布局状态 ── */

export function useResponsiveLayout() {
  const isMobile = useMaxWidth('sm');
  const [conversationSidebarCollapsed, setConversationSidebarCollapsed] = useState(false);
  const [conversationSidebarFloating, setConversationSidebarFloating] = useState(false);
  const [toolPanelHidden, setToolPanelHidden] = useState(false);

  useEffect(() => {
    if (isMobile) {
      setConversationSidebarCollapsed(true);
      setConversationSidebarFloating(true);
    } else {
      setConversationSidebarFloating(false);
    }
  }, [isMobile]);

  return {
    isMobile,
    conversationSidebarCollapsed,
    setConversationSidebarCollapsed,
    conversationSidebarFloating,
    setConversationSidebarFloating,
    toolPanelHidden,
    setToolPanelHidden,
  };
}

/* ── 业务：面板互斥 / 全屏判定 ── */

export interface ResponsivePanelResizeParams {
  isTeamAreaExpanded: boolean;
  conversationSidebarCollapsed: boolean;
  setConversationSidebarCollapsed: (collapsed: boolean) => void;
  setSingleAgentPanelExpanded: (expanded: boolean) => void;
  setTeamAreaExpanded: (expanded: boolean) => void;
  mode: string;
}

export function useResponsivePanelResize({
  isTeamAreaExpanded,
  conversationSidebarCollapsed,
  setConversationSidebarCollapsed,
  setSingleAgentPanelExpanded,
  setTeamAreaExpanded,
  mode,
}: ResponsivePanelResizeParams) {
  const [shouldFullscreen, setShouldFullscreen] = useState(false);
  const stateRef = useRef({ isTeamAreaExpanded, conversationSidebarCollapsed, mode });
  stateRef.current = { isTeamAreaExpanded, conversationSidebarCollapsed, mode };
  const prevRef = useRef({ isTeamAreaExpanded, conversationSidebarCollapsed });

  useLayoutEffect(() => {
    if (!isTeamAreaExpanded) {
      setShouldFullscreen(false);
      prevRef.current = { isTeamAreaExpanded, conversationSidebarCollapsed };
      return;
    }

    const prev = prevRef.current;
    const justExpanded = isTeamAreaExpanded && !prev.isTeamAreaExpanded;
    prevRef.current = { isTeamAreaExpanded, conversationSidebarCollapsed };

    const check = (isUserAction: boolean) => {
      const s = stateRef.current;
      if (!canFitToolPanelOnly()) {
        if (isUserAction) {
          setShouldFullscreen(true);
        } else if (s.isTeamAreaExpanded && !shouldFullscreen) {
          if (s.mode === 'team') {
            setTeamAreaExpanded(false);
          } else {
            setSingleAgentPanelExpanded(false);
          }
        }
        return;
      }
      setShouldFullscreen(false);
      if (!canFitBoth() && !s.conversationSidebarCollapsed) {
        setConversationSidebarCollapsed(true);
      }
    };

    check(justExpanded);
    const onResize = () => check(false);
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, [isTeamAreaExpanded, shouldFullscreen, setConversationSidebarCollapsed, setSingleAgentPanelExpanded, setTeamAreaExpanded]);

  useEffect(() => {
    if (shouldFullscreen) {
      prevRef.current = { isTeamAreaExpanded, conversationSidebarCollapsed };
      return;
    }
    if (canFitBoth()) {
      prevRef.current = { isTeamAreaExpanded, conversationSidebarCollapsed };
      return;
    }

    const prev = prevRef.current;
    const teamAreaJustExpanded = isTeamAreaExpanded && !prev.isTeamAreaExpanded;
    const sidebarJustExpanded = !conversationSidebarCollapsed && prev.conversationSidebarCollapsed;

    if (teamAreaJustExpanded && !conversationSidebarCollapsed) {
      setConversationSidebarCollapsed(true);
    } else if (sidebarJustExpanded && isTeamAreaExpanded) {
      if (mode === 'team') {
        setTeamAreaExpanded(false);
      } else {
        setSingleAgentPanelExpanded(false);
      }
    }

    prevRef.current = { isTeamAreaExpanded, conversationSidebarCollapsed };
  }, [shouldFullscreen, isTeamAreaExpanded, conversationSidebarCollapsed, mode, setConversationSidebarCollapsed, setSingleAgentPanelExpanded, setTeamAreaExpanded]);

  return { shouldFullscreen };
}

/* ── 业务：Welcome 气泡定位 ── */

export interface WelcomeBubblePositionParams {
  panelRef: RefObject<HTMLDivElement>;
  bubbleRef: RefObject<HTMLDivElement>;
  active: boolean;
}

const BUBBLE_RIGHT_BREAKPOINTS: Array<{ minWidth: number; right: number }> = [
  { minWidth: 1130, right: -114 },
  { minWidth: 1000, right: -55 },
  { minWidth: 800, right: -10 },
];

const BUBBLE_DEFAULT_RIGHT = -10;

export function useWelcomeBubblePosition({ panelRef, bubbleRef, active }: WelcomeBubblePositionParams) {
  useEffect(() => {
    if (!active) return;
    const panel = panelRef.current;
    if (!panel || typeof ResizeObserver === 'undefined') return;

    const updateBubbleRight = () => {
      const bubble = bubbleRef.current;
      if (!bubble) return;
      const width = panel.offsetWidth;
      const matched = BUBBLE_RIGHT_BREAKPOINTS.find((bp) => width >= bp.minWidth);
      const rightValue = matched ? matched.right : BUBBLE_DEFAULT_RIGHT;
      bubble.style.right = `${rightValue}px`;
    };

    const raf = requestAnimationFrame(updateBubbleRight);

    const observer = new ResizeObserver(updateBubbleRight);
    observer.observe(panel);
    return () => {
      cancelAnimationFrame(raf);
      observer.disconnect();
    };
  }, [panelRef, bubbleRef, active]);
}

/* ── 业务：卡片网格自适应（市场/技能列表等卡片墙） ── */

export interface CardGridStyleOptions {
  /** 规则3：一行卡片的最小宽度（卡片将要低于该宽度时减少每行数量），默认 360 */
  minCardWidth?: number;
  /** 规则1：宽屏下卡片最大宽度，默认 456 */
  maxCardWidth?: number;
  /** 卡片间距，默认 16（对应 Tailwind gap-4） */
  gap?: number;
  /** 规则2：窄屏两侧固定边距，默认 48 */
  sideMargin?: number;
  /** 规则1 的宽屏阈值，默认 1600 */
  wideBreakpoint?: number;
}

/**
 * 卡片网格容器自适应样式（宽度均指浏览器宽度），规则：
 * 1. ≥1600：一行最多 3 张，卡片最大 456px，内容居中，左右边距 auto；
 * 2. ≤1600：两侧固定 48px 边距，卡片宽度自适应，一行保持 3 张；
 * 3. 默认最小浏览器宽度 1280，此时卡片最小宽度 360px；
 * 4. 极限宽度 800px：一行一张，卡片适应宽度；
 * 5. 手动压缩到 800~1280：卡片将要小于 360px 时按 3→2→1 逐级减少每行数量。
 *
 * 实现（按区间显式列数，不用 auto-fill）：
 * - ≥1600：repeat(3, minmax(0, max)) + justify-content:center —— 3×456 定格、
 *   leftover 均分两侧即"边距 auto"；
 * - 1208~1600（1208 = 3×360+2×gap+2×side）：repeat(3, minmax(0,1fr)) + padding side
 *   —— 边距严格 48px、3 张、卡片 360~490 自适应（规则 1 的 456 上限只约束 ≥1600）；
 * - 832~1208（832 = 2×360+gap+2×side）：卡片将跌破 360px，减到 2 张；
 * - <832：一行一张、适应宽度（800px 即此档）。
 * 显式列数避免 auto-fill 在 1584~1600 挤出第 4 列、又不必用 maxWidth 封顶破坏"固定 48px"。
 *
 * 样式须挂在直接包住卡片的专用网格容器上（父级不要再叠水平 padding，48px 由本样式提供）；
 * 不要挂到带标题栏/工具栏的 flex 列容器上——inline display:grid 会覆盖 flex-col 打乱布局。
 */
export function useCardGridStyle(options?: CardGridStyleOptions): CSSProperties {
  const { minCardWidth = 360, maxCardWidth = 456, gap = 16, sideMargin = 48, wideBreakpoint = 1600 } = options ?? {};
  const threeColMin = 3 * minCardWidth + 2 * gap + 2 * sideMargin;
  const twoColMin = 2 * minCardWidth + gap + 2 * sideMargin;
  const isWide = useMediaQuery(`(min-width: ${wideBreakpoint}px)`);
  const isThreeCol = useMediaQuery(`(min-width: ${threeColMin}px)`);
  const isTwoCol = useMediaQuery(`(min-width: ${twoColMin}px)`);

  return useMemo(() => {
    if (isWide) {
      return {
        display: 'grid',
        gridTemplateColumns: `repeat(3, minmax(0, ${maxCardWidth}px))`,
        justifyContent: 'center',
        columnGap: gap,
        rowGap: gap,
      };
    }
    const narrow = (columns: number): CSSProperties => ({
      display: 'grid',
      gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))`,
      columnGap: gap,
      rowGap: gap,
      padding: `0 ${sideMargin}px`,
      boxSizing: 'border-box',
    });
    if (isThreeCol) return narrow(3);
    if (isTwoCol) return narrow(2);
    return narrow(1);
  }, [isWide, isThreeCol, isTwoCol, minCardWidth, maxCardWidth, gap, sideMargin]);
}
