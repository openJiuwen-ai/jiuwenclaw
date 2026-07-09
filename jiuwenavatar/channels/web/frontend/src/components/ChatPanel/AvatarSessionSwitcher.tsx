/**
 * AvatarSessionSwitcher — 主对话页按当前分身切换历史会话
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ChevronDown, History, Plus, Trash2 } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { useAvatarStore } from '../../stores/avatarStore';
import type { Session } from '../../types';
import { filterChatSessionsForAvatar } from '../../utils/avatarSessionStorage';
import {
  formatSessionRelativeTime,
  parseSessionDisplayLabel,
} from '../../utils/sessionDisplayLabel';
import './AvatarSessionSwitcher.css';

interface AvatarSessionSwitcherProps {
  currentSessionId: string;
  currentAvatarId: string | null;
  sessions: Session[];
  isConnected: boolean;
  isProcessing: boolean;
  onSwitchSession: (sessionId: string) => void | Promise<void>;
  onNewSession: () => void | Promise<void>;
  onRefreshSessions?: () => void | Promise<void>;
  onDeleteSession?: (sessionId: string) => void | Promise<void>;
}

export function AvatarSessionSwitcher({
  currentSessionId,
  currentAvatarId,
  sessions,
  isConnected,
  isProcessing,
  onSwitchSession,
  onNewSession,
  onRefreshSessions,
  onDeleteSession,
}: AvatarSessionSwitcherProps) {
  const { t, i18n } = useTranslation();
  const { getAvatarById } = useAvatarStore();
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  const avatarSessions = useMemo(
    () => filterChatSessionsForAvatar(sessions, currentAvatarId),
    [sessions, currentAvatarId],
  );

  const currentAvatar = currentAvatarId ? getAvatarById(currentAvatarId) : null;

  const currentLabel = useMemo(() => {
    const active = avatarSessions.find((s) => s.session_id === currentSessionId);
    if (active?.title?.trim()) {
      return active.title.trim();
    }
    if (currentSessionId && currentSessionId.startsWith('sess_')) {
      return parseSessionDisplayLabel(currentSessionId, t);
    }
    return t('chat.sessionSwitcher.unnamed');
  }, [avatarSessions, currentSessionId, t]);

  useEffect(() => {
    if (!open) return;
    void onRefreshSessions?.();
    const handlePointerDown = (event: MouseEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener('mousedown', handlePointerDown);
    return () => document.removeEventListener('mousedown', handlePointerDown);
  }, [open, onRefreshSessions]);

  const handleSelect = useCallback(
    (sessionId: string) => {
      if (sessionId === currentSessionId || isProcessing || !isConnected) {
        setOpen(false);
        return;
      }
      setOpen(false);
      void onSwitchSession(sessionId);
    },
    [currentSessionId, isConnected, isProcessing, onSwitchSession],
  );

  const handleNew = useCallback(() => {
    setOpen(false);
    void onNewSession();
  }, [onNewSession]);

  const switchDisabled = !isConnected || isProcessing;

  const handleDelete = useCallback(
    (e: React.MouseEvent, sessionId: string) => {
      e.stopPropagation();
      if (!onDeleteSession) return;
      const session = avatarSessions.find((s) => s.session_id === sessionId);
      const label = session
        ? (session.title?.trim() || parseSessionDisplayLabel(sessionId, t))
        : sessionId;
      if (!window.confirm(t('chat.sessionSwitcher.deleteConfirm', { session: label }))) {
        return;
      }
      void onDeleteSession(sessionId);
    },
    [avatarSessions, onDeleteSession, t],
  );
  const menuTitle = currentAvatar
    ? t('chat.sessionSwitcher.menuTitleNamed', { name: currentAvatar.name })
    : t('chat.sessionSwitcher.menuTitleDefault');

  return (
    <div className="chat-session-switcher" ref={rootRef}>
      <button
        type="button"
        className="chat-session-switcher__trigger"
        aria-haspopup="listbox"
        aria-expanded={open}
        title={switchDisabled && isProcessing ? t('chat.sessionSwitcher.disabledProcessing') : menuTitle}
        onClick={() => setOpen((prev) => !prev)}
      >
        <span className="chat-session-switcher__icon">
          <History size={16} strokeWidth={2} />
        </span>
        <span className="chat-session-switcher__text">
          <span className="chat-session-switcher__label">{t('chat.sessionSwitcher.label')}</span>
          <span className="chat-session-switcher__value">{currentLabel}</span>
        </span>
        <ChevronDown
          className={`chat-session-switcher__chevron${open ? ' chat-session-switcher__chevron--open' : ''}`}
          size={16}
        />
      </button>

      {open && (
        <div className="chat-session-switcher__menu" role="listbox" aria-label={menuTitle}>
          <div className="chat-session-switcher__menu-head">
            <span className="chat-session-switcher__menu-title">{menuTitle}</span>
            <span className="chat-session-switcher__menu-count">
              {t('chat.sessionSwitcher.count', { count: avatarSessions.length })}
            </span>
          </div>

          <div className="chat-session-switcher__list">
            {avatarSessions.length === 0 ? (
              <div className="chat-session-switcher__empty">{t('chat.sessionSwitcher.empty')}</div>
            ) : (
              avatarSessions.map((session) => {
                const isActive = session.session_id === currentSessionId;
                const title =
                  session.title?.trim() || parseSessionDisplayLabel(session.session_id, t);
                const timeText = formatSessionRelativeTime(session, i18n.language, t);
                return (
                  <div
                    key={session.session_id}
                    className={`chat-session-switcher__option${isActive ? ' chat-session-switcher__option--active' : ''}`}
                  >
                    <button
                      type="button"
                      role="option"
                      aria-selected={isActive}
                      disabled={switchDisabled && !isActive}
                      className="chat-session-switcher__option-btn"
                      onClick={() => handleSelect(session.session_id)}
                      title={title}
                    >
                      <span className="chat-session-switcher__option-main">
                        <span className="chat-session-switcher__option-title">{title}</span>
                        {timeText ? (
                          <span className="chat-session-switcher__option-meta">{timeText}</span>
                        ) : null}
                      </span>
                      {isActive ? (
                        <span className="chat-session-switcher__option-badge">
                          {t('chat.sessionSwitcher.current')}
                        </span>
                      ) : null}
                    </button>
                    {onDeleteSession && !isActive ? (
                      <button
                        type="button"
                        className="chat-session-switcher__option-delete"
                        title={t('chat.sessionSwitcher.delete')}
                        onClick={(e) => handleDelete(e, session.session_id)}
                      >
                        <Trash2 size={14} />
                      </button>
                    ) : null}
                  </div>
                );
              })
            )}
          </div>

          <button
            type="button"
            className="chat-session-switcher__new"
            onClick={handleNew}
          >
            <Plus size={14} strokeWidth={2} />
            {t('chat.newSession')}
          </button>
        </div>
      )}
    </div>
  );
}
