/**
 * 分身创建/编辑弹窗 — 统一布局（顶栏 + 可滚动内容 + 底栏操作）
 */

import { ReactNode } from 'react';
import { X } from 'lucide-react';
import { PersonaIcon } from './PersonaIcon';

interface AvatarFormModalProps {
  title: string;
  subtitle?: string;
  personaIcon?: string;
  onClose: () => void;
  children: ReactNode;
  footer: ReactNode;
  error?: string | null;
  disableClose?: boolean;
}

export function AvatarFormModal({
  title,
  subtitle,
  personaIcon,
  onClose,
  children,
  footer,
  error,
  disableClose = false,
}: AvatarFormModalProps) {
  return (
    <div className="avatar-platform-modal" role="dialog" aria-modal="true">
      <div className="avatar-platform-modal__backdrop" onClick={disableClose ? undefined : onClose} />
      <div className="avatar-platform-modal__panel avatar-platform-modal__panel--form">
        <header className="avatar-form-modal__header">
          <div className="avatar-form-modal__header-main">
            {personaIcon && <PersonaIcon icon={personaIcon} size="sm" />}
            <div className="avatar-form-modal__header-text">
              <h3 className="avatar-form-modal__title">{title}</h3>
              {subtitle && <p className="avatar-form-modal__subtitle">{subtitle}</p>}
            </div>
          </div>
          <button
            type="button"
            className="avatar-form-modal__close"
            onClick={onClose}
            disabled={disableClose}
            aria-label="Close"
          >
            <X size={18} strokeWidth={2} />
          </button>
        </header>

        <div className="avatar-form-modal__body">{children}</div>

        {error && <p className="avatar-platform-modal__error avatar-form-modal__error">{error}</p>}

        <footer className="avatar-form-modal__footer">{footer}</footer>
      </div>
    </div>
  );
}
