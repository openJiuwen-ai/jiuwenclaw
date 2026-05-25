import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Modal } from '../../components/Modal';
import { InstanceApi, ApiError } from '../../services/api';
import { toast } from '../../stores/uiStore';

interface Props {
  open: boolean;
  onClose: () => void;
  onProvisioned: () => void;
}

export function ProvisionLocalModal({ open, onClose, onProvisioned }: Props) {
  const { t } = useTranslation();
  const [name, setName] = useState('local-instance');
  const [creatorId, setCreatorId] = useState('system');
  const [description, setDescription] = useState('');
  const [saving, setSaving] = useState(false);

  const submit = async () => {
    setSaving(true);
    try {
      await InstanceApi.provisionLocal({
        jiuwenclaw_name: name.trim() || 'local-instance',
        creator_id: creatorId.trim() || 'system',
        description: description.trim() || undefined,
      });
      toast('success', t('success.created'));
      onProvisioned();
    } catch (e) {
      toast('danger', t('errors.saveFailed', { detail: e instanceof ApiError ? e.detail : (e as Error).message }));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal
      open={open}
      title={t('instanceForm.provisionLocalTitle')}
      onClose={onClose}
      footer={
        <>
          <button className="btn ghost" onClick={onClose}>
            {t('common.cancel')}
          </button>
          <button className="btn primary" onClick={submit} disabled={saving}>
            {saving ? t('common.loading') : t('common.submit')}
          </button>
        </>
      }
    >
      <div className="text-xs text-muted mb-3">{t('instanceForm.provisionLocalDesc')}</div>
      <div className="grid grid-cols-1 gap-3">
        <div>
          <label className="label">{t('instanceForm.name')}</label>
          <input className="input" value={name} onChange={(e) => setName(e.target.value)} />
        </div>
        <div>
          <label className="label">{t('instanceForm.creatorId')}</label>
          <input className="input" value={creatorId} onChange={(e) => setCreatorId(e.target.value)} />
        </div>
        <div>
          <label className="label">{t('instanceForm.description')}</label>
          <input className="input" value={description} onChange={(e) => setDescription(e.target.value)} />
        </div>
      </div>
    </Modal>
  );
}
