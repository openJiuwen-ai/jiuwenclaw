import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useAsync } from '../../hooks/useAsync';
import { useRouter } from '../../router';
import { InstanceApi, ApiError } from '../../services/api';
import { StatusBadge } from '../../components/StatusBadge';
import { Modal } from '../../components/Modal';
import { JsonField, tryParseJson, useInvalidJsonChecker } from '../../components/JsonField';
import { safeStringify } from '../../utils/format';
import { toast } from '../../stores/uiStore';
import { InstancePoliciesPanel } from './instancePoliciesPanel/InstancePoliciesPanel';
import { InstanceConfigPanel } from './instanceConfigPanel/InstanceConfigPanel';
import { InstanceDetailPanel } from './instanceDetailPanel/instanceDetailPanel';

export type InstancePageTab = 'detail' | 'policies' | 'config';

interface Props {
  instanceId: string;
  tab?: InstancePageTab;
}

export function InstanceDetailPage({ instanceId, tab = 'detail' }: Props) {
  const { t } = useTranslation();
  const { navigate } = useRouter();
  const instance = useAsync(() => InstanceApi.get(instanceId), [instanceId]);

  const [editOpen, setEditOpen] = useState(false);
  const [editText, setEditText] = useState('');
  const checkJson = useInvalidJsonChecker();

  const mainTabs: { key: InstancePageTab; label: string; href: string }[] = [
    { key: 'detail', label: t('instanceDetail.tabs.detail'), href: `/instances/${instanceId}` },
    { key: 'policies', label: t('instanceDetail.tabs.policies'), href: `/instances/${instanceId}/policies` },
    { key: 'config', label: t('instanceDetail.tabs.config'), href: `/instances/${instanceId}/config` },
  ];

  const handleOpenEdit = () => {
    setEditText(safeStringify(instance.data?.data ?? {}, 2));
    setEditOpen(true);
  };

  const submitData = async () => {
    const err = checkJson(editText);
    if (err) {
      toast('danger', err);
      return;
    }
    try {
      await InstanceApi.update(instanceId, { data: tryParseJson(editText, {}) });
      toast('success', t('success.saved'));
      setEditOpen(false);
      void instance.reload();
    } catch (e) {
      toast('danger', t('errors.saveFailed', { detail: e instanceof ApiError ? e.detail : (e as Error).message }));
    }
  };

  return (
    <div className="flex flex-col gap-4">
      <div className="page-header">
        <div className="flex items-center gap-3 flex-1 min-w-0">
          <button className="btn ghost sm" onClick={() => navigate('/instances')}>
            ← {t('instanceDetail.back')}
          </button>
          <div className="min-w-0">
            <div className="page-title truncate">{instance.data?.jiuwenclaw_name ?? '…'}</div>
            <div className="text-[11px] text-muted mono truncate">{instanceId}</div>
          </div>
          {instance.data?.status && <StatusBadge status={instance.data.status} />}
        </div>

        <div className="tabs-bar shrink-0 mx-3">
          {mainTabs.map((it) => (
            <button
              key={it.key}
              onClick={() => navigate(it.href)}
              className={`tab ${tab === it.key ? 'active' : ''}`}
            >
              {it.label}
            </button>
          ))}
        </div>

        <div className="flex-1 min-w-0" />
      </div>

      {tab === 'detail' && (
        <InstanceDetailPanel
          instance={instance}
          onOpenEdit={handleOpenEdit}
          onRefresh={() => void instance.reload()}
        />
      )}
      {tab === 'policies' && <InstancePoliciesPanel instanceId={instanceId} />}
      {tab === 'config' && <InstanceConfigPanel instanceId={instanceId} />}

      <Modal
        open={editOpen}
        title={t('instanceDetail.editData')}
        onClose={() => setEditOpen(false)}
        size="lg"
        footer={
          <>
            <button className="btn ghost" onClick={() => setEditOpen(false)}>
              {t('common.cancel')}
            </button>
            <button className="btn primary" onClick={submitData}>
              {t('common.save')}
            </button>
          </>
        }
      >
        <JsonField label="instance_info.data" value={editText} onChange={setEditText} rows={14} />
      </Modal>
    </div>
  );
}
