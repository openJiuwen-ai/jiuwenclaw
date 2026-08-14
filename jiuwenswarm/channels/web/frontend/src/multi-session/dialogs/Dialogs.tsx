import { type ReactNode } from 'react';
import { X } from 'lucide-react';
import { Trans, useTranslation } from 'react-i18next';
import './dialogs.css';

interface DialogShellProps { title: string; children: ReactNode; onCancel: () => void; testId?: string }
interface DeleteDialogProps {
  title: string;
  dialogTitle?: string;
  descriptionKey?: string;
  descriptionValues?: Record<string, string>;
  deleting: boolean;
  error: string | null;
  onCancel: () => void;
  onDelete: () => void;
  testId?: string;
}

function DialogShell({ title, children, onCancel, testId }: DialogShellProps) {
  const { t } = useTranslation();
  return (
    <div className="conversation-dialog" role="dialog" aria-modal="true" aria-label={title} data-testid={testId ?? 'multi-session-dialog'} data-variant="delete">
      <button type="button" className="conversation-dialog__backdrop" onClick={onCancel} aria-label={t('common.cancel')} data-testid={testId ? `${testId}-backdrop` : 'multi-session-dialog-backdrop'} />
      <div className="conversation-dialog__panel" data-testid={testId ? `${testId}-panel` : 'multi-session-dialog-panel'}>
        <button type="button" className="conversation-dialog__close" onClick={onCancel} aria-label={t('common.close')} data-testid={testId ? `${testId}-close` : 'multi-session-dialog-close'}><X size={16} /></button>
        <h2 data-testid={testId ? `${testId}-title` : 'multi-session-dialog-title'}>{title}</h2>
        {children}
      </div>
    </div>
  );
}

function DialogActions({ busy, danger = false, confirmLabel, onCancel, onConfirm, testId }: { busy?: boolean; danger?: boolean; confirmLabel: string; onCancel: () => void; onConfirm: () => void; testId?: string }) {
  const { t } = useTranslation();
  return (
    <div className="conversation-dialog__actions" data-testid={testId ? `${testId}-actions` : 'multi-session-dialog-actions'}>
      <button type="button" onClick={onCancel} data-testid={testId ? `${testId}-cancel` : 'multi-session-dialog-cancel'}>{t('common.cancel')}</button>
      <button type="button" className={danger ? 'is-danger' : 'is-primary'} disabled={busy} onClick={onConfirm} data-testid={testId ? `${testId}-confirm` : 'multi-session-dialog-confirm'} data-variant={danger ? 'danger' : 'primary'}>
        {confirmLabel}
      </button>
    </div>
  );
}

export function DeleteDialog({
  title,
  dialogTitle,
  descriptionKey = 'multiSession.deleteDialog.description',
  descriptionValues = { title },
  deleting,
  error,
  onCancel,
  onDelete,
  testId,
}: DeleteDialogProps) {
  const { t } = useTranslation();
  return (
    <DialogShell title={dialogTitle ?? t('multiSession.deleteDialog.title')} onCancel={onCancel} testId={testId}>
      <p data-testid={testId ? `${testId}-description` : 'multi-session-dialog-description'}>
        <Trans
          i18nKey={descriptionKey}
          values={descriptionValues}
          components={{
            name: <span className="conversation-dialog__subject" title={title} />,
          }}
        />
      </p>
      {error && <div className="conversation-dialog__error" data-testid={testId ? `${testId}-error` : 'multi-session-dialog-error'}>{error}</div>}
      <DialogActions busy={deleting} danger confirmLabel={t('multiSession.delete')} onCancel={onCancel} onConfirm={onDelete} testId={testId} />
    </DialogShell>
  );
}
