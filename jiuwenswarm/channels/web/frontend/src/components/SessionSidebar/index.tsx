/**
 * SessionSidebar Component
 *
 * Redesigned sidebar with logo, navigation, and advanced config panel.
 * Supports collapsed 48px icon-only mode matching the Pixso high-fidelity design.
 */

import { useState, useRef, useEffect, useLayoutEffect, useCallback } from 'react';
import { createPortal } from 'react-dom';
import { useTranslation } from 'react-i18next';
import './SessionSidebar.css';
import heartbeatIcon from '../../assets/sidebar/heartbeat.svg';
import channelIcon from '../../assets/sidebar/channel.svg';
import pluginIcon from '../../assets/sidebar/plugin.svg';
import configIcon from '../../assets/sidebar/config.svg';
import webIcon from '../../assets/sidebar/web.svg';
import logsIcon from '../../assets/sidebar/logs.svg';
import plusIcon from '../../assets/sidebar/plus.svg';
import logoIcon from '/logo.svg';
import advancedConfigIcon from '../../assets/sidebar/advanced-config-new.svg';
import collapseIcon from '../../assets/sidebar/collapse.svg';
import updateIcon from '../../assets/sidebar/advanced-config.svg';
import appearanceSystemIcon from '../../assets/sidebar/appearance-system.svg';
import appearanceDarkIcon from '../../assets/sidebar/appearance-dark.svg';
import appearanceLightIcon from '../../assets/sidebar/appearance-light.svg';
import workIcon from '../../assets/工作.svg';
import cronDesignIcon from '../../assets/定时任务.svg';
import skillDesignIcon from '../../assets/技能.svg';
import agentDesignIcon from '../../assets/智能体.svg';
import moreDesignIcon from '../../assets/更多.svg';
import { webRequest } from '../../services/webClient';

type MainNavKey = 'chat' | 'skills' | 'agents' | 'teams' | 'sessions' | 'heartbeat' | 'cron' | 'channels' | 'extensions' | 'configpanel' | 'logspanel' | 'browserpanel' | 'updatepanel';

interface SessionSidebarProps {
  activeNav: MainNavKey;
  onNavigate: (nav: MainNavKey) => void;
  sessionId: string;
  appVersion: string;
  isConnected: boolean;
  onNewSession?: () => void;
  collapsed?: boolean;
  onCollapse?: () => void;
  onExpand?: () => void;
  showNewSession?: boolean;
  hiddenNavItems?: MainNavKey[];
  onMorePanelOpenChange?: (open: boolean) => void;
}

interface NavItem {
  key: MainNavKey;
  labelKey: string;
  icon: React.ReactNode;
}

const teamNavIcon = (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5}>
    <path strokeLinecap="round" strokeLinejoin="round" d="M18 18.72a8.96 8.96 0 01-12 0m12 0a3.75 3.75 0 00-6 0m6 0A8.96 8.96 0 0012 15.75a8.96 8.96 0 00-6 2.97m12 0A9 9 0 1012 21a8.96 8.96 0 006-2.28zM15 9.75a3 3 0 11-6 0 3 3 0 016 0zm6 3a2.25 2.25 0 11-4.5 0 2.25 2.25 0 014.5 0zm-13.5 0a2.25 2.25 0 11-4.5 0 2.25 2.25 0 014.5 0z" />
  </svg>
);

const mainNavItems: NavItem[] = [
  { key: 'chat', labelKey: 'nav.work', icon: <img src={workIcon} alt="" /> },
  { key: 'cron', labelKey: 'nav.cron', icon: <img src={cronDesignIcon} alt="" /> },
  { key: 'skills', labelKey: 'nav.skills', icon: <img src={skillDesignIcon} alt="" /> },
  { key: 'channels', labelKey: 'nav.channels', icon: <img src={channelIcon} alt="" /> },
  { key: 'agents', labelKey: 'nav.agent', icon: <img src={agentDesignIcon} alt="" /> },
  { key: 'teams', labelKey: 'nav.teams', icon: teamNavIcon },
];

const moreNavItems: NavItem[] = [
  { key: 'heartbeat', labelKey: 'nav.heartbeat', icon: <img src={heartbeatIcon} alt="" /> },
  { key: 'extensions', labelKey: 'nav.extensions', icon: <img src={pluginIcon} alt="" /> },
  { key: 'browserpanel', labelKey: 'nav.browser', icon: <img src={webIcon} alt="" /> },
  { key: 'configpanel', labelKey: 'nav.config', icon: <img src={configIcon} alt="" /> },
  { key: 'logspanel', labelKey: 'nav.logs', icon: <img src={logsIcon} alt="" /> },
  { key: 'updatepanel', labelKey: 'nav.update', icon: <img src={updateIcon} alt="" /> },
];

// Tooltip component — SVG speech bubble matching high-fidelity design
function Tooltip({
  text,
  targetRef,
  visible,
}: {
  text: string;
  targetRef: React.RefObject<HTMLElement>;
  visible: boolean;
}) {
  const tipRef = useRef<HTMLDivElement>(null);
  const textRef = useRef<SVGTextElement>(null);
  const posRef = useRef({ top: 0, left: 0 });
  const textWidthRef = useRef(0);
  const [, forceRender] = useState(0);

  useLayoutEffect(() => {
    if (visible && textRef.current) {
      const w = textRef.current.getComputedTextLength();
      if (Math.abs(textWidthRef.current - w) > 0.5) {
        textWidthRef.current = w;
        forceRender((n) => n + 1);
      }
    }
  }, [visible, text]);

  useLayoutEffect(() => {
    if (!visible || !targetRef.current || !tipRef.current) return;
    function updatePos() {
      if (!targetRef.current || !tipRef.current) return;
      const rect = targetRef.current.getBoundingClientRect();
      // Body 30px tall, tail centered vertically (no overhang)
      const top = rect.top + rect.height / 2 - 30 / 2;
      const left = rect.right + 11;
      if (Math.abs(posRef.current.top - top) > 0.5 || Math.abs(posRef.current.left - left) > 0.5) {
        posRef.current = { top, left };
        forceRender((n) => n + 1);
      }
    }
    updatePos();
    window.addEventListener('scroll', updatePos, true);
    return () => window.removeEventListener('scroll', updatePos, true);
  }, [visible, targetRef]);

  useEffect(() => {
    return () => { posRef.current = { top: 0, left: 0 }; };
  }, []);

  if (!visible) return null;

  const textW = Math.max(textWidthRef.current, 20);
  const W = textW + 24; // 12px padding each side

  return createPortal(
    <div ref={tipRef} style={{ position: 'fixed', top: posRef.current.top, left: posRef.current.left, zIndex: 1100, pointerEvents: 'none' }}>
      <svg
        width={W + 8}
        height={30}
        viewBox={`-8 0 ${W + 8} 30`}
        xmlns="http://www.w3.org/2000/svg"
      >
        <defs>
          <filter id="bubble-shadow" x="-50%" y="-20%" width="200%" height="200%">
            <feDropShadow dx="0" dy="8" stdDeviation="12" floodColor="rgba(0,0,0,0.16)" />
          </filter>
        </defs>
        <g filter="url(#bubble-shadow)">
          {/* Body — rounded rect, 4px radius, #2A2A2A */}
          <rect x="0" y="0" width={W} height="30" rx="4" fill="#2A2A2A" />
          {/* Tail — base inside body (invisible), rounded tip protrudes 8px left,
              centered vertically at y=15 */}
          <polygon
            points={`10,10 -6,15 10,20`}
            fill="#2A2A2A"
            stroke="#2A2A2A"
            strokeWidth="3"
            strokeLinejoin="round"
          />
        </g>
        <text
          ref={textRef}
          x="12"
          y="22"
          fill="#FFFFFF"
          fontSize="14"
          fontWeight="400"
        >
          {text}
        </text>
      </svg>
    </div>,
    document.body
  );
}

// Advanced Config Panel Component
function AdvancedConfigPanel({
  isOpen,
  onClose,
  isConnected,
  buttonRef,
}: {
  isOpen: boolean;
  onClose: () => void;
  isConnected: boolean;
  buttonRef: React.RefObject<HTMLButtonElement>;
}) {
  const { i18n, t } = useTranslation();
  const [theme, setTheme] = useState(() => localStorage.getItem('theme') || 'light');
  const panelRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (
        panelRef.current &&
        !panelRef.current.contains(event.target as Node) &&
        buttonRef.current &&
        !buttonRef.current.contains(event.target as Node)
      ) {
        onClose();
      }
    }
    if (isOpen) {
      document.addEventListener('mousedown', handleClickOutside);
    }
    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
    };
  }, [isOpen, onClose, buttonRef]);

  const handleLanguageChange = (lang: 'zh' | 'en') => {
    i18n.changeLanguage(lang);
    void webRequest('locale.set_conf', { preferred_language: lang }).catch(() => {});
  };

  const handleThemeChange = (newTheme: string) => {
    setTheme(newTheme);
    localStorage.setItem('theme', newTheme);
    if (newTheme === 'light') {
      document.documentElement.setAttribute('data-theme', 'light');
    } else {
      document.documentElement.removeAttribute('data-theme');
    }
  };

  const isZh = i18n.language.startsWith('zh');

  if (!isOpen) return null;

  return (
    <div ref={panelRef} className="advanced-config-panel">
      <div className="config-row">
        <span className="config-row__label">{t('sessionSidebar.connectionStatus')}</span>
        <div className={`connection-status ${isConnected ? 'connection-status--connected' : 'connection-status--disconnected'}`}>
          <span className="connection-status__dot" />
          <span className="connection-status__text">
            {isConnected ? t('connection.connected') : t('connection.disconnected')}
          </span>
        </div>
      </div>

      <div className="config-row">
        <span className="config-row__label">{t('sessionSidebar.language')}</span>
        <div className="segmented-control">
          <button
            className={`segmented-control__btn ${isZh ? 'segmented-control__btn--active' : ''}`}
            onClick={() => handleLanguageChange('zh')}
          >
            中
          </button>
          <button
            className={`segmented-control__btn ${!isZh ? 'segmented-control__btn--active' : ''}`}
            onClick={() => handleLanguageChange('en')}
          >
            En
          </button>
        </div>
      </div>

      <div className="config-row">
        <span className="config-row__label">{t('sessionSidebar.appearance')}</span>
        <div className="segmented-control segmented-control--icons">
          <button
            className={`segmented-control__btn ${theme === 'system' ? 'segmented-control__btn--active' : ''}`}
            onClick={() => handleThemeChange('system')}
            title={t('app.themeSystem')}
          >
            <img src={appearanceSystemIcon} alt="" />
          </button>
          <button
            className={`segmented-control__btn ${theme === 'dark' ? 'segmented-control__btn--active' : ''}`}
            onClick={() => handleThemeChange('dark')}
            title={t('app.themeDark')}
          >
            <img src={appearanceDarkIcon} alt="" />
          </button>
          <button
            className={`segmented-control__btn ${theme === 'light' ? 'segmented-control__btn--active' : ''}`}
            onClick={() => handleThemeChange('light')}
            title={t('app.themeLight')}
          >
            <img src={appearanceLightIcon} alt="" />
          </button>
        </div>
      </div>
    </div>
  );
}

export function SessionSidebar({
  activeNav,
  onNavigate,
  sessionId: _sessionId,
  appVersion,
  isConnected,
  onNewSession,
  collapsed = false,
  onCollapse,
  onExpand,
  showNewSession = true,
  hiddenNavItems = [],
  onMorePanelOpenChange,
}: SessionSidebarProps) {
  const { t } = useTranslation();
  const [advancedConfigOpen, setAdvancedConfigOpen] = useState(false);
  const [moreOpen, setMoreOpen] = useState(false);
  const advancedBtnRef = useRef<HTMLButtonElement>(null);
  const moreBtnRef = useRef<HTMLButtonElement>(null);
  const morePanelRef = useRef<HTMLDivElement>(null);

  // Tooltip state for collapsed mode
  const [hoveredNav, setHoveredNav] = useState<string | null>(null);
  const mouseMovedRef = useRef(false);
  const sidebarRef = useRef<HTMLElement>(null);
  const logoRef = useRef<HTMLDivElement>(null);
  const newChatRef = useRef<HTMLButtonElement>(null);
  const settingsRef = useRef<HTMLButtonElement>(null);
  const navRefs = useRef<Map<string, HTMLButtonElement>>(new Map());

  // Synchronously reset mouse-move guard and clear hover during render phase,
  // before DOM commit, so mouseenter events arriving between commit and effect
  // see the correct state.
  const prevCollapsedRef = useRef(collapsed);
  if (prevCollapsedRef.current !== collapsed) {
    mouseMovedRef.current = false;
    prevCollapsedRef.current = collapsed;
  }
  // Also clear any stale hover state when transitioning to expanded
  useEffect(() => {
    if (!collapsed) {
      setHoveredNav(null);
    }
  }, [collapsed]);

  const handleMouseEnter = useCallback((key: string) => {
    if (mouseMovedRef.current) {
      setHoveredNav(key);
    }
  }, []);

  useEffect(() => {
    if (!collapsed) return;
    function onMouseMove() {
      if (!mouseMovedRef.current) {
        mouseMovedRef.current = true;
      }
    }
    window.addEventListener('mousemove', onMouseMove, { once: false });
    return () => window.removeEventListener('mousemove', onMouseMove);
  }, [collapsed]);

  const handleNewSession = useCallback(() => {
    onNavigate('chat');
    if (onNewSession) {
      onNewSession();
    }
  }, [onNavigate, onNewSession]);

  const toggleAdvancedConfig = () => {
    setAdvancedConfigOpen(!advancedConfigOpen);
  };

  const toggleMore = () => {
    setMoreOpen((open) => {
      const nextOpen = !open;
      if (nextOpen) {
        const defaultMoreNav = visibleMoreNavItems[0]?.key;
        if (defaultMoreNav) {
          onNavigate(defaultMoreNav);
        }
        if (!collapsed) {
          onCollapse?.();
        }
      }
      return nextOpen;
    });
  };

  const handleNavClick = (nav: MainNavKey) => {
    setMoreOpen(false);
    onNavigate(nav);
  };

  const handleMoreNavClick = (nav: MainNavKey) => {
    onNavigate(nav);
  };

  const handleLogoClick = () => {
    if (collapsed && onExpand) {
      onExpand();
    } else if (!collapsed && onCollapse) {
      onCollapse();
    }
  };

  const getNavItemLabel = (item: NavItem) => t(item.labelKey);
  const visibleMainNavItems = mainNavItems.filter((item) => !hiddenNavItems.includes(item.key));
  const visibleMoreNavItems = moreNavItems.filter((item) => !hiddenNavItems.includes(item.key));
  const isMoreActive = visibleMoreNavItems.some((item) => item.key === activeNav);

  useEffect(() => {
    if (!moreOpen) return;
    function handleClickOutside(event: MouseEvent) {
      const target = event.target as Node;
      if (moreBtnRef.current?.contains(target) || morePanelRef.current?.contains(target)) {
        return;
      }
      setMoreOpen(false);
    }
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [moreOpen]);

  useEffect(() => {
    onMorePanelOpenChange?.(moreOpen);
    return () => onMorePanelOpenChange?.(false);
  }, [moreOpen, onMorePanelOpenChange]);

  // Collapsed mode: 48px icon-only sidebar
  if (collapsed) {
    return (
      <aside ref={sidebarRef} className="sidebar sidebar--collapsed">
        <Tooltip text={t('sessionSidebar.expandSidebar')} targetRef={logoRef} visible={hoveredNav === 'logo'} />
        <div
          ref={logoRef}
          className="collapsed-logo"
          onClick={handleLogoClick}
          onMouseEnter={() => handleMouseEnter('logo')}
          onMouseLeave={() => setHoveredNav(null)}
          title={t('sessionSidebar.expandSidebar')}
        >
          <img src={logoIcon} alt="Logo" width="28" height="28" />
        </div>

        {/* New Chat button */}
        {showNewSession && <><Tooltip text={t('chat.newSession')} targetRef={newChatRef} visible={hoveredNav === 'newchat'} />
        <button
          ref={newChatRef}
          className="collapsed-nav-item"
          onClick={handleNewSession}
          onMouseEnter={() => handleMouseEnter('newchat')}
          onMouseLeave={() => setHoveredNav(null)}
          title={t('chat.newSession')}
        >
          <span className="collapsed-nav-item__icon">
            <img src={plusIcon} alt="" width="16" height="16" />
          </span>
          <span className="collapsed-nav-item__label">{t('chat.newSession')}</span>
        </button></>}

        {/* Main nav icons */}
        {visibleMainNavItems.map((item) => (
          <Tooltip
            key={item.key}
            text={getNavItemLabel(item)}
            targetRef={{ current: navRefs.current.get(item.key) || null }}
            visible={hoveredNav === item.key}
          />
        ))}
        {visibleMainNavItems.map((item) => (
          <button
            key={item.key}
            ref={(el) => {
              if (el) navRefs.current.set(item.key, el);
            }}
            className={`collapsed-nav-item${activeNav === item.key ? ' collapsed-nav-item--active' : ''}`}
            onClick={() => handleNavClick(item.key)}
            onMouseEnter={() => handleMouseEnter(item.key)}
            onMouseLeave={() => setHoveredNav(null)}
            title={getNavItemLabel(item)}
          >
            <span className="collapsed-nav-item__icon">{item.icon}</span>
            <span className="collapsed-nav-item__label">{getNavItemLabel(item)}</span>
          </button>
        ))}

        {/* More menu */}
        {visibleMoreNavItems.length > 0 && (
          <>
            <Tooltip text={t('nav.more')} targetRef={moreBtnRef} visible={hoveredNav === 'more' && !moreOpen} />
            <button
              ref={moreBtnRef}
              className={`collapsed-nav-item${(moreOpen || isMoreActive) ? ' collapsed-nav-item--active' : ''}`}
              onClick={toggleMore}
              onMouseEnter={() => handleMouseEnter('more')}
              onMouseLeave={() => setHoveredNav(null)}
              title={t('nav.more')}
              aria-expanded={moreOpen}
            >
              <span className="collapsed-nav-item__icon">
                <img src={moreDesignIcon} alt="" />
              </span>
              <span className="collapsed-nav-item__label">{t('nav.more')}</span>
            </button>
            {moreOpen && (
              <div ref={morePanelRef} className="collapsed-more-panel">
                <div className="collapsed-more-panel__title">{t('sessionSidebar.moreSettings')}</div>
                <nav className="collapsed-more-panel__list" aria-label={t('sessionSidebar.moreSettings')}>
                  {visibleMoreNavItems.map((item) => (
                    <button
                      key={item.key}
                      className={`collapsed-more-panel__item${activeNav === item.key ? ' collapsed-more-panel__item--active' : ''}`}
                      onClick={() => handleMoreNavClick(item.key)}
                    >
                      <span className="collapsed-more-panel__icon">{item.icon}</span>
                      <span className="collapsed-more-panel__text">{getNavItemLabel(item)}</span>
                    </button>
                  ))}
                </nav>
              </div>
            )}
          </>
        )}

        {/* Separator dot */}
        <div className="collapsed-separator" />

        {/* Bottom spacer */}
        <div className="collapsed-spacer" />

        {/* Settings icon */}
        <button
          ref={settingsRef}
          className="collapsed-nav-item"
          onClick={toggleAdvancedConfig}
          onMouseEnter={() => handleMouseEnter('settings')}
          onMouseLeave={() => setHoveredNav(null)}
        >
          <span className="collapsed-nav-item__icon">
            <img src={advancedConfigIcon} alt="" width="16" height="16" />
          </span>
        </button>

        <AdvancedConfigPanel
          isOpen={advancedConfigOpen}
          onClose={() => setAdvancedConfigOpen(false)}
          isConnected={isConnected}
          buttonRef={advancedBtnRef}
        />
      </aside>
    );
  }

  // Expanded mode
  return (
    <aside className="sidebar">
      {/* Header Row: Logo + Collapse Button */}
      <div className="sidebar-header">
        <div className="sidebar-logo">
          <img src={logoIcon} alt="Logo" width="28" height="28" />
        </div>
        <button
          className="collapse-btn"
          title={t('sessionSidebar.collapseSidebar')}
          onClick={() => onCollapse?.()}
        >
          <img src={collapseIcon} alt="" />
        </button>
      </div>

      {/* 智能体 Section */}
      <div className="nav-section">
        <div className="nav-section-label">{t('nav.work')}</div>
        {showNewSession && <button className="new-chat-btn" onClick={handleNewSession}>
          <span className="new-chat-btn__left">
            <img src={plusIcon} alt="" />
            <span className="new-chat-btn__text">{t('chat.newSession')}</span>
          </span>
        </button>}
        <nav className="sidebar-nav">
          {visibleMainNavItems.map((item) => (
            <button
              key={item.key}
              className={`nav-item ${activeNav === item.key ? 'active' : ''}`}
              onClick={() => handleNavClick(item.key)}
            >
              <span className="nav-item__icon">{item.icon}</span>
              <span className="nav-item__text">{getNavItemLabel(item)}</span>
            </button>
          ))}
          {visibleMoreNavItems.length > 0 && (
            <>
              <button
                ref={moreBtnRef}
                className={`nav-item nav-item--more-toggle ${isMoreActive ? 'active' : ''}`}
                onClick={toggleMore}
                aria-expanded={moreOpen || isMoreActive}
              >
                <span className="nav-item__icon">
                  <img src={moreDesignIcon} alt="" />
                </span>
                <span className="nav-item__text">{t('nav.more')}</span>
              </button>
              {(moreOpen || isMoreActive) && (
                <div ref={morePanelRef} className="sidebar-more-list">
                  {visibleMoreNavItems.map((item) => (
                    <button
                      key={item.key}
                      className={`nav-item nav-item--more-child ${activeNav === item.key ? 'active' : ''}`}
                      onClick={() => handleNavClick(item.key)}
                    >
                      <span className="nav-item__icon">{item.icon}</span>
                      <span className="nav-item__text">{t(item.labelKey)}</span>
                    </button>
                  ))}
                </div>
              )}
            </>
          )}
        </nav>
      </div>

      {/* User Info Bar - Bottom Row */}
      <div className="sidebar-bottom">
        <div className="sidebar-user">
          <span className="sidebar-user__name">{t('version', { version: appVersion })}</span>
        </div>
        <button
          ref={advancedBtnRef}
          className="advanced-config-btn"
          onClick={toggleAdvancedConfig}
        >
          <img src={advancedConfigIcon} alt="" />
        </button>
      </div>

      <AdvancedConfigPanel
        isOpen={advancedConfigOpen}
        onClose={() => setAdvancedConfigOpen(false)}
        isConnected={isConnected}
        buttonRef={advancedBtnRef}
      />
    </aside>
  );
}
