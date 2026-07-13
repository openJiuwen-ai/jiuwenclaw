/**
 * SessionSidebar Component
 *
 * Redesigned sidebar with logo, navigation, and advanced config panel.
 */

import { useState, useRef, useEffect, useLayoutEffect, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import './SessionSidebar.css';
import heartbeatIcon from '../../assets/sidebar/heartbeat.svg';
import channelIcon from '../../assets/sidebar/channel.svg';
import pluginIcon from '../../assets/sidebar/plugin.svg';
import configIcon from '../../assets/sidebar/config.svg';
import webIcon from '../../assets/sidebar/web.svg';
import plusIcon from '../../assets/sidebar/plus.svg';
import logoIcon from '/logo.svg';
import advancedConfigIcon from '../../assets/sidebar/advanced-config-new.svg';
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

type MainNavKey = 'chat' | 'skills' | 'agents' | 'teams' | 'sessions' | 'heartbeat' | 'cron' | 'channels' | 'extensions' | 'configpanel' | 'browserpanel' | 'updatepanel';

interface SessionSidebarProps {
  activeNav: MainNavKey;
  onNavigate: (nav: MainNavKey) => void;
  appVersion: string;
  isConnected: boolean;
  onNewSession?: () => void;
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
  { key: 'updatepanel', labelKey: 'nav.update', icon: <img src={updateIcon} alt="" /> },
];

// Advanced Config Panel Component
function AdvancedConfigPanel({
  isOpen,
  onClose,
  appVersion,
  isConnected,
  buttonRef,
}: {
  isOpen: boolean;
  onClose: () => void;
  appVersion: string;
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

      {appVersion && (
        <div className="config-row">
          <span className="config-row__label">{t('sessionSidebar.version')}</span>
          <span className="config-row__value">{appVersion}</span>
        </div>
      )}

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
  appVersion,
  isConnected,
  onNewSession,
  showNewSession = true,
  hiddenNavItems = [],
  onMorePanelOpenChange,
}: SessionSidebarProps) {
  const { t } = useTranslation();
  const [advancedConfigOpen, setAdvancedConfigOpen] = useState(false);
  const settingsRef = useRef<HTMLButtonElement>(null);

  const handleNewSession = useCallback(() => {
    onNavigate('chat');
    if (onNewSession) {
      onNewSession();
    }
  }, [onNavigate, onNewSession]);

  const toggleAdvancedConfig = () => {
    setAdvancedConfigOpen(!advancedConfigOpen);
  };

  const handleMoreClick = () => {
    if (!isMoreActive) {
      const defaultMoreNav = visibleMoreNavItems[0]?.key;
      if (defaultMoreNav) {
        onNavigate(defaultMoreNav);
      }
    }
  };

  const handleNavClick = (nav: MainNavKey) => {
    onNavigate(nav);
  };

  const handleMoreNavClick = (nav: MainNavKey) => {
    onNavigate(nav);
  };

  const getNavItemLabel = (item: NavItem) => t(item.labelKey);
  const visibleMainNavItems = mainNavItems.filter((item) => !hiddenNavItems.includes(item.key));
  const visibleMoreNavItems = moreNavItems.filter((item) => !hiddenNavItems.includes(item.key));
  const isMoreActive = visibleMoreNavItems.some((item) => item.key === activeNav);

  useLayoutEffect(() => {
    onMorePanelOpenChange?.(isMoreActive);
    return () => onMorePanelOpenChange?.(false);
  }, [isMoreActive, onMorePanelOpenChange]);

  return (
    <aside className="sidebar sidebar--icon-rail">
      <div className="icon-rail-logo">
        <img src={logoIcon} alt="Logo" width="28" height="28" />
      </div>

      {showNewSession && (
        <button
          className="icon-rail-nav-item"
          onClick={handleNewSession}
        >
          <span className="icon-rail-nav-item__icon">
            <img src={plusIcon} alt="" width="16" height="16" />
          </span>
          <span className="icon-rail-nav-item__label">{t('chat.newSession')}</span>
        </button>
      )}

      {visibleMainNavItems.map((item) => (
        <button
          key={item.key}
          className={`icon-rail-nav-item${activeNav === item.key ? ' icon-rail-nav-item--active' : ''}`}
          onClick={() => handleNavClick(item.key)}
        >
          <span className="icon-rail-nav-item__icon">{item.icon}</span>
          <span className="icon-rail-nav-item__label">{getNavItemLabel(item)}</span>
        </button>
      ))}

      {visibleMoreNavItems.length > 0 && (
        <>
          <button
            className={`icon-rail-nav-item${isMoreActive ? ' icon-rail-nav-item--active' : ''}`}
            onClick={handleMoreClick}
            aria-expanded={isMoreActive}
          >
            <span className="icon-rail-nav-item__icon">
              <img src={moreDesignIcon} alt="" />
            </span>
            <span className="icon-rail-nav-item__label">{t('nav.more')}</span>
          </button>
          {isMoreActive && (
            <div className="icon-rail-more-panel">
              <div className="icon-rail-more-panel__title">{t('sessionSidebar.moreSettings')}</div>
              <nav className="icon-rail-more-panel__list" aria-label={t('sessionSidebar.moreSettings')}>
                {visibleMoreNavItems.map((item) => (
                  <button
                    key={item.key}
                    className={`icon-rail-more-panel__item${activeNav === item.key ? ' icon-rail-more-panel__item--active' : ''}`}
                    onClick={() => handleMoreNavClick(item.key)}
                  >
                    <span className="icon-rail-more-panel__icon">{item.icon}</span>
                    <span className="icon-rail-more-panel__text">{getNavItemLabel(item)}</span>
                  </button>
                ))}
              </nav>
            </div>
          )}
        </>
      )}

      <div className="icon-rail-spacer" />

      <button
        ref={settingsRef}
        className="icon-rail-nav-item"
        onClick={toggleAdvancedConfig}
        aria-label={t('sessionSidebar.moreSettings')}
      >
        <span className="icon-rail-nav-item__icon">
          <img src={advancedConfigIcon} alt="" width="16" height="16" />
        </span>
      </button>

      <AdvancedConfigPanel
        isOpen={advancedConfigOpen}
        onClose={() => setAdvancedConfigOpen(false)}
        appVersion={appVersion}
        isConnected={isConnected}
        buttonRef={settingsRef}
      />
    </aside>
  );
}
