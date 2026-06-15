import { useTranslation } from 'react-i18next';
import { Modal } from './Modal';

interface ConfirmDialogProps {
  open: boolean;
  title?: string;
  message: string;
  confirmText?: string;
  cancelText?: string;
  danger?: boolean;
  onConfirm: () => void;
  onClose: () => void;
}

export function ConfirmDialog({
  open,
  title,
  message,
  confirmText,
  cancelText,
  danger,
  onConfirm,
  onClose,
}: ConfirmDialogProps) {
  const { t } = useTranslation();
  return (
    <Modal
      open={open}
      title={title ?? t('common.confirm')}
      onClose={onClose}
      footer={
        <>
          <button className="btn ghost" onClick={onClose}>
            {cancelText ?? t('common.cancel')}
          </button>
          <button
            className={danger ? 'btn danger' : 'btn primary'}
            onClick={() => {
              onConfirm();
              onClose();
            }}
          >
            {confirmText ?? t('common.confirm')}
          </button>
        </>
      }
    >
      <div className="text-sm text-text whitespace-pre-wrap">{message}</div>
    </Modal>
  );
}
