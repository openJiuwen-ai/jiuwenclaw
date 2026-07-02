import { type ReactNode } from 'react';
import { X } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import './dialogs.css';

interface DialogShellProps { title: string; children: ReactNode; onCancel: () => void }
interface DeleteDialogProps { title: string; deleting: boolean; error: string | null; onCancel: () => void; onDelete: () => void }

function DialogShell({ title, children, onCancel }: DialogShellProps) {
  const { t } = useTranslation();
  return (
    <div className="conversation-dialog" role="dialog" aria-modal="true" aria-label={title}>
      <button type="button" className="conversation-dialog__backdrop" onClick={onCancel} aria-label={t('common.cancel')} />
      <div className="conversation-dialog__panel">
        <button type="button" className="conversation-dialog__close" onClick={onCancel} aria-label={t('common.close')}><X size={16} /></button>
        <h2>{title}</h2>
        {children}
      </div>
    </div>
  );
}

function DialogActions({ busy, danger = false, confirmLabel, onCancel, onConfirm }: { busy?: boolean; danger?: boolean; confirmLabel: string; onCancel: () => void; onConfirm: () => void }) {
  const { t } = useTranslation();
  return (
    <div className="conversation-dialog__actions">
      <button type="button" onClick={onCancel}>{t('common.cancel')}</button>
      <button type="button" className={danger ? 'is-danger' : 'is-primary'} disabled={busy} onClick={onConfirm}>
        {confirmLabel}
      </button>
    </div>
  );
}

export function DeleteDialog({ title, deleting, error, onCancel, onDelete }: DeleteDialogProps) {
  const { t } = useTranslation();
  return (
    <DialogShell title={t('multiSession.deleteDialog.title')} onCancel={onCancel}>
      <p>{t('multiSession.deleteDialog.description', { title })}</p>
      {error && <div className="conversation-dialog__error">{error}</div>}
      <DialogActions busy={deleting} danger confirmLabel={t('multiSession.delete')} onCancel={onCancel} onConfirm={onDelete} />
    </DialogShell>
  );
}
