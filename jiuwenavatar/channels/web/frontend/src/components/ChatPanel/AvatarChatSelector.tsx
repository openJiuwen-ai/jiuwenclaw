/**
 * AvatarChatSelector — 主对话页分身选择器
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ChevronDown } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { useAvatarStore } from '../../stores/avatarStore';
import { webRequest } from '../../services/webClient';
import { PersonaIcon } from '../AvatarPlatform/PersonaIcon';
import './AvatarChatSelector.css';

interface AvatarChatSelectorProps {
  onNavigateToAvatars?: () => void;
  /** 切换分身回调；上层恢复该分身最近会话或新建 */
  onAvatarChange?: (avatarId: string | null) => void;
}

export function AvatarChatSelector({ onNavigateToAvatars, onAvatarChange }: AvatarChatSelectorProps) {
  const { t } = useTranslation();
  const {
    avatars,
    personas,
    currentAvatarId,
    setCurrentAvatarId,
    fetchAvatars,
    fetchPersonas,
    getPersonaById,
    getAvatarById,
  } = useAvatarStore();
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  const sendRequest = useCallback(
    (method: string, params?: Record<string, unknown>) => webRequest(method, params),
    [],
  );

  const fetchedAvatarsRef = useRef(false);
  const fetchedPersonasRef = useRef(false);

  useEffect(() => {
    if (avatars.length === 0 && !fetchedAvatarsRef.current) {
      fetchedAvatarsRef.current = true;
      void fetchAvatars(sendRequest);
    }
    if (personas.length === 0 && !fetchedPersonasRef.current) {
      fetchedPersonasRef.current = true;
      void fetchPersonas(sendRequest);
    }
  }, [avatars.length, personas.length, fetchAvatars, fetchPersonas, sendRequest]);

  useEffect(() => {
    if (!currentAvatarId || avatars.length === 0) {
      return;
    }
    if (!avatars.some((a) => a.id === currentAvatarId)) {
      setCurrentAvatarId(null);
    }
  }, [avatars, currentAvatarId, setCurrentAvatarId]);

  useEffect(() => {
    if (!open) {
      return;
    }
    const handlePointerDown = (event: MouseEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener('mousedown', handlePointerDown);
    return () => document.removeEventListener('mousedown', handlePointerDown);
  }, [open]);

  const currentAvatar = currentAvatarId ? getAvatarById(currentAvatarId) : null;
  const currentPersona = currentAvatar ? getPersonaById(currentAvatar.persona_id) : null;

  const label = useMemo(() => {
    if (currentAvatar) {
      return currentAvatar.name;
    }
    return t('avatar.chatSelector.default', '默认助手');
  }, [currentAvatar, t]);

  const handleSelect = (avatarId: string | null) => {
    if (onAvatarChange) {
      onAvatarChange(avatarId);
    } else {
      setCurrentAvatarId(avatarId);
    }
    setOpen(false);
  };

  return (
    <div className="chat-avatar-selector" ref={rootRef}>
      <button
        type="button"
        className="chat-avatar-selector__trigger"
        aria-haspopup="listbox"
        aria-expanded={open}
        onClick={() => setOpen((prev) => !prev)}
      >
        <span className="chat-avatar-selector__icon">
          <PersonaIcon icon={currentPersona?.icon || 'avatar'} size="sm" />
        </span>
        <span className="chat-avatar-selector__text">
          <span className="chat-avatar-selector__label">{t('avatar.chatSelector.label', '对话分身')}</span>
          <span className="chat-avatar-selector__value">{label}</span>
        </span>
        <ChevronDown className={`chat-avatar-selector__chevron${open ? ' chat-avatar-selector__chevron--open' : ''}`} size={16} />
      </button>

      {open && (
        <div className="chat-avatar-selector__menu" role="listbox">
          <button
            type="button"
            role="option"
            aria-selected={!currentAvatarId}
            className={`chat-avatar-selector__option${!currentAvatarId ? ' chat-avatar-selector__option--active' : ''}`}
            onClick={() => handleSelect(null)}
          >
            <span className="chat-avatar-selector__option-icon">
              <PersonaIcon icon="avatar" size="sm" />
            </span>
            <span className="chat-avatar-selector__option-main">
              <span className="chat-avatar-selector__option-title">{t('avatar.chatSelector.default', '默认助手')}</span>
              <span className="chat-avatar-selector__option-desc">{t('avatar.chatSelector.defaultHint', '使用平台默认能力对话')}</span>
            </span>
          </button>

          {avatars.length === 0 ? (
            <div className="chat-avatar-selector__empty">
              <p>{t('avatar.chatSelector.empty', '暂无分身')}</p>
              {onNavigateToAvatars && (
                <button type="button" className="chat-avatar-selector__link" onClick={() => { setOpen(false); onNavigateToAvatars(); }}>
                  {t('avatar.chatSelector.goCreate', '去创建分身')}
                </button>
              )}
            </div>
          ) : (
            avatars.map((avatar) => {
              const persona = getPersonaById(avatar.persona_id);
              const active = avatar.id === currentAvatarId;
              return (
                <button
                  key={avatar.id}
                  type="button"
                  role="option"
                  aria-selected={active}
                  className={`chat-avatar-selector__option${active ? ' chat-avatar-selector__option--active' : ''}`}
                  onClick={() => handleSelect(avatar.id)}
                >
                  <span className="chat-avatar-selector__option-icon">
                    <PersonaIcon icon={persona?.icon || 'avatar'} size="sm" />
                  </span>
                  <span className="chat-avatar-selector__option-main">
                    <span className="chat-avatar-selector__option-title">{avatar.name}</span>
                    <span className="chat-avatar-selector__option-desc">{persona?.display_name || avatar.persona_id}</span>
                  </span>
                </button>
              );
            })
          )}
        </div>
      )}
    </div>
  );
}
