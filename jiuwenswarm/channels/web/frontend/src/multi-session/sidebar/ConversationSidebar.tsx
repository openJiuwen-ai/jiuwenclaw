import { useEffect, useMemo, useRef, useState } from 'react';
import { Ellipsis, LoaderCircle, Plus, StickyNotePlus, Trash2 } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { useChatStore, type ChatRuntime } from '../../stores/chatStore';
import type { Session } from '../../types';
import './ConversationSidebar.css';

const UNREAD_KEY = 'jiuwenswarm_session_unread';
const PAGE_SIZE = 10;
const RELATIVE_TIME_REFRESH_MS = 60_000;

type SessionIndicator = 'waiting' | 'processing' | 'unread' | 'time';

interface ConversationSidebarProps {
  sessions: Session[];
  activeSessionId: string | null;
  onNew: () => void;
  onSelect: (session: Session) => void;
  onDelete: (session: Session) => void;
}

interface ConversationListItemProps {
  session: Session;
  runtime?: ChatRuntime;
  active: boolean;
  unread: boolean;
  now: number;
  onSelect: () => void;
  onDelete: () => void;
}

export function getSessionActivityAt(session: Session): number {
  if (typeof session.last_user_message_at === 'number') {
    return session.last_user_message_at < 1e11
      ? session.last_user_message_at * 1000
      : session.last_user_message_at;
  }
  return 0;
}

export function getSessionIndicator(
  runtime: ChatRuntime | undefined,
  unread: boolean,
  sessionProcessing = false,
): SessionIndicator {
  if (runtime?.pendingQuestion) return 'waiting';
  if (runtime?.isProcessing || sessionProcessing) return 'processing';
  if (unread) return 'unread';
  return 'time';
}

export function getProcessingTransitions(
  previous: Record<string, boolean>,
  sessions: Session[],
  runtimes: Record<string, ChatRuntime>,
  activeSessionId: string | null,
): { snapshot: Record<string, boolean>; completedInBackground: string[] } {
  const snapshot: Record<string, boolean> = {};
  const completedInBackground: string[] = [];
  for (const session of sessions) {
    const sessionId = session.session_id;
    const processing = runtimes[sessionId]?.isProcessing ?? session.is_processing === true;
    snapshot[sessionId] = processing;
    if (previous[sessionId] && !processing && sessionId !== activeSessionId) {
      completedInBackground.push(sessionId);
    }
  }
  return { snapshot, completedInBackground };
}

export function formatRelativeTime(
  activityAt: number,
  now: number,
  language: string,
  translate: (key: string, options?: Record<string, unknown>) => string,
): string {
  const elapsed = Math.max(0, now - activityAt);
  if (elapsed < 60_000) return translate('time.justNow');
  if (elapsed < 3_600_000) return translate('time.minutesAgo', { count: Math.floor(elapsed / 60_000) });
  if (elapsed < 86_400_000) return translate('time.hoursAgo', { count: Math.floor(elapsed / 3_600_000) });
  if (elapsed < 604_800_000) return translate('time.daysAgo', { count: Math.floor(elapsed / 86_400_000) });
  return new Date(activityAt).toLocaleDateString(language, { month: 'short', day: 'numeric' });
}

function loadUnreadSessions(): Set<string> {
  try {
    const value: unknown = JSON.parse(localStorage.getItem(UNREAD_KEY) || '[]');
    if (!Array.isArray(value)) return new Set();
    return new Set(value.filter((id): id is string => typeof id === 'string'));
  } catch {
    return new Set();
  }
}

function ConversationListItem({
  session,
  runtime,
  active,
  unread,
  now,
  onSelect,
  onDelete,
}: ConversationListItemProps) {
  const { t, i18n } = useTranslation();
  const itemRef = useRef<HTMLDivElement>(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const title = session.title?.trim() || t('multiSession.untitled');
  const indicator = getSessionIndicator(runtime, unread, session.is_processing === true);
  const deleteDisabled = indicator === 'processing' || indicator === 'waiting';

  let status: React.ReactNode;
  if (indicator === 'waiting') {
    status = <span className="conversation-list-item__waiting">{t('multiSession.waitingReply')}</span>;
  } else if (indicator === 'processing') {
    status = <LoaderCircle className="conversation-list-item__loader" aria-label={t('multiSession.runtime.processing')} />;
  } else if (indicator === 'unread') {
    status = <span className="conversation-list-item__unread" title={t('multiSession.completedUnread')} />;
  } else {
    status = formatRelativeTime(getSessionActivityAt(session), now, i18n.language, t);
  }

  useEffect(() => {
    if (!menuOpen) return;

    const handlePointerDown = (event: MouseEvent) => {
      if (!itemRef.current?.contains(event.target as Node)) {
        setMenuOpen(false);
      }
    };
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setMenuOpen(false);
      }
    };

    document.addEventListener('mousedown', handlePointerDown);
    document.addEventListener('keydown', handleKeyDown);
    return () => {
      document.removeEventListener('mousedown', handlePointerDown);
      document.removeEventListener('keydown', handleKeyDown);
    };
  }, [menuOpen]);

  return (
    <div ref={itemRef} className={`conversation-list-item${active ? ' is-active' : ''}${menuOpen ? ' is-menu-open' : ''}`}>
      <button type="button" className="conversation-list-item__main" onClick={onSelect} title={title}>
        <span className="conversation-list-item__title">{title}</span>
        <span className="conversation-list-item__meta">{status}</span>
      </button>
      <button
        type="button"
        className="conversation-list-item__actions"
        onClick={(event) => {
          event.stopPropagation();
          setMenuOpen((open) => !open);
        }}
        title={t('multiSession.moreActions')}
        aria-label={t('multiSession.moreActions')}
        aria-haspopup="menu"
        aria-expanded={menuOpen}
      >
        <Ellipsis size={15} />
      </button>
      {menuOpen ? (
        <div className="conversation-list-item__menu" role="menu">
          <button
            type="button"
            className="conversation-list-item__menu-item"
            disabled={deleteDisabled}
            onClick={(event) => {
              event.stopPropagation();
              setMenuOpen(false);
              onDelete();
            }}
            title={deleteDisabled ? t('multiSession.deleteRunningDisabled') : t('multiSession.delete')}
            role="menuitem"
          >
            <Trash2 size={14} aria-hidden="true" />
            <span>{t('multiSession.delete')}</span>
          </button>
        </div>
      ) : null}
    </div>
  );
}

export function ConversationSidebar({
  sessions,
  activeSessionId,
  onNew,
  onSelect,
  onDelete,
}: ConversationSidebarProps) {
  const { t } = useTranslation();
  const runtimes = useChatStore((state) => state.runtimes);
  const [visibleCount, setVisibleCount] = useState(PAGE_SIZE);
  const [relativeTimeNow, setRelativeTimeNow] = useState(Date.now);
  const [unreadSessions, setUnreadSessions] = useState(loadUnreadSessions);
  const previousProcessing = useRef<Record<string, boolean>>({});

  useEffect(() => {
    const timer = window.setInterval(() => setRelativeTimeNow(Date.now()), RELATIVE_TIME_REFRESH_MS);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    const { snapshot, completedInBackground } = getProcessingTransitions(
      previousProcessing.current,
      sessions,
      runtimes,
      activeSessionId,
    );
    previousProcessing.current = snapshot;
    if (completedInBackground.length > 0) {
      setUnreadSessions((current) => new Set([...current, ...completedInBackground]));
    }
  }, [activeSessionId, runtimes, sessions]);

  useEffect(() => {
    if (!activeSessionId) return;
    setUnreadSessions((current) => {
      if (!current.has(activeSessionId)) return current;
      const next = new Set(current);
      next.delete(activeSessionId);
      return next;
    });
  }, [activeSessionId]);

  useEffect(() => {
    localStorage.setItem(UNREAD_KEY, JSON.stringify([...unreadSessions]));
  }, [unreadSessions]);

  const orderedSessions = useMemo(
    () => [...sessions]
      .filter((session) => session.session_id.startsWith('sess_'))
      .sort((left, right) => getSessionActivityAt(right) - getSessionActivityAt(left)),
    [sessions],
  );

  return (
    <aside className="conversation-sidebar" aria-label={t('multiSession.conversations')}>
      <div className="brand-title"> {t('multiSession.title')} </div>
      <button type="button" className="conversation-sidebar__options" onClick={onNew}>
        <StickyNotePlus size={14} aria-hidden="true" />
        <span>{t('multiSession.newConversation')}</span>
      </button>
      <div className="conversation-sidebar__section-heading">
        <span className="conversation-sidebar__label">{t('multiSession.conversations')}</span>
        <button
          type="button"
          className="conversation-sidebar__section-new"
          onClick={onNew}
          title={t('multiSession.newConversation')}
          aria-label={t('multiSession.newConversation')}
        >
          <Plus size={14} aria-hidden="true" />
        </button>
      </div>
      <div className="conversation-sidebar__list">
        {orderedSessions.length === 0 ? (
          <div className="conversation-sidebar__empty">{t('multiSession.empty')}</div>
        ) : (
          orderedSessions.slice(0, visibleCount).map((session) => (
            <ConversationListItem
              key={session.session_id}
              session={session}
              runtime={runtimes[session.session_id]}
              active={activeSessionId === session.session_id}
              unread={unreadSessions.has(session.session_id)}
              now={relativeTimeNow}
              onSelect={() => onSelect(session)}
              onDelete={() => onDelete(session)}
            />
          ))
        )}
        {orderedSessions.length > 0 && (
          <div className="conversation-sidebar__pagination">
            {visibleCount < orderedSessions.length && (
              <button onClick={() => setVisibleCount((count) => count + PAGE_SIZE)}>{t('multiSession.showMore')}</button>
            )}
            {visibleCount > PAGE_SIZE && (
              <button onClick={() => setVisibleCount(PAGE_SIZE)}>{t('multiSession.collapse')}</button>
            )}
          </div>
        )}
      </div>
    </aside>
  );
}
