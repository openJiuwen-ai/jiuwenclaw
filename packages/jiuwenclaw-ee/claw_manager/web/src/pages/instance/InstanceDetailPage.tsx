import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useAsync } from '../../hooks/useAsync';
import { useRouter } from '../../router';
import { InstanceApi, ApiError } from '../../services/api';
import { StatusBadge } from '../../components/StatusBadge';
import { Modal } from '../../components/Modal';
import { JsonField, tryParseJson, useInvalidJsonChecker } from '../../components/JsonField';
import { formatTime, relativeTime, safeStringify } from '../../utils/format';
import { toast } from '../../stores/uiStore';
import { InstancePoliciesPanel } from './policies/InstancePoliciesPanel';
import { InstanceConfigPanel } from './instanceConfig/InstanceConfigPanel';
import type { InstanceDetail } from '../../types';

export type InstancePageTab = 'detail' | 'policies' | 'config';

interface Props {
  instanceId: string;
  tab?: InstancePageTab;
}

function InstanceDetailContent({
  instance,
  onOpenEdit,
}: {
  instance: { data?: InstanceDetail | null };
  onOpenEdit: () => void;
}) {
  const { t } = useTranslation();

  return (
    <div className="grid grid-cols-1 xl:grid-cols-3 gap-4">
      <div className="card">
        <div className="card-header">
          <div className="card-title">K8S</div>
        </div>
        <div className="text-xs grid grid-cols-[6em_1fr] gap-y-2 gap-x-2 mono">
          <div className="text-muted">master</div>
          <div className="truncate" title={instance.data?.k8s_master_host ?? ''}>
            {instance.data?.k8s_master_host ?? '-'}
          </div>
          <div className="text-muted">auth_type</div>
          <div>{instance.data?.k8s_auth_type ?? '-'}</div>
          <div className="text-muted">namespace</div>
          <div>{instance.data?.k8s_namespace ?? '-'}</div>
        </div>
      </div>

      <div className="card">
        <div className="card-header">
          <div className="card-title">Meta</div>
        </div>
        <div className="text-xs grid grid-cols-[6em_1fr] gap-y-2 gap-x-2">
          <div className="text-muted">group</div>
          <div className="mono">{instance.data?.group_id ?? '-'}</div>
          <div className="text-muted">space</div>
          <div className="mono">{instance.data?.space_id ?? '-'}</div>
          <div className="text-muted">created</div>
          <div className="mono">{formatTime(instance.data?.created_at)}</div>
          <div className="text-muted">{t('topology.lastHeartbeat')}</div>
          <div className="mono">{relativeTime(instance.data?.last_heartbeat)}</div>
          <div className="text-muted">description</div>
          <div>{instance.data?.description ?? '-'}</div>
        </div>
      </div>

      <div className="card">
        <div className="card-header">
          <div className="card-title">{t('instanceDetail.extraData')}</div>
          <button className="btn ghost sm" onClick={onOpenEdit}>
            {t('instanceDetail.editData')}
          </button>
        </div>
        <pre className="text-[11px] mono whitespace-pre-wrap break-words text-text max-h-48 overflow-auto">
          {safeStringify(instance.data?.data ?? {}, 2) || '-'}
        </pre>
      </div>
    </div>
  );
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

        <div className="flex items-center gap-2 flex-1 justify-end min-w-0">
          <button className="btn sm" onClick={() => void instance.reload()}>
            {t('common.refresh')}
          </button>
        </div>
      </div>

      {tab === 'detail' && (
        <InstanceDetailContent instance={instance} onOpenEdit={handleOpenEdit} />
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
