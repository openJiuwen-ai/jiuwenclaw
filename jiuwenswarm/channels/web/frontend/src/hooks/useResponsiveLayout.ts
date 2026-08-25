import { useEffect, useState } from 'react';

const MOBILE_BREAKPOINT = '(max-width: 823px)';

export function useResponsiveLayout() {
  const [conversationSidebarCollapsed, setConversationSidebarCollapsed] = useState(false);
  const [conversationSidebarFloating, setConversationSidebarFloating] = useState(false);
  const [toolPanelHidden, setToolPanelHidden] = useState(false);
  const [toolPanelMaximized, setToolPanelMaximized] = useState(false);

  useEffect(() => {
    const mql = window.matchMedia(MOBILE_BREAKPOINT);
    const handler = (e: MediaQueryListEvent) => {
      if (e.matches) {
        setConversationSidebarCollapsed(true);
        setConversationSidebarFloating(true);
      } else {
        setConversationSidebarFloating(false);
      }
    };
    if (mql.matches) {
      setConversationSidebarCollapsed(true);
      setConversationSidebarFloating(true);
    }
    mql.addEventListener('change', handler);
    return () => mql.removeEventListener('change', handler);
  }, []);

  return {
    conversationSidebarCollapsed,
    setConversationSidebarCollapsed,
    conversationSidebarFloating,
    setConversationSidebarFloating,
    toolPanelHidden,
    setToolPanelHidden,
    toolPanelMaximized,
    setToolPanelMaximized,
  };
}
