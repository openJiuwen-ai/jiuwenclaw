/**
 * SessionSidebar 组件
 *
 * 会话列表 + 定时任务导航：顶部"新建会话" + cron 按钮，下方 DB 会话列表。
 */

import { useTranslation } from 'react-i18next';
import clsx from 'clsx';
import type { HistorySession } from '../../services/api';
import { formatRelativeTime } from '../../utils';
import './SessionSidebar.css';

type MainNavKey = 'chat' | 'cron';

interface SessionSidebarProps {
  sessions: HistorySession[];
  currentSessionId: string | null;
  onSelect: (sessionId: string) => void;
  onNewSession: () => void;
  activeNav: MainNavKey;
  onNavigate: (nav: MainNavKey) => void;
  appVersion: string;
}

export function SessionSidebar({
  sessions,
  currentSessionId,
  onSelect,
  onNewSession,
  activeNav,
  onNavigate,
  appVersion,
}: SessionSidebarProps) {
  const { t } = useTranslation();
  return (
    <aside className="nav flex flex-col">
      <div className="p-2 border-b border-border">
        <button onClick={onNewSession} className="nav-item w-full justify-center font-medium">
          + {t('history.newSession')}
        </button>
        <button
          onClick={() => onNavigate('cron')}
          className={`nav-item w-full ${activeNav === 'cron' ? 'active' : ''}`}
          data-testid="nav-cron"
        >
          <svg className="w-4 h-4 nav-item__icon" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.5}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M12 6v6h4.5m4.5 0a9 9 0 11-18 0 9 9 0 0118 0z" />
          </svg>
          {t('nav.cron')}
        </button>
      </div>

      <div className="flex-1 overflow-y-auto p-2 space-y-1">
        {sessions.length === 0 && (
          <div className="text-text-muted text-sm px-2 py-3">{t('history.empty')}</div>
        )}
        {sessions.map((s) => (
          <button
            key={s.session_id}
            onClick={() => onSelect(s.session_id)}
            className={clsx(
              'w-full text-left rounded-md p-2 border transition-colors',
              s.session_id === currentSessionId
                ? 'border-accent bg-accent-subtle'
                : 'border-transparent hover:bg-secondary',
            )}
          >
            <div className="text-sm font-medium text-text truncate">
              {s.title || s.session_id}
            </div>
            {s.last_preview && (
              <div className="text-xs text-text-muted truncate mt-0.5">{s.last_preview}</div>
            )}
            <div className="text-xs text-text-muted mt-0.5">
              {formatRelativeTime(new Date(s.updated_at * 1000).toISOString())}
            </div>
          </button>
        ))}
      </div>

      <div className="pt-2 border-t border-border text-xs text-text-muted">
        <div className="px-2.5">
          <span>{t('version', { version: appVersion })}</span>
        </div>
      </div>
    </aside>
  );
}
